"""Local pass-through to Ollama for Horizon that turns the model's "thinking" off.
Horizon talks to Ollama's OpenAI-compatible API and has no setting for it; with thinking on, qwen3.5 spends its
token budget reasoning and often returns an empty answer. Listens on 127.0.0.1 only."""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start(port: int, upstream: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _forward(self, body: bytes | None):
            if body and self.path.endswith("/chat/completions"):
                try:
                    data = json.loads(body)
                    data.setdefault("reasoning_effort", "none")
                    body = json.dumps(data).encode()
                except ValueError:
                    pass
            req = urllib.request.Request(upstream.rstrip("/") + self.path, data=body, method=self.command,
                                         headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    status, ctype, out = r.status, r.headers.get("Content-Type", "application/json"), r.read()
            except urllib.error.HTTPError as e:
                status, ctype, out = e.code, e.headers.get("Content-Type", "application/json"), e.read()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._forward(None)

        def do_POST(self):
            self._forward(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
