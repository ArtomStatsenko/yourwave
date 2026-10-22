"""Sign in with ChatGPT: OpenAI's open-source token-sharing flow (developers.openai.com/siwc), the same one pi uses.
A public client without a secret: each sign-in registers this installation (client_id=dynamic_agent_client), the
browser comes back to the page's /auth/callback, and the code becomes an access token that works as an OpenAI API
key for the Responses API, on the user's ChatGPT plan. Tokens live in DATA_DIR/chatgpt.json (owner-only)."""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid

from . import config

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPE = f"openid profile email offline_access resource.invoke {DIRECT_SCOPE}"

_pending: dict[str, tuple[str, str]] = {}  # state -> (PKCE verifier, redirect_uri)
_lock = threading.Lock()


def _path():
    return config.DATA_DIR / "chatgpt.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = _path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, _path())


def signed_in() -> bool:
    return bool(_load().get("refresh"))


def login_url(redirect_uri: str) -> str:
    """redirect_uri: http://127.0.0.1:<port>/auth/callback (OpenAI accepts only 127.0.0.1; the port may vary)."""
    data = _load()
    if not data.get("host_id"):  # one stable id per installation ("agent host")
        data["host_id"] = f"urn:uuid:{uuid.uuid4()}"
        _save(data)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    _pending[state] = (verifier, redirect_uri)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": "dynamic_agent_client", "agent_name_hint": "Personal Radio", "ext_agent_host_id": data["host_id"],
        "response_type": "code", "redirect_uri": redirect_uri, "resource": RESOURCE, "scope": SCOPE,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "nonce": secrets.token_urlsafe(32)})


def _token_request(form: dict) -> dict:
    req = urllib.request.Request(TOKEN_URL, urllib.parse.urlencode(form).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        t = json.load(r)
    if not isinstance(t, dict) or not t.get("access_token") or not isinstance(t.get("expires_in"), (int, float)):
        raise ValueError("OpenAI returned no token")
    if DIRECT_SCOPE not in str(t.get("scope", "")).split():
        raise ValueError("This ChatGPT plan did not allow the radio to use it")
    return t


def finish(state: str, code: str, client_id: str) -> None:
    pending = _pending.pop(state, None)
    if not pending or not code or not client_id:
        raise ValueError("This sign-in link is not from this page; start again from Settings")
    verifier, redirect_uri = pending
    t = _token_request({"grant_type": "authorization_code", "client_id": client_id, "code": code,
                        "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE})
    if not t.get("refresh_token"):
        raise ValueError("OpenAI returned no refresh token")
    with _lock:
        data = _load()
        data.update(client_id=client_id, access=t["access_token"], refresh=t["refresh_token"],
                    expires=time.time() + t["expires_in"] - 180)  # refresh 3 min early, like pi
        _save(data)


def token() -> str:
    """A valid access token, refreshed when it is about to expire; "" when not signed in or refresh failed."""
    with _lock:
        data = _load()
        if not data.get("refresh"):
            return ""
        if time.time() < data.get("expires", 0):
            return data["access"]
        try:
            t = _token_request({"grant_type": "refresh_token", "client_id": data["client_id"],
                                "refresh_token": data["refresh"], "resource": RESOURCE})
        except (OSError, ValueError):
            return ""
        data.update(access=t["access_token"], refresh=t.get("refresh_token") or data["refresh"],
                    expires=time.time() + t["expires_in"] - 180)
        _save(data)
        return data["access"]


def sign_out() -> None:
    with _lock:
        data = _load()
        for k in ("client_id", "access", "refresh", "expires"):
            data.pop(k, None)
        _save(data)
