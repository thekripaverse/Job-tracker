"""Central security helpers (Phase 1.5). No new third-party dependencies.

Covers: CSRF tokens (session-bound, itsdangerous-free HMAC compare),
in-memory rate limiting, safe-redirect validation, OAuth state,
SSRF-safe URL fetching, and Fernet encryption for stored OAuth tokens.

CSRF and rate limiting are bypassed ONLY when the Flask app runs with
TESTING=True (the existing pytest suite) or CSRF_DISABLED/RATE_LIMIT_DISABLED
config flags. Dedicated tests in tests/test_security_hardening.py exercise
enforcement with TESTING=False.
"""
import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import time
import urllib.parse
import urllib.request
from functools import wraps

from flask import current_app, g, jsonify, request, session

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------
def is_production():
    try:
        return (current_app.config.get('ENV') or 'development') == 'production'
    except RuntimeError:
        import os
        return os.environ.get('ENV') == 'production'


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------
CSRF_FORM_FIELD = 'csrf_token'
CSRF_HEADER = 'X-CSRFToken'
CSRF_SESSION_KEY = '_csrf_token'

_MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}


def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(provided):
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


def csrf_protect():
    """Call from before_app_request. Returns a 400/403 response or None."""
    if request.method not in _MUTATING:
        return None
    try:
        if current_app.config.get('TESTING') or current_app.config.get('CSRF_DISABLED'):
            return None
    except RuntimeError:
        pass
    provided = request.form.get(CSRF_FORM_FIELD)
    if not provided and request.is_json:
        try:
            provided = (request.get_json(silent=True) or {}).get(CSRF_FORM_FIELD)
        except Exception:
            provided = None
    if not provided:
        provided = request.headers.get(CSRF_HEADER)
    if not validate_csrf_token(provided):
        if request.is_json or request.path.startswith('/api'):
            return jsonify({'error': 'Invalid or missing CSRF token. Reload the page and try again.'}), 403
        return jsonify({'error': 'Invalid or missing CSRF token. Reload the page and try again.'}), 403
    return None


# --------------------------------------------------------------------------
# Rate limiting (in-memory, per-process; no Redis per Phase 1.5 scope)
# --------------------------------------------------------------------------
_rate_buckets = {}
_RATE_LOCK = None


def _get_lock():
    global _RATE_LOCK
    if _RATE_LOCK is None:
        import threading
        _RATE_LOCK = threading.Lock()
    return _RATE_LOCK


def check_rate_limit(key, limit, window_seconds):
    """Sliding-window limiter. Returns (allowed: bool, retry_after_s: int)."""
    now = time.time()
    with _get_lock():
        hits = _rate_buckets.get(key, [])
        hits = [t for t in hits if t > now - window_seconds]
        if len(hits) >= limit:
            retry = int(hits[0] + window_seconds - now) + 1
            _rate_buckets[key] = hits
            return False, max(retry, 1)
        hits.append(now)
        _rate_buckets[key] = hits
        return True, 0


def reset_rate_limits():
    with _get_lock():
        _rate_buckets.clear()


def rate_limit(limit, window_seconds=60, key_prefix=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                if current_app.config.get('TESTING') or current_app.config.get('RATE_LIMIT_DISABLED'):
                    return f(*args, **kwargs)
            except RuntimeError:
                pass
            ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
            key = f"{key_prefix or request.endpoint or request.path}:{ip}"
            allowed, retry = check_rate_limit(key, limit, window_seconds)
            if not allowed:
                resp = jsonify({'error': 'Too many requests. Please slow down and try again.',
                                'retry_after_seconds': retry})
                resp.status_code = 429
                resp.headers['Retry-After'] = str(retry)
                return resp
            return f(*args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------
# Safe redirects (open-redirect fix)
# --------------------------------------------------------------------------
def is_safe_redirect(target):
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target.startswith('/') or target.startswith('//'):
        return False
    if target.startswith('/\\') or '\\' in target.split('?')[0].split('#')[0]:
        return False
    lowered = target.lower()
    if lowered.startswith(('javascript:', 'data:', 'vbscript:')):
        return False
    # Embedded scheme/host tricks
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    return True


def safe_redirect_target(candidate, fallback='/'):
    if is_safe_redirect(candidate):
        return candidate
    return fallback


# --------------------------------------------------------------------------
# OAuth state (random, session-bound, expiring, single-use)
# --------------------------------------------------------------------------
OAUTH_STATE_SESSION_KEY = '_oauth_states'


def new_oauth_state(purpose='gmail', ttl_seconds=600):
    token = secrets.token_urlsafe(32)
    states = session.get(OAUTH_STATE_SESSION_KEY, {})
    now = time.time()
    # prune expired
    states = {k: v for k, v in states.items() if v.get('exp', 0) > now}
    states[token] = {'purpose': purpose, 'exp': now + ttl_seconds}
    session[OAUTH_STATE_SESSION_KEY] = states
    return token


def consume_oauth_state(token, purpose='gmail'):
    if not token:
        return False
    states = session.get(OAUTH_STATE_SESSION_KEY, {})
    entry = states.pop(token, None)
    session[OAUTH_STATE_SESSION_KEY] = states
    if not entry:
        return False
    if entry.get('purpose') != purpose:
        return False
    if entry.get('exp', 0) < time.time():
        return False
    return True


# --------------------------------------------------------------------------
# SSRF-safe fetching
# --------------------------------------------------------------------------
CLOUD_METADATA_IPS = {
    '169.254.169.254',  # AWS/GCP/Azure link-local metadata
    '100.100.100.100',  # Alibaba metadata
    '192.0.0.192',      # Oracle metadata (some regions)
}


def validate_fetch_target(url):
    """Return (ok: bool, reason: str). Rejects non-http(s), credentials in URL,
    unresolvable hosts, and any non-globally-routable IP (loopback, private,
    link-local, multicast, reserved, unspecified) plus cloud metadata IPs."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return False, 'Malformed URL'
    if parsed.scheme not in ('http', 'https'):
        return False, 'Only http and https URLs are allowed'
    if parsed.username or parsed.password or '@' in (parsed.netloc or ''):
        return False, 'Credentials in URL are not allowed'
    host = parsed.hostname
    if not host:
        return False, 'URL has no host'
    if host.lower() == 'localhost':
        return False, 'Localhost URLs are not allowed'
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                   type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, 'Host could not be resolved'
    ips = {info[4][0] for info in infos}
    if not ips:
        return False, 'Host could not be resolved'
    for ip_str in ips:
        if ip_str in CLOUD_METADATA_IPS:
            return False, 'Cloud metadata addresses are not allowed'
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, 'Unparseable resolved address'
        if not ip.is_global:
            return False, f'Resolved address {ip_str} is not publicly routable'
    return True, ''


def safe_fetch_url(url, max_bytes=2 * 1024 * 1024, timeout=6, user_agent=None):
    """Fetch a URL after SSRF validation, with size cap + timeout.
    Raises ValueError on validation failure, urllib.error on fetch failure."""
    ok, reason = validate_fetch_target(url)
    if not ok:
        raise ValueError(f'Blocked URL ({reason})')
    req = urllib.request.Request(url, headers={
        'User-Agent': user_agent or 'Mozilla/5.0 (JobTracker URL preview)',
        'Accept': 'text/html,application/xhtml+xml',
    })
    # Single bounded read (not a chunk loop): test doubles implement read()
    # without size semantics, and one read caps memory either way.
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        try:
            data = resp.read(max_bytes + 1)
        except TypeError:
            data = resp.read()
    if len(data) > max_bytes:
        raise ValueError(f'Response exceeds size limit ({max_bytes} bytes)')
    return data


# --------------------------------------------------------------------------
# OAuth token encryption (Fernet; key from env, never hardcoded)
# --------------------------------------------------------------------------
_ENC_PREFIX = 'enc:'
_cipher = None


def _load_key():
    import os
    key = os.environ.get('TOKEN_ENCRYPTION_KEY', '')
    try:
        key = key or current_app.config.get('TOKEN_ENCRYPTION_KEY', '')
    except RuntimeError:
        pass
    return (key or '').strip()


def get_token_cipher():
    """Return a Fernet cipher. In production a key is REQUIRED (fail closed);
    in development an ephemeral key is generated with a loud warning."""
    global _cipher
    key = _load_key()
    if key:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    if is_production():
        raise RuntimeError('TOKEN_ENCRYPTION_KEY must be set in production')
    logger.warning('TOKEN_ENCRYPTION_KEY not set — using ephemeral dev key; '
                   'stored Gmail tokens will not survive restarts. Set a persistent key.')
    from cryptography.fernet import Fernet, Fernet as _F
    if _cipher is None:
        _cipher = _F(Fernet.generate_key())
    return _cipher


def encrypt_token(plaintext):
    if plaintext is None:
        return None
    data = str(plaintext)
    # Leave existing dev mock tokens untouched (fail-closed paths handle them).
    if data in ('dev_mock_access_token', 'dev_mock_refresh_token'):
        return data
    token = get_token_cipher().encrypt(data.encode()).decode()
    return _ENC_PREFIX + token


def decrypt_token(stored):
    if stored is None:
        return None
    data = str(stored)
    if not data.startswith(_ENC_PREFIX):
        return data  # legacy plaintext: caller may re-encrypt on next save
    return get_token_cipher().decrypt(data[len(_ENC_PREFIX):].encode()).decode()


def is_mock_token(value):
    return value in ('dev_mock_access_token', 'dev_mock_refresh_token')


def generate_token_encryption_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()
