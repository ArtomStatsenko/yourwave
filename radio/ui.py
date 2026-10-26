"""Radio control UI on loopback, optionally shared through private Tailscale Serve.
Every API call needs a per-run token and a trusted Host header."""
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

from . import chatgpt, config, openrouter
from . import hooks, store, control

PAGE = Path(__file__).with_name("ui.html")
CALLBACK = Path(__file__).with_name("callback.html")  # the page the browser lands on after OpenRouter sign-in


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

    def serve(self, handler):
        # Live MP3 has no length or ranges; a listener may join between frames.
        q = self.add()
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "audio/mpeg")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            while True:
                try:
                    handler.wfile.write(q.get(timeout=1))
                except queue.Empty:
                    if q not in self.clients:
                        return
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.remove(q)


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

        def _trusted_host(self) -> bool:
            # Only the phone port accepts the exact configured Tailscale hostname.
            host = self.headers.get("Host", "")
            remote = getattr(self.server, "control_host", "")
            return host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}") or bool(remote and host == remote)

        def do_GET(self):
            if not self._trusted_host():
                return self._json({"error": "forbidden"}, 403)
            if self.path in ("/", "/index.html"):
                body = (PAGE.read_text(encoding="utf-8").replace("__TOKEN__", token)
                        .replace("__LOCAL_CONTROL__", str(self.server is server).lower()).encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
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
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
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
            elif self.path.split('?')[0] == '/hook-preview.wav':
                if not self._authorized():
                    return self._json({'error':'forbidden'},403)
                db = store.connect()
                try:
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    row = db.execute('SELECT * FROM track_hooks WHERE id=?', (int(query.get('id',['0'])[0]),)).fetchone()
                    path = Path(row['clip']).resolve() if row and row['clip'] else None
                    if not path or not path.is_relative_to((config.DATA_DIR / 'cache/hooks').resolve()) or hooks.signature(row['source_path']) != row['source_sig']:
                        return self._json({'error':'unavailable'},404)
                    body = path.read_bytes()
                except (OSError,ValueError):
                    return self._json({'error':'unavailable'},404)
                finally:
                    db.close()
                self.send_response(200)
                self.send_header('Content-Type','audio/wav')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/status":
                if not self._authorized():
                    return self._json({"error": "forbidden"}, 403)
                result = control.read('snapshot', sender=send)
                self._json(result, 200 if result.get('ok') else 503)
            elif self.path == '/api/capabilities':
                if not self._authorized():
                    return self._json({'error': 'forbidden'}, 403)
                self._json(control.capabilities())
            else:
                self._json({"error": "not found"}, 404)

        def _stream(self):
            stream.serve(self)

        def do_POST(self):
            nonlocal cfg
            if self.path != "/api/cmd" or not self._trusted_host() or not self._authorized():
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
            if cmd in ("play", "stop") and self.server is not server:
                return self._json({"ok": False, "error": "Start or stop the station on your Mac."}, 403)
            resp, code = control.execute(body, sender=send, starter=start_daemon, port=port)
            if cmd == 'audio_preset' and resp.get('ok'):
                cfg = config.load()
            self._json(resp, code)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = server.server_port  # 0 in tests: the Host check must use the port that was really taken
    server.token = token
    if cfg["output"].get("listen_port", 0):
        try:
            server.listener = ThreadingHTTPServer(("127.0.0.1", cfg["output"]["listen_port"]), Handler)
        except OSError:
            server.server_close()
            raise
        server.listener.control_host = cfg["output"].get("control_host", "")
        threading.Thread(target=server.listener.serve_forever, daemon=True).start()
    return server
