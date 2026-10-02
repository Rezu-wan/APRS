"""
api/core/config.py — environment-driven application settings.

All secrets and infrastructure locations come from environment variables
(optionally via a .env file at the project root — see .env.example).
 NOTHING secret is hardcoded: the api_key_* dev defaults exist only so the
app can boot in local development; the application logs a warning when they
are in use, and real deployments MUST override them via the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_KEY_WARNING = (
    "Using development API-key defaults. These are NOT safe outside local "
    "development — set API_KEY_SYSTEM / API_KEY_ADMIN / API_KEY_SUPPORT / "
    "API_KEY_CUSTOMER in the environment."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- application -------------------------------------------------------
    environment: str = "development"  # development | test | production
    log_level: str = "INFO"
    cors_origins: str = ""  # comma-separated list; empty = no browser origins

    # --- database ----------------------------------------------------------
    # Production target: postgresql+psycopg://user:pass@host:5432/payment_recovery
    # Local dev fallback: sqlite:///./data/app.db
    database_url: str = "sqlite:///./data/app.db"

    # --- authentication (API-key foundation; see api/core/security.py) -----
    api_key_system: str = "dev-system-key"
    api_key_admin: str = "dev-admin-key"
    api_key_support: str = "dev-support-key"
    api_key_customer: str = "dev-customer-key"

    # --- recovery policy (deliberately separate from the ML model so
    #     thresholds/business rules can change without retraining) -----------
    recovery_min_safe_probability: float = 0.90
    recovery_max_amount: float = 1500.0
    recovery_max_previous_failures: int = 3

    # --- ML artifacts -------------------------------------------------------
    model_dir: str = "models"

    # --- GenAI explanation layer (Stage 4) ----------------------------------
    # GenAI EXPLAINS decisions only — it never makes or changes them. The
    # provider is selected by name so openai/gemini/mock can be swapped
    # without touching the recovery path. "mock" needs no key and is the
    # safe local default; production must set AI_PROVIDER=openai.
    ai_provider: str = "mock"  # mock | openai
    openai_api_key: str = ""  # server-side only — never sent to any client
    openai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 12.0  # hard cap so explanation can never hang a request

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def uses_dev_api_keys(self) -> bool:
        # ALL four keys must be replaced before production — a leftover dev
        # support/customer key would still grant data read access
        return any(
            k.startswith("dev-")
            for k in (
                self.api_key_system,
                self.api_key_admin,
                self.api_key_support,
                self.api_key_customer,
            )
        )

    def recovery_policy_snapshot(self) -> dict:
        """The exact policy parameters in force — stored on each decision
        so past decisions stay auditable even after thresholds change."""
        return {
            "min_safe_probability": self.recovery_min_safe_probability,
            "max_amount": self.recovery_max_amount,
            "max_previous_failures": self.recovery_max_previous_failures,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
