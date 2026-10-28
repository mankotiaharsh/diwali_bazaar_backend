"""All settings come from environment variables (or the .env file in development)."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Diwali Bazaar API"
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"

    # SQLite locally; use a Postgres URL (e.g. Supabase / Neon) in production.
    database_url: str = "sqlite+aiosqlite:///./diwali.db"

    # Where the React app lives — used for CORS and the post-payment return URL.
    frontend_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Public https URL of this API. When set, Cashfree sends payment webhooks here.
    backend_public_url: str = ""

    cashfree_app_id: str = ""
    cashfree_secret_key: SecretStr = SecretStr("")
    cashfree_env: Literal["sandbox", "production"] = "sandbox"
    cashfree_api_version: str = "2023-08-01"

    # Shagun limits are enforced here on the server. SHAGUN_AMOUNT is the minimum (and the default);
    # the sender may choose more, up to SHAGUN_MAX_AMOUNT. Whole rupees only.
    shagun_amount: Decimal = Field(default=Decimal("51"), ge=1)
    shagun_max_amount: Decimal = Field(default=Decimal("10000"), ge=1)
    # Quick-pick amounts shown in the app (comma-separated rupees).
    shagun_presets: str = "51,101,251,501,1100"
    # Every order id starts with this, so a Cashfree account shared with other projects stays separable.
    order_prefix: str = Field(default="diwali", pattern=r"^[a-z0-9]{2,12}$")
    order_expiry_minutes: int = Field(default=30, ge=15, le=1440)

    # Receipt emails via Resend (https://resend.com). Without an API key, receipts are only logged.
    resend_api_key: SecretStr = SecretStr("")
    email_from: str = "Diwali Bazaar <onboarding@resend.dev>"
    email_reply_to: str = ""
    invoice_prefix: str = Field(default="DB", pattern=r"^[A-Z0-9]{1,8}$")

    @model_validator(mode="after")
    def _check_limits(self) -> "Settings":
        if self.shagun_max_amount < self.shagun_amount:
            raise ValueError("SHAGUN_MAX_AMOUNT must be at least SHAGUN_AMOUNT")
        return self

    @field_validator("frontend_base_url", "backend_public_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @field_validator("database_url")
    @classmethod
    def _use_async_driver(cls, v: str) -> str:
        v = v.strip()
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cashfree_enabled(self) -> bool:
        return bool(self.cashfree_app_id and self.cashfree_secret_key.get_secret_value())

    @property
    def cashfree_base_url(self) -> str:
        if self.cashfree_env == "production":
            return "https://api.cashfree.com/pg"
        return "https://sandbox.cashfree.com/pg"

    @property
    def min_amount(self) -> int:
        return int(self.shagun_amount)

    @property
    def max_amount(self) -> int:
        return int(self.shagun_max_amount)

    @property
    def preset_amounts(self) -> list[int]:
        values = []
        for part in self.shagun_presets.split(","):
            if part.strip().isdigit() and self.min_amount <= int(part) <= self.max_amount:
                values.append(int(part))
        return sorted(set(values)) or [self.min_amount]

    @property
    def emails_enabled(self) -> bool:
        return bool(self.resend_api_key.get_secret_value())

    @property
    def payment_return_url(self) -> str:
        # Cashfree replaces {order_id} itself when it redirects back.
        return f"{self.frontend_base_url}/payment/return?order_id={{order_id}}"

    def config_warnings(self) -> list[str]:
        """Setup problems worth fixing. Never includes secret values, so it's safe to show on /health."""
        warnings = []
        live = self.cashfree_env == "production"
        if not self.cashfree_enabled:
            warnings.append("CASHFREE_APP_ID / CASHFREE_SECRET_KEY are not set — payments are off.")
        if live and not self.frontend_base_url.startswith("https://"):
            warnings.append(
                f"FRONTEND_BASE_URL is '{self.frontend_base_url}'. Cashfree production only accepts https return URLs, "
                "so every checkout fails. Set it to your live site, e.g. https://diwali-bazaar.vercel.app"
            )
        if self.frontend_base_url not in self.cors_origin_list:
            warnings.append(f"FRONTEND_BASE_URL '{self.frontend_base_url}' is not in CORS_ORIGINS.")
        if live and not self.payment_notify_url:
            warnings.append("BACKEND_PUBLIC_URL is not an https URL — Cashfree webhooks are off.")
        if live and self.database_url.startswith("sqlite"):
            warnings.append("DATABASE_URL is SQLite — on most hosts it is wiped on redeploy. Use Postgres.")
        if live and self.app_env != "production":
            warnings.append("APP_ENV is not 'production' while taking real payments (API docs are public).")
        if not self.emails_enabled:
            warnings.append("RESEND_API_KEY is not set — receipt emails are only logged.")
        return warnings

    @property
    def payment_notify_url(self) -> str | None:
        # Cashfree only accepts https webhook URLs, so skip it for local http development.
        if self.backend_public_url.startswith("https://"):
            return f"{self.backend_public_url}/api/webhooks/cashfree"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
