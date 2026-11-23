"""
Server-side security primitives for Beardslee St. Research.
- Memory-hard scrypt password hashing (OWASP-recommended parameters)
- Constant-time allowlist verification (never exposed to client)
- Two-step pre-auth nonce + HttpOnly session token management
- Sliding-window IP & account rate limiting with temporary lockout
- Per-client CSRF token generation and verification
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

# Default allowlisted accounts (server-side only; can be overridden via ALLOWED_EMAILS env var)
DEFAULT_ALLOWED_EMAILS: Set[str] = {
    "isamalfuqaha@gmail.com",
    "samuelnoginsky@gmail.com",
}

# Scrypt parameters (N=32768, r=8, p=1 -> 32 MiB memory-hard)
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash a plaintext password using scrypt and return an encoded string."""
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=64 * 1024 * 1024,
        dklen=SCRYPT_DKLEN,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(dk).decode('ascii')}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a plaintext password against a stored scrypt hash in constant time."""
    if not password or not encoded_hash:
        return False
    try:
        parts = encoded_hash.strip().split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4])
        expected = base64.b64decode(parts[5])
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=128 * 1024 * 1024,
            dklen=len(expected),
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def get_allowed_emails() -> Set[str]:
    """Load allowlisted emails strictly on the server."""
    raw = os.getenv("ALLOWED_EMAILS", "").strip()
    if not raw:
        return set(DEFAULT_ALLOWED_EMAILS)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_email_allowed(email: str) -> bool:
    """Constant-time check against the server-side email allowlist."""
    candidate = (email or "").strip().lower()
    if not candidate or "@" not in candidate:
        return False
    matched = False
    for allowed in get_allowed_emails():
        if hmac.compare_digest(candidate, allowed):
            matched = True
    return matched


def get_password_hash_for_email(email: str) -> Optional[str]:
    """
    Look up the scrypt password hash for an allowlisted email from environment variables:
      AUTH_HASH_ISAM     -> isamalfuqaha@gmail.com
      AUTH_HASH_SAMUEL   -> samuelnoginsky@gmail.com
    Or generic AUTH_HASH_<SANITIZED_EMAIL>.
    """
    norm = (email or "").strip().lower()
    explicit_map = {
        "isamalfuqaha@gmail.com": os.getenv("AUTH_HASH_ISAM", "").strip(),
        "samuelnoginsky@gmail.com": os.getenv("AUTH_HASH_SAMUEL", "").strip(),
    }
    if norm in explicit_map and explicit_map[norm]:
        return explicit_map[norm]
    env_key = "AUTH_HASH_" + "".join(ch if ch.isalnum() else "_" for ch in norm).upper()
    val = os.getenv(env_key, "").strip()
    return val or None


@dataclass
class PreAuthRecord:
    email: str
    ip: str
    expires_at: float


@dataclass
class SessionRecord:
    session_id: str
    email: str
    ip: str
    csrf_token: str
    created_at: float
    last_seen: float
    expires_at: float


class RateLimiter:
    """Sliding-window rate limiter with account lockout protection."""

    def __init__(
        self,
        max_attempts: int = 8,
        window_seconds: int = 300,
        lockout_threshold: int = 5,
        lockout_seconds: int = 900,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_threshold = lockout_threshold
        self.lockout_seconds = lockout_seconds
        self._hits: Dict[str, deque] = defaultdict(deque)
        self._failures: Dict[str, int] = defaultdict(int)
        self._lockouts: Dict[str, float] = {}

    def check_allowed(self, key: str) -> Tuple[bool, int]:
        now = time.time()
        locked_until = self._lockouts.get(key, 0.0)
        if locked_until > now:
            return False, int(locked_until - now)

        dq = self._hits[key]
        cutoff = now - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.max_attempts:
            retry_after = int(dq[0] + self.window_seconds - now) + 1
            return False, max(retry_after, 1)
        dq.append(now)
        return True, 0

    def record_failure(self, key: str) -> None:
        self._failures[key] += 1
        if self._failures[key] >= self.lockout_threshold:
            self._lockouts[key] = time.time() + self.lockout_seconds
            self._failures[key] = 0

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._lockouts.pop(key, None)


class AuthStore:
    """In-memory server-side session & pre-auth store (swap for Redis in multi-worker prod)."""

    def __init__(self, idle_ttl: int = 3600, absolute_ttl: int = 28800, preauth_ttl: int = 300) -> None:
        self.idle_ttl = idle_ttl
        self.absolute_ttl = absolute_ttl
        self.preauth_ttl = preauth_ttl
        self._preauth: Dict[str, PreAuthRecord] = {}
        self._sessions: Dict[str, SessionRecord] = {}

    def create_preauth(self, email: str, ip: str) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self._preauth[token] = PreAuthRecord(
            email=email.strip().lower(),
            ip=ip,
            expires_at=time.time() + self.preauth_ttl,
        )
        return token

    def verify_preauth(self, token: Optional[str], email: str, ip: str) -> bool:
        if not token:
            return False
        rec = self._preauth.get(token)
        if not rec:
            return False
        if rec.expires_at < time.time():
            self._preauth.pop(token, None)
            return False
        if not hmac.compare_digest(rec.email, email.strip().lower()):
            return False
        if rec.ip != ip:
            return False
        return True

    def consume_preauth(self, token: Optional[str]) -> None:
        if token:
            self._preauth.pop(token, None)

    def create_session(self, email: str, ip: str) -> SessionRecord:
        self._prune()
        now = time.time()
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        rec = SessionRecord(
            session_id=sid,
            email=email.strip().lower(),
            ip=ip,
            csrf_token=csrf,
            created_at=now,
            last_seen=now,
            expires_at=now + self.absolute_ttl,
        )
        self._sessions[sid] = rec
        return rec

    def get_session(self, sid: Optional[str]) -> Optional[SessionRecord]:
        if not sid:
            return None
        rec = self._sessions.get(sid)
        if not rec:
            return None
        now = time.time()
        if now > rec.expires_at or (now - rec.last_seen) > self.idle_ttl:
            self._sessions.pop(sid, None)
            return None
        rec.last_seen = now
        return rec

    def revoke_session(self, sid: Optional[str]) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def _prune(self) -> None:
        now = time.time()
        for k in [k for k, v in self._preauth.items() if v.expires_at < now]:
            self._preauth.pop(k, None)
        for k in [
            k
            for k, v in self._sessions.items()
            if now > v.expires_at or (now - v.last_seen) > self.idle_ttl
        ]:
            self._sessions.pop(k, None)
