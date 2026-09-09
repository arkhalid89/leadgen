"""Password hashing and cookie-session authentication."""
from __future__ import annotations

import re
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import db, settings

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="leadgen-session")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email))


def validate_password_strength(password: str) -> str | None:
    """Return an error message, or None when the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password):
        return "Password must contain at least one letter."
    if not re.search(r"\d", password):
        return "Password must contain at least one number."
    return None


def generate_license_key() -> str:
    blocks = [secrets.token_hex(2).upper() for _ in range(3)]
    return "LEAD-" + "-".join(blocks)


def issue_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=settings.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return uid if isinstance(uid, int) else None


async def current_user(request: Request) -> dict:
    """FastAPI dependency: resolves the signed cookie to a user row."""
    token = request.cookies.get(settings.SESSION_COOKIE)
    uid = read_session(token) if token else None
    if uid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (uid,)))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    user.pop("password", None)
    return user


async def active_user(user: dict = Depends(current_user)) -> dict:
    """Dependency for routes that require an activated licence."""
    if not user.get("is_active"):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Account is not activated. Enter a licence key to unlock the tools.",
        )
    return user
