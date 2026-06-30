"""
JWT authentication and RBAC dependency factory for the Fermentation Monitor API.

Roles (lowest → highest privilege):
    viewer   → read sensor data and summary
    operator → viewer + trigger device actions (baseline reset)
    admin    → operator + user management + service management

Usage in routes:
    @app.get("/readings")
    async def get_readings(user=Depends(require_role("viewer", "operator", "admin"))):
        ...

    @app.post("/users")
    async def create_user(user=Depends(require_role("admin"))):
        ...
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "insecure-default-replace-me")
ALGORITHM  = os.getenv("JWT_ALGORITHM", "HS256")
EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MINUTES", 480))

ROLES = ("viewer", "operator", "admin")

pwd_ctx      = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=EXPIRE_MIN)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── RBAC dependency factory ───────────────────────────────────────────────────

def require_role(*allowed_roles: str):
    """
    Returns a FastAPI dependency that validates the JWT and checks role membership.
    Pass the roles that are permitted to access the endpoint.

    Example:
        Depends(require_role("admin"))
        Depends(require_role("viewer", "operator", "admin"))
    """
    allowed = set(allowed_roles)

    async def _check(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
        payload = decode_token(token)
        role = payload.get("role", "")
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is not permitted here. Required: {sorted(allowed)}",
            )
        return payload

    return _check


# ── Convenience shortcuts ─────────────────────────────────────────────────────

AnyRole      = require_role(*ROLES)                          # viewer, operator, admin
OperatorPlus = require_role("operator", "admin")             # operator, admin
AdminOnly    = require_role("admin")                         # admin only
