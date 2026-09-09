"""Registration, login, activation and account management."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .. import db, security, settings

router = APIRouter(prefix="/api/auth", tags=["auth"])
account_router = APIRouter(prefix="/api/account", tags=["account"])


class RegisterIn(BaseModel):
    email: str
    password: str
    full_name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class ActivateIn(BaseModel):
    license_key: str


class ProfileIn(BaseModel):
    full_name: str = Field(default="", max_length=120)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def _set_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        settings.SESSION_COOKIE,
        security.issue_session(user_id),
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.IS_PRODUCTION,
        path="/",
    )


def _public(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user.get("full_name", ""),
        "is_active": bool(user.get("is_active")),
        "license_key": user.get("license_key", ""),
        "created_at": user.get("created_at"),
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, response: Response) -> dict:
    email = payload.email.strip().lower()
    if not security.is_valid_email(email):
        raise HTTPException(400, "Enter a valid email address.")
    problem = security.validate_password_strength(payload.password)
    if problem:
        raise HTTPException(400, problem)
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "An account with that email already exists.")

    cur = db.execute(
        "INSERT INTO users (email, password, full_name) VALUES (?, ?, ?)",
        (email, security.hash_password(payload.password), payload.full_name.strip()),
    )
    user_id = int(cur.lastrowid)
    _set_cookie(response, user_id)
    user = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (user_id,)))
    return _public(user)  # type: ignore[arg-type]


@router.post("/login")
async def login(payload: LoginIn, response: Response) -> dict:
    email = payload.email.strip().lower()
    row = db.row_to_dict(db.query_one("SELECT * FROM users WHERE email = ?", (email,)))
    if row is None or not security.verify_password(payload.password, row["password"]):
        raise HTTPException(401, "Incorrect email or password.")

    db.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
    )
    _set_cookie(response, int(row["id"]))
    return _public(row)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(security.current_user)) -> dict:
    return _public(user)


@router.post("/activate")
async def activate(payload: ActivateIn, user: dict = Depends(security.current_user)) -> dict:
    key = payload.license_key.strip().upper()
    row = db.row_to_dict(db.query_one("SELECT * FROM license_keys WHERE key = ?", (key,)))
    if row is None:
        raise HTTPException(404, "That licence key was not found.")
    if row["used_count"] >= row["max_uses"]:
        raise HTTPException(409, "That licence key has already been used.")
    if row.get("expires_at") and row["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(409, "That licence key has expired.")

    db.execute("UPDATE license_keys SET used_count = used_count + 1 WHERE key = ?", (key,))
    db.execute(
        "UPDATE users SET is_active = 1, license_key = ? WHERE id = ?", (key, user["id"])
    )
    updated = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],)))
    return _public(updated)  # type: ignore[arg-type]


@account_router.put("/profile")
async def update_profile(
    payload: ProfileIn, user: dict = Depends(security.current_user)
) -> dict:
    db.execute(
        "UPDATE users SET full_name = ? WHERE id = ?",
        (payload.full_name.strip(), user["id"]),
    )
    updated = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],)))
    return _public(updated)  # type: ignore[arg-type]


@account_router.put("/password")
async def change_password(
    payload: PasswordIn, user: dict = Depends(security.current_user)
) -> dict:
    row = db.query_one("SELECT password FROM users WHERE id = ?", (user["id"],))
    if row is None or not security.verify_password(payload.current_password, row["password"]):
        raise HTTPException(401, "Your current password is incorrect.")
    problem = security.validate_password_strength(payload.new_password)
    if problem:
        raise HTTPException(400, problem)
    db.execute(
        "UPDATE users SET password = ? WHERE id = ?",
        (security.hash_password(payload.new_password), user["id"]),
    )
    return {"ok": True}


@account_router.delete("")
async def delete_account(response: Response, user: dict = Depends(security.current_user)) -> dict:
    uid = user["id"]
    with db.transaction() as conn:
        conn.execute("DELETE FROM email_templates WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM job_events WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM leads WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM jobs WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return {"ok": True}
