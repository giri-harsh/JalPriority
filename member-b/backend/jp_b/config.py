from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

from pathlib import Path

_MEMBER_B_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SQLITE_PATH = str(_MEMBER_B_ROOT / "var" / "jp_b.sqlite3")

class Settings(BaseSettings):
    shared_jwt_secret: str = "dev-only-jalpriority-secret-change-me-0123456789"
    shared_jwt_issuer: str = "jalpriority-b"
    shared_jwt_audience: str = "jalpriority"
    shared_env: str = "local"
    shared_log_level: str = "INFO"

    b_port: int = 8002
    b_store_mode: str = "sqlite"
    b_sqlite_path: str = _DEFAULT_SQLITE_PATH
    b_ddb_table_prefix: str = "jp_b_"
    b_aws_region: str = "ap-south-1"
    b_upstream_a_url: str = "http://localhost:8001"
    b_use_mock_a: bool = True
    b_a_timeout_s: int = 15
    b_replan_time_limit_s: float = 3.0
    b_demo_users: str = "officer_01:jal-demo-2026:dispatcher,admin_01:jal-admin-2026:admin"
    b_debt_decay: float = 0.3
    b_debt_cap_litres: Optional[int] = None
    b_ff_fairness_weights: bool = True
    b_ff_vulnerability: bool = True
    b_ff_replan: bool = True
    b_cors_origins: str = "http://localhost:5173"
    b_contract_live_a: int = 0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
