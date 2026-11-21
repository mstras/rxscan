"""Login for RxScan.

Always on. Built-in login is admin / admin123 (see config.py); APP_USERNAME / APP_PASSWORD override it. Two ways in:
- Sign-in page (/login) → signed session cookie, valid SESSION_DAYS. Works in installed PWAs,
  where the browser's basic-auth popup is unreliable.
- HTTP Basic header (Authorization: Basic …), for curl/scripts.

The cookie is HMAC-signed with a random key kept in DATA_DIR/session.key, and includes a
fingerprint of the credentials, so changing APP_USERNAME/APP_PASSWORD signs everyone out.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from . import config

log = logging.getLogger("rxscan.auth")

COOKIE = "rxscan_session"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "90"))
USERNAME = config.APP_USERNAME
PASSWORD = config.APP_PASSWORD
ENABLED = bool(PASSWORD)

# Paths reachable without signing in (nothing private in them).
PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/sw.js", "/manifest.webmanifest", "/favicon.ico",
                "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}

# Brute-force protection: max failures per IP per window.
MAX_FAILS, WINDOW = 8, 15 * 60
_fails: dict[str, deque] = defaultdict(deque)


def _load_key() -> bytes:
    path = config.DATA_DIR / "session.key"
    try:
        if path.exists():
            k = path.read_bytes()
            if len(k) >= 32:
                return k
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        k = secrets.token_bytes(32)
        path.write_bytes(k)
        os.chmod(path, 0o600)
        return k
    except OSError as e:
        log.warning("Can't persist session key (%s); sign-ins will reset when the container restarts.", e)
        return secrets.token_bytes(32)


_KEY = _load_key() if ENABLED else b""
_FPR = hashlib.sha256(f"{USERNAME}\0{PASSWORD}".encode()).hexdigest()[:16]


def _sign(msg: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_KEY, msg.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_session() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    msg = f"{exp}.{_FPR}"
    return f"{msg}.{_sign(msg)}"


def _valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        exp, fpr, sig = token.split(".", 2)
    except ValueError:
        return False
    if fpr != _FPR or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, _sign(f"{exp}.{fpr}"))


def check_credentials(username: str, password: str) -> bool:
    ok_u = hmac.compare_digest((username or "").encode(), USERNAME.encode())
    ok_p = hmac.compare_digest((password or "").encode(), PASSWORD.encode())
    return ok_u and ok_p


def _valid_basic(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    return check_credentials(user, pw)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def is_authenticated(request) -> bool:
    if not ENABLED:
        return True
    return _valid_session(request.cookies.get(COOKIE)) or _valid_basic(request.headers.get("authorization"))


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "?")


def too_many_failures(ip: str) -> bool:
    q = _fails[ip]
    now = time.time()
    while q and q[0] < now - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_failure(ip: str):
    _fails[ip].append(time.time())


def clear_failures(ip: str):
    _fails.pop(ip, None)


def safe_next(nxt: str | None) -> str:
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and not nxt.startswith("/login"):
        return nxt
    return "/"
