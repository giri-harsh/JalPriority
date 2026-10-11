"""JWT verification (CONTRACT section 8). B issues tokens; A only verifies them."""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Request

from ..config import Settings
from .errors import ForbiddenError, UnauthorizedError

ALLOWED_ROLES = {"dispatcher", "admin", "service"}
LEEWAY_S = 30
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.shared_jwt_secret,
            algorithms=["HS256"],
            audience=settings.shared_jwt_audience,
            issuer=settings.shared_jwt_issuer,
            leeway=LEEWAY_S,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:  # expired, bad signature, wrong iss/aud, malformed ...
        raise UnauthorizedError("Missing, invalid or expired token.", details={"reason": type(exc).__name__}) from exc
    role = claims.get("role")
    if role not in ALLOWED_ROLES:
        raise ForbiddenError("Role not allowed.", details={"role": role if isinstance(role, str) else None})
    return Principal(sub=str(claims["sub"]), role=role)


def _settings(request: Request) -> Settings:
    return request.app.state.container.settings


def require_auth(request: Request, settings: Settings = Depends(_settings)) -> Principal:
    """Dependency for every route except health. ``A_AUTH_DISABLED=true`` skips verification (A's local tests only)."""
    if settings.a_auth_disabled:
        return Principal(sub="auth-disabled", role="admin")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError("Missing bearer token.")
    return decode_token(token.strip(), settings)
