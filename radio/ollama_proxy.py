"""Local pass-through for Horizon to the radio's selected LLM provider (editor.target): Ollama or an OpenAI-compatible
API. Horizon is configured once, against this port; the proxy supplies the provider's address, key and model.
For Ollama it also turns the model's "thinking" off: with thinking on, qwen3.5 spends its token budget reasoning
and often returns an empty answer. Listens on 127.0.0.1 only."""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def to_responses_request(chat: dict) -> dict:
    """A chat.completions request (Horizon's: messages, max tokens, temperature, json_object) as a Responses one."""
    body = {"model": chat["model"], "input": chat.get("messages", [])}
    tokens = chat.get("max_completion_tokens") or chat.get("max_tokens")
    if tokens:
        body["max_output_tokens"] = tokens
    if "temperature" in chat:
        body["temperature"] = chat["temperature"]
    fmt = chat.get("response_format") or {}
    if fmt.get("type") == "json_object":
        body["text"] = {"format": {"type": "json_object"}}
    elif fmt.get("type") == "json_schema":
        body["text"] = {"format": {"type": "json_schema", **fmt.get("json_schema", {})}}
    return body


def to_chat_response(resp: dict) -> dict:
    from .editor import output_text
    usage = resp.get("usage") or {}
    return {"id": resp.get("id", ""), "object": "chat.completion", "created": resp.get("created_at", 0),
            "model": resp.get("model", ""),
            "choices": [{"index": 0, "finish_reason": "length" if resp.get("status") == "incomplete" else "stop",
                         "message": {"role": "assistant", "content": output_text(resp)}}],
            "usage": {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0)}}


def start(port: int, get_target) -> ThreadingHTTPServer:
    """get_target() -> editor.target(cfg), asked per request so a provider switch is live."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _forward(self, body: bytes | None):
            # It adds the user's key: only Horizon may use it. A browser always sends Origin (a web page could
            # otherwise spend the key with a no-cors POST); a foreign Host means DNS rebinding.
            if "Origin" in self.headers or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(403)
                return
            t = get_target()
            path, to_responses = self.path, False
            if body and self.path.endswith("/chat/completions"):
                try:
                    data = json.loads(body)
                    data["model"] = t["model"]  # Horizon's config.json names an Ollama model
                    if t["provider"] == "ollama":
                        data.setdefault("reasoning_effort", "none")
                    if t["provider"] == "openai":  # a ChatGPT sign-in token only reaches the Responses API
                        data, path, to_responses = to_responses_request(data), "/v1/responses", True
                    body = json.dumps(data).encode()
                except ValueError:
                    pass
            headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            if t["key"]:
                headers["Authorization"] = "Bearer " + t["key"]
            req = urllib.request.Request(t["root"] + path, data=body, method=self.command, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    status, ctype, out = r.status, r.headers.get("Content-Type", "application/json"), r.read()
                if to_responses:
                    out = json.dumps(to_chat_response(json.loads(out))).encode()
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
