import hashlib
import time
import jwt
from typing import Optional, Dict
from jp_b.config import settings
from jp_b.api.errors import APIError

# We will hash the demo users' passwords in memory at startup.
class AuthService:
    def __init__(self):
        self._users: Dict[str, Dict[str, str]] = {}
        self._svc_token: Optional[str] = None
        self._svc_token_expires: int = 0
        self._init_demo_users()

    def _init_demo_users(self):
        for user_str in settings.b_demo_users.split(","):
            if not user_str: continue
            parts = user_str.split(":")
            if len(parts) == 3:
                username, password, role = parts
                salt = b"jalpriority_salt"
                hashed_pw = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)
                self._users[username] = {
                    "hashed_pw": hashed_pw,
                    "role": role,
                }

    def _hash_password(self, password: str) -> bytes:
        salt = b"jalpriority_salt"
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)

    def login(self, username: str, password: str) -> dict:
        user = self._users.get(username)
        if not user or user["hashed_pw"] != self._hash_password(password):
            raise APIError("INVALID_CREDENTIALS", status_code=401, message="Invalid username or password")

        role = user["role"]
        now = int(time.time())
        expires_at = now + 8 * 3600
        payload = {
            "iss": settings.shared_jwt_issuer,
            "aud": settings.shared_jwt_audience,
            "sub": username,
            "role": role,
            "iat": now,
            "exp": expires_at,
        }
        token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
        return {
            "token": token,
            "officer_id": username,
            "role": role,
            "expires_at": expires_at
        }

    def get_service_token(self) -> str:
        now = int(time.time())
        if self._svc_token and now < self._svc_token_expires - 60:
            return self._svc_token

        expires_at = now + 5 * 60
        payload = {
            "iss": settings.shared_jwt_issuer,
            "aud": settings.shared_jwt_audience,
            "sub": "svc-b",
            "role": "service",
            "iat": now,
            "exp": expires_at,
        }
        self._svc_token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
        self._svc_token_expires = expires_at
        return self._svc_token

    def verify_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(
                token, 
                settings.shared_jwt_secret, 
                algorithms=["HS256"], 
                audience=settings.shared_jwt_audience,
                issuer=settings.shared_jwt_issuer,
                leeway=30
            )
            return payload
        except jwt.PyJWTError:
            raise APIError("UNAUTHORIZED", status_code=401, message="Invalid or expired token")

auth_service = AuthService()
