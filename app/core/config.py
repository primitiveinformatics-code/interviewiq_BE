import json
from typing import Any, List, Tuple, Type

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, DotEnvSettingsSource, EnvSettingsSource, PydanticBaseSettingsSource


class _CommaSepEnvSource(EnvSettingsSource):
    """EnvSettingsSource that falls back to comma-splitting for List fields.

    Newer pydantic-settings wraps JSONDecodeError in SettingsError before it
    reaches our except clause, so we bypass super() and parse JSON directly.
    """

    def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            if isinstance(value, str):
                return [i.strip() for i in value.split(",") if i.strip()]
            raise


class _CommaSepDotEnvSource(DotEnvSettingsSource):
    """DotEnvSettingsSource that falls back to comma-splitting for List fields."""

    def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            if isinstance(value, str):
                return [i.strip() for i in value.split(",") if i.strip()]
            raise


class Settings(BaseSettings):
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    # ENCRYPTION_SALT: generate once with:
    #   python -c "import os,base64; print(base64.b64encode(os.urandom(16)).decode())"
    # Leave empty in dev — falls back to first 16 chars of SECRET_KEY (not for prod).
    ENCRYPTION_SALT: str = ""
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8501"]

    DATABASE_URL: str = "postgresql+asyncpg://interviewiq:password@localhost:5432/interviewiq"
    SYNC_DATABASE_URL: str = "postgresql://interviewiq:password@localhost:5432/interviewiq"

    JWT_SECRET: str = "your-jwt-secret-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    REMEMBER_ME_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Service URLs ──────────────────────────────────────────────────────
    FRONTEND_URL: str = "http://localhost:3000"
    BACKEND_URL:  str = "http://localhost:8000"

    # ── OAuth credentials (Google + GitHub direct OAuth, no Keycloak) ─────
    GOOGLE_CLIENT_ID:     str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GITHUB_CLIENT_ID:     str = ""
    GITHUB_CLIENT_SECRET: str = ""

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""

    # ── Hugging Face (faster-whisper model downloads) ──────────────────────
    # Get a free token at: https://huggingface.co/settings/tokens
    # Enables higher rate limits and avoids anonymous download throttling.
    HF_TOKEN: str = ""

    # ── OpenRouter (free tier for local dev) ──────────────────────────────
    # Get key at: https://openrouter.ai/keys  (free, no credit card needed)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    COHERE_API_KEY: str = ""

    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"

    # ── Per-agent model selection ──────────────────────────────────────────
    # Cloud models: "claude-sonnet-4-6", "gpt-4o", "gemini-1.5-flash", etc.
    # Local Ollama:  "ollama/mistral", "ollama/llama3", etc.
    INTERVIEWER_MODEL:  str = "claude-sonnet-4-6"
    EVALUATOR_MODEL:    str = "gpt-4o"
    PARSER_MODEL:       str = "gemini-1.5-flash"
    FOLLOWUP_MODEL:     str = "claude-haiku-4-5-20251001"
    REPORT_MODEL:       str = "claude-haiku-4-5-20251001"
    EMBEDDINGS_MODEL:   str = "cohere/embed-english-v3.0"

    # ── Razorpay (one-time credit packs) ──────────────────────────────────
    RAZORPAY_KEY_ID:             str = ""
    RAZORPAY_KEY_SECRET:         str = ""
    RAZORPAY_WEBHOOK_SECRET:     str = ""  # optional — for async payment.captured webhook
    # Prices in paise (INR × 100) or smallest currency unit
    # Override in .env to change pricing
    RAZORPAY_1_CREDIT_AMOUNT:    int = 70000   # ₹700
    RAZORPAY_5_CREDIT_AMOUNT:    int = 299000  # ₹2,990
    RAZORPAY_10_CREDIT_AMOUNT:   int = 499000  # ₹4,990
    RAZORPAY_CURRENCY:           str = "INR"

    # ── Redis (interview session state) ───────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Admin access ──────────────────────────────────────────────────────
    # Email-based admin (no UUID lookup needed). Comma-separated in .env.
    # e.g. ADMIN_EMAILS=nipin88832@gmail.com,other@example.com
    ADMIN_EMAILS: List[str] = []

    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    KEYCLOAK_SERVER_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "interviewiq"
    KEYCLOAK_CLIENT_ID: str = "interviewiq-app"

    K8S_NAMESPACE: str = "interviewiq"
    SESSION_POD_IMAGE: str = "interviewiq/agent:latest"

    LANGCHAIN_TRACING_V2: str = "true"
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "interviewiq"

    # Users allowed to access /llmtest and /admin/corpus/ingest endpoints.
    # Comma-separated UUIDs in .env:  ADMIN_USER_IDS=uuid1,uuid2
    ADMIN_USER_IDS: List[str] = []

    # ── Production safety validators ──────────────────────────────────────────
    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_changed(cls, v: str, info) -> str:
        import os
        if os.getenv("APP_ENV", "development") == "production" and v == "change-me-in-production":
            raise ValueError(
                "SECRET_KEY must be changed before running in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return v

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_must_be_changed(cls, v: str, info) -> str:
        import os
        if os.getenv("APP_ENV", "development") == "production" and v == "your-jwt-secret-change-in-prod":
            raise ValueError(
                "JWT_SECRET must be changed before running in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        env_file = settings_cls.model_config.get("env_file", ".env")
        return (
            init_settings,
            _CommaSepEnvSource(settings_cls),  # Use custom env source for Railway env vars
            _CommaSepDotEnvSource(settings_cls, env_file=env_file),
            file_secret_settings,
        )

    class Config:
        env_file = ".env"


settings = Settings()
