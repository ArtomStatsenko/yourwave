"""`radio ui`: a small local web page to control the radio. Listens on 127.0.0.1 only; every API call needs
a per-run token and a local Host header, so another site open in the browser cannot drive the radio."""
import html
import json
import queue
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import chatgpt, config, editor, openrouter, plugins, settings, tts, ytmusic
from .timeline import timeline

PAGE = Path(__file__).with_name("ui.html")
CALLBACK = Path(__file__).with_name("callback.html")  # the page the browser lands on after OpenRouter sign-in
VOICE_SAMPLE = "Добрый вечер! Вы слушаете Персональное радио. Сейчас — коротко о новостях, а потом снова музыка."
COMMANDS = {"pause", "resume", "next", "skip-news", "stop"}


class Stream:
    """Fans the sink's mp3-over-UDP out to every /stream client and keeps the listener count in a heartbeat file
    the daemon reads (auto-pause). mp3 decoders resync on frame headers, so a client may join mid-packet."""
    def __init__(self, udp_port: int):
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.sock.bind(("127.0.0.1", udp_port))
        threading.Thread(target=self._recv, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _recv(self):
        while True:
            data = self.sock.recv(65536)
            with self.lock:
                for q in list(self.clients):
                    try:
                        q.put_nowait(data)
                    except queue.Full:  # a client stalled for minutes: drop it, its page reconnects
                        self.clients.discard(q)

    def _heartbeat(self):
        while True:
            self.beat()
            time.sleep(5)

    def beat(self):
        f = config.RUN_DIR / "listeners.json"
        f.write_text(json.dumps({"n": len(self.clients), "t": time.time()}))

    def add(self) -> queue.Queue:
        q = queue.Queue(maxsize=4000)  # ~3 min of 192k mp3
        with self.lock:
            self.clients.add(q)
        self.beat()
        return q

    def remove(self, q):
        with self.lock:
            self.clients.discard(q)
        self.beat()


def serve(port: int) -> None:
    server = make_server(port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Yourwave UI: {url}  (Ctrl+C — закрыть интерфейс; радио продолжит играть)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def make_server(port: int) -> ThreadingHTTPServer:
    """The page server, not yet serving; server.token is the per-run API token the page gets."""
    from .__main__ import send, start_daemon
    token = secrets.token_urlsafe(16)
    cfg = config.load()
    stream = Stream(cfg["output"]["stream_port"])
    ytmusic_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Radio-Token", ""), token)

        def _local_host(self) -> bool:
            # Blocks DNS rebinding: a foreign hostname resolving to 127.0.0.1 must not get the page or the API.
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self._local_host():
                return self._json({"error": "forbidden"}, 403)
            if self.path in ("/", "/index.html"):
                body = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", token).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.split("?")[0] in ("/openrouter/callback", "/auth/callback"):
                q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
                service = "OpenRouter" if self.path.startswith("/openrouter/") else "ChatGPT"
                try:
                    if q.get("error"):
                        raise ValueError(f"{service} said: {q['error'][:100]}")
                    if service == "OpenRouter":
                        openrouter.finish(q.get("state", ""), q.get("code", ""))
                    else:
                        chatgpt.finish(q.get("state", ""), q.get("code", ""), q.get("client_id", ""))
                    kind, note, detail = "", f"Signed in to {service}.", "You can close this tab, or go back to the radio."
                except (ValueError, OSError) as e:
                    kind, note = "fail", f"{service} sign-in failed."
                    detail = str(e) if isinstance(e, ValueError) else f"{service} did not answer; try again."
                body = (CALLBACK.read_text(encoding="utf-8").replace("__CLASS__", kind)
                        .replace("__SERVICE__", service.upper())
                        .replace("__NOTE__", html.escape(note)).replace("__DETAIL__", html.escape(detail))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.split("?")[0] == "/voice-sample.wav":
                f = config.RUN_DIR / "voice-sample.wav"
                if not f.exists():
                    return self._json({"error": "not found"}, 404)
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.split("?")[0] == "/stream":
                self._stream()
            elif self.path == "/api/status":
                if not self._authorized():
                    return self._json({"error": "forbidden"}, 403)
                resp = send("status", timeout=3)
                status = resp["status"] if resp and resp.get("ok") else {"state": "STOPPED"}
                self._json({"status": status, "timeline": timeline(), "plugins": plugins.listing(cfg),
                            "settings": settings.listing(cfg), "ytmusic": ytmusic.status(),
                            "audio_preset": config.audio_presets()})
            else:
                self._json({"error": "not found"}, 404)

        def _stream(self):
            # Live: no length, no ranges. Same origin as the page, so Web Audio may read it (the spectrum).
            q = stream.add()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:
                    try:
                        self.wfile.write(q.get(timeout=1))
                    except queue.Empty:
                        if q not in stream.clients:
                            return
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                stream.remove(q)

        def do_POST(self):
            nonlocal cfg
            if self.path != "/api/cmd" or not self._local_host() or not self._authorized():
                return self._json({"error": "forbidden"}, 403)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 65536:
                    return self._json({"error": "request too large"}, 413)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                cmd = body.get("cmd")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            if cmd == "ytmusic_connect":
                try:
                    with ytmusic_lock:
                        result = ytmusic.connect(str(body.get("cookie", "")),
                                                 str(body.get("playlist") or ytmusic.DEFAULT_PLAYLIST),
                                                 browser=str(body.get("browser", "")))
                        plugins.set_plugin("ytmusic", True)
                except ValueError as e:  # our own messages only: never yt-dlp output (cookies, account details)
                    return self._json({"ok": False, "error": str(e)}, 400)
                except OSError:
                    return self._json({"ok": False, "error": "Could not connect YouTube Music. Check the playlist; YouTube may be rate-limiting requests."}, 400)
                resp = {"ok": True, "ytmusic": result}
            elif cmd == "openrouter_login":
                resp = {"ok": True, "url": openrouter.login_url(f"http://127.0.0.1:{port}/openrouter/callback")}
            elif cmd == "chatgpt_login":  # OpenAI accepts only a 127.0.0.1 callback; the port may vary
                resp = {"ok": True, "url": chatgpt.login_url(f"http://127.0.0.1:{port}/auth/callback")}
            elif cmd == "voice_sample":  # one host line in the selected voice, played by the page
                live = config.load()
                settings.apply(live, settings.base(live))
                try:
                    voice = tts.make_voice(live)
                    if not voice.health():
                        raise OSError
                    voice.synthesize(VOICE_SAMPLE, config.RUN_DIR / "voice-sample.wav")
                except OSError:
                    return self._json({"ok": False, "error": "the voice engine did not answer"}, 400)
                except (ValueError, RuntimeError) as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                resp = {"ok": True, "url": f"/voice-sample.wav?{time.time_ns()}"}
            elif cmd == "llm_key":  # the key is stored, never echoed back
                try:
                    editor.set_key(str(body.get("provider", "")), str(body.get("key", "")))
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                resp = {"ok": True, "settings": settings.listing(cfg)}
            elif cmd == "chatgpt_logout":
                chatgpt.sign_out()
                resp = {"ok": True, "settings": settings.listing(cfg)}
            elif cmd == "openrouter_logout":
                openrouter.sign_out()
                resp = {"ok": True, "settings": settings.listing(cfg)}
            elif cmd == "play":
                resp = start_daemon()
            elif cmd == "plugin":  # a file switch; a running radio also takes back what the plugin already queued
                name, on = str(body.get("name", "")), bool(body.get("on"))
                if name not in plugins.PLUGINS:
                    return self._json({"ok": False, "error": f"unknown plugin {name}"}, 400)
                resp = send("plugin", timeout=10, name=name, on=on)
                if resp is None:  # the radio is off: the file alone
                    plugins.set_plugin(name, on)
                    resp = {"ok": True}
            elif cmd == "audio_preset":
                try:
                    config.select_audio_preset(str(body.get("name", "")))
                    cfg = config.load()
                except (ValueError, OSError) as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                resp = {"ok": True, "audio_preset": config.audio_presets()}
            elif cmd in ("setting", "settings_reset"):  # a file too: the daemon reads it before every decision
                try:
                    if cmd == "setting":
                        settings.set_setting(str(body.get("key", "")), body.get("value"))
                    else:
                        settings.reset(str(body["key"]) if body.get("key") else None)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                resp = {"ok": True, "settings": settings.listing(cfg)}
            elif cmd == "reroll-next":
                resp = send(cmd, timeout=45, ref=str(body.get("ref", "")))
            elif cmd == "direction":
                resp = send(cmd, timeout=10, **{k: v for k, v in body.items() if k != "cmd"})
            elif cmd in ("replay", "unqueue"):
                resp = send(cmd, timeout=10, kind=str(body.get("kind", "")), ref=str(body.get("ref", "")))
            elif cmd in COMMANDS:
                resp = send(cmd, timeout=45 if cmd == 'next' else 10)
            else:
                return self._json({"error": "unknown command"}, 400)
            self._json(resp or {"ok": False, "error": "the radio is not answering"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = server.server_port  # 0 in tests: the Host check must use the port that was really taken
    server.token = token
    return server
