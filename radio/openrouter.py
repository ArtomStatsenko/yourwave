"""Sign in with OpenRouter (OAuth PKCE, https://openrouter.ai/docs/use-cases/oauth-pkce): the browser comes back to
the page's /openrouter/callback with a code, which is exchanged for the user's own API key. The key is kept in
DATA_DIR/openrouter.key (owner-only), never shown on the page."""
import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request

from . import config

_pending: dict[str, str] = {}  # callback state -> PKCE verifier, for sign-ins started by this UI process


def _path():
    return config.DATA_DIR / "openrouter.key"


def key() -> str:
    try:
        return _path().read_text().strip()
    except FileNotFoundError:
        return ""


def login_url(callback: str) -> str:
    state, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(48)
    _pending[state] = verifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    back = callback + "?" + urllib.parse.urlencode({"state": state})
    return "https://openrouter.ai/auth?" + urllib.parse.urlencode(
        {"callback_url": back, "code_challenge": challenge, "code_challenge_method": "S256"})


def finish(state: str, code: str) -> None:
    verifier = _pending.pop(state, None)
    if not verifier or not code:
        raise ValueError("This sign-in link is not from this page; start again from Settings")
    req = urllib.request.Request("https://openrouter.ai/api/v1/auth/keys", json.dumps(
        {"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}).encode(),
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        new = json.load(r).get("key", "")
    if not isinstance(new, str) or not new.startswith("sk-"):
        raise ValueError("OpenRouter returned no key")
    tmp = _path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(new)
    os.replace(tmp, _path())


def sign_out() -> None:
    _path().unlink(missing_ok=True)
