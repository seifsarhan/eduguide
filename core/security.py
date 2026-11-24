import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urlparse
from flask import session, request, redirect, url_for, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
from core.db import get_db

# In-memory sliding-window rate limiter store: key -> deque[timestamps]
_RATE_BUCKETS = defaultdict(deque)


def hash_password(plain_password: str) -> str:
    return generate_password_hash(plain_password, method="pbkdf2:sha256:260000")


def is_password_hashed(stored: str) -> bool:
    if not stored or not isinstance(stored, str):
        return False
    return stored.startswith(("pbkdf2:", "scrypt:", "argon2:"))


def verify_password(stored_hash: str, plain_password: str) -> bool:
    """Strict hashed-only password verification (no plaintext bypass)."""
    if not stored_hash or not plain_password:
        return False
    if not is_password_hashed(stored_hash):
        return False
    try:
        return check_password_hash(stored_hash, plain_password)
    except Exception:
        return False


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _is_api_request() -> bool:
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if _is_api_request():
                return jsonify({"success": False, "error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def roles_required(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                if _is_api_request():
                    return jsonify({"success": False, "error": "Unauthorized"}), 401
                return redirect(url_for("login"))
            if session.get("role") not in allowed_roles:
                if _is_api_request():
                    return jsonify({"success": False, "error": "Forbidden: insufficient permissions"}), 403
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def rate_limit(max_requests: int, window_seconds: int, key_prefix: str = "rl"):
    """Sliding-window rate limiter keyed by client IP + optional user_id."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "local").split(",")[0].strip()
            actor = session.get("user_id", "anon")
            bucket_key = f"{key_prefix}:{client_ip}:{actor}"
            now = time.time()
            q = _RATE_BUCKETS[bucket_key]
            while q and (now - q[0]) > window_seconds:
                q.popleft()
            if len(q) >= max_requests:
                if _is_api_request():
                    return jsonify({
                        "success": False,
                        "error": f"Too many requests. Please wait {window_seconds} seconds and try again."
                    }), 429
                return (
                    "Too many requests. Please wait a moment and try again.",
                    429,
                )
            q.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def verify_same_origin_or_csrf():
    """
    Protect state-changing requests against cross-site request forgery:
    - Exempts external payment webhooks (/payments/callback).
    - Validates CSRF token if provided in form (_csrf_token) or header (X-CSRFToken).
    - Otherwise enforces strict Same-Origin via Origin / Referer headers for browser requests.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.path.startswith("/payments/callback"):
        return None

    # Enforce Same-Origin for requests carrying Origin or Referer FIRST
    host_netloc = urlparse(request.host_url).netloc
    origin = request.headers.get("Origin")
    same_origin_verified = False
    if origin and origin != "null":
        if urlparse(origin).netloc != host_netloc:
            if _is_api_request():
                return jsonify({"success": False, "error": "Cross-origin request blocked"}), 403
            abort(403)
        same_origin_verified = True

    referer = request.headers.get("Referer")
    if referer:
        if urlparse(referer).netloc != host_netloc:
            if _is_api_request():
                return jsonify({"success": False, "error": "Cross-site referer blocked"}), 403
            abort(403)
        same_origin_verified = True

    # Validate CSRF token when submitted (gracefully allow same-origin login forms if another tab rotated the session)
    is_login_post = request.path == "/login" or request.path.startswith("/u/")
    sent_token = (
        request.headers.get("X-CSRFToken")
        or request.form.get("_csrf_token")
        or (request.get_json(silent=True) or {}).get("_csrf_token")
    )
    if sent_token:
        expected = session.get("_csrf_token")
        if not expected or not secrets.compare_digest(str(sent_token), str(expected)):
            if is_login_post and same_origin_verified:
                return None
            if _is_api_request():
                return jsonify({"success": False, "error": "Invalid CSRF token"}), 403
            abort(403)
    return None


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=()")
    return response


def log_audit(action: str, target_type: str = "", target_id: str = "", details: str = "", actor_override: dict = None):
    """Persist an accountability record to audit_logs."""
    try:
        actor = actor_override or {}
        actor_id = str(actor.get("id", session.get("user_id", "system")))
        actor_name = str(actor.get("name", session.get("name", "Unknown")))
        actor_role = str(actor.get("role", session.get("role", "anonymous")))
        ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "local").split(",")[0].strip()

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO audit_logs (actor_id, actor_name, actor_role, action, target_type, target_id, details, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (actor_id, actor_name, actor_role, action, target_type, str(target_id), details, ip_addr),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"WARN: Audit log write failed: {e}")
