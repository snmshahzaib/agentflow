"""Session identity.

AgentFlow has no user accounts by design (the MVP excludes them), but a
visitor must only see their own research. A signed, opaque session id gives
that without building authentication: the signature stops a visitor from
claiming someone else's session by editing a cookie.

Full rate limiting and the access-code path arrive in M4; this is the
identity primitive they build on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SESSION_COOKIE_NAME = "af_session"
_SEPARATOR = "."


def new_session_id() -> str:
    """A fresh opaque session identifier."""
    return secrets.token_hex(16)


def _signature(session_id: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_session_id(session_id: str, secret: str) -> str:
    """Return ``<id>.<signature>`` for use as a cookie value."""
    return f"{session_id}{_SEPARATOR}{_signature(session_id, secret)}"


def verify_session_cookie(value: str, secret: str) -> str | None:
    """Return the session id if the signature is valid, else ``None``.

    Compared with :func:`hmac.compare_digest` so the check does not leak
    information through timing.
    """
    session_id, separator, signature = value.partition(_SEPARATOR)
    if not separator or not session_id or not signature:
        return None
    expected = _signature(session_id, secret)
    if not hmac.compare_digest(signature, expected):
        return None
    return session_id
