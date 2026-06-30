"""
User management — CRUD against the SQLite users table.
All mutations are admin-only (enforced in api.py via Depends(AdminOnly)).
"""

import sqlite3
import time
from typing import Optional
from fastapi import HTTPException, status
from pydantic import BaseModel, field_validator

from auth import hash_password, verify_password

VALID_ROLES = {"viewer", "operator", "admin"}


# ── Pydantic models ───────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    role: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        return v

class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        return v

class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: int


# ── Schema bootstrap ──────────────────────────────────────────────────────────

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('viewer', 'operator', 'admin')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);
"""

def ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(USERS_DDL)
    conn.commit()


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    return dict(zip(["id", "username", "password_hash", "role", "is_active"], row))


def authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> dict:
    user = get_user_by_username(conn, username)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return user


def list_users(conn: sqlite3.Connection) -> list[UserOut]:
    rows = conn.execute(
        "SELECT id, username, role, is_active, created_at FROM users ORDER BY id"
    ).fetchall()
    return [UserOut(id=r[0], username=r[1], role=r[2], is_active=bool(r[3]), created_at=r[4]) for r in rows]


def create_user(conn: sqlite3.Connection, data: UserCreate) -> UserOut:
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (data.username, hash_password(data.password), data.role, int(time.time())),
        )
        conn.commit()
        return UserOut(
            id=cur.lastrowid, username=data.username, role=data.role,
            is_active=True, created_at=int(time.time()),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Username '{data.username}' already exists")


def update_user(conn: sqlite3.Connection, user_id: int, data: UserUpdate) -> UserOut:
    user = conn.execute(
        "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    fields, values = [], []
    if data.role is not None:
        fields.append("role = ?"); values.append(data.role)
    if data.is_active is not None:
        fields.append("is_active = ?"); values.append(int(data.is_active))
    if data.password is not None:
        fields.append("password_hash = ?"); values.append(hash_password(data.password))

    if fields:
        values.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()

    updated = conn.execute(
        "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return UserOut(id=updated[0], username=updated[1], role=updated[2], is_active=bool(updated[3]), created_at=updated[4])


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    result = conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    conn.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")
