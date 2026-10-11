"""Mint a dev JWT with exactly the CONTRACT section 8 claims (for A's isolated testing / the harness).

    python scripts/mint_dev_token.py                      # dispatcher, officer_01, 8 h
    python scripts/mint_dev_token.py --role service --sub svc-b --minutes 5
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import jwt

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.split("#", 1)[0].strip()
    return env


def mint(sub: str, role: str, ttl_seconds: int, secret: str, issuer: str, audience: str) -> str:
    now = int(time.time())
    claims = {"iss": issuer, "aud": audience, "sub": sub, "role": role, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(claims, secret, algorithm="HS256")


def main(argv: list[str] | None = None) -> int:
    file_env = _load_env_file()

    def cfg(key: str, default: str) -> str:
        return os.environ.get(key) or file_env.get(key) or default

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sub", default="officer_01")
    p.add_argument("--role", default="dispatcher", help="dispatcher | admin | service (anything else -> 403 at A)")
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--minutes", type=float, default=None, help="overrides --hours")
    args = p.parse_args(argv)
    ttl = int((args.minutes * 60) if args.minutes is not None else args.hours * 3600)
    token = mint(
        args.sub,
        args.role,
        ttl,
        cfg("SHARED_JWT_SECRET", "dev-only-jalpriority-secret-change-me-0123456789"),
        cfg("SHARED_JWT_ISSUER", "jalpriority-b"),
        cfg("SHARED_JWT_AUDIENCE", "jalpriority"),
    )
    sys.stdout.write(token + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
