"""Settings (A_* and SHARED_* env vars). Contract: MEMBER_A.md section 3, CONTRACT.md section 4.8."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/jp_a/config.py -> parents[0]=jp_a, [1]=backend, [2]=member-a
MEMBER_A_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(MEMBER_A_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # --- A-owned ---------------------------------------------------------
    a_port: int = 8001
    a_data_dir: Path = MEMBER_A_DIR / "data"
    a_models_dir: Path = MEMBER_A_DIR / "models"
    a_storage_mode: Literal["local", "s3"] = "local"
    a_s3_bucket: str | None = None
    a_s3_prefix: str = "a/"
    a_model_mode: Literal["local", "s3", "sagemaker_endpoint"] = "local"
    a_sagemaker_endpoint: str | None = None
    a_sagemaker_role_arn: str | None = None
    a_aws_region: str = "ap-south-1"
    a_solver_time_limit_s: float = Field(default=8.0, ge=0.5, le=9.5)
    a_coverage_floor: float = Field(default=0.25, ge=0.0, le=1.0)
    a_floor_penalty: float = Field(default=3.0, ge=0.0)
    a_seed: int = 42
    a_auth_disabled: bool = False
    a_cors_origins: str = "http://localhost:5174"

    # --- SHARED (contract-owned, read-only) --------------------------------
    shared_jwt_secret: str = "dev-only-jalpriority-secret-change-me-0123456789"
    shared_jwt_issuer: str = "jalpriority-b"
    shared_jwt_audience: str = "jalpriority"
    shared_env: Literal["local", "docker", "aws"] = "local"
    shared_log_level: str = "INFO"

    @field_validator("a_s3_bucket", "a_sagemaker_endpoint", "a_sagemaker_role_arn", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        # `.env.example` ships these keys blank; treat "" as unset.
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.a_cors_origins.split(",") if o.strip()]

    @property
    def scenarios_dir(self) -> Path:
        return Path(self.a_data_dir) / "scenarios"

    @property
    def derived_dir(self) -> Path:
        return Path(self.a_data_dir) / "derived"

    @property
    def training_dir(self) -> Path:
        return Path(self.a_data_dir) / "training"

    @property
    def truth_dir(self) -> Path:
        return Path(self.a_data_dir) / "truth"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
