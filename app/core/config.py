from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "document-extraction-api"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/document_api"
    storage_backend: Literal["local"] = "local"
    storage_path: Path = Path("./storage")
    max_pdf_size_mb: int = Field(50, gt=0)
    max_pdf_pages: int = Field(500, gt=0)
    max_selected_pages: int = Field(500, gt=0)
    upload_chunk_size_bytes: int = Field(1_048_576, ge=4096)
    default_extraction_engine: Literal["auto", "native", "marker"] = "auto"
    pdf_native_min_chars_per_page: int = Field(80, ge=0)
    pdf_native_min_words_per_page: int = Field(5, ge=0)
    pdf_native_max_invalid_char_ratio: float = Field(0.10, ge=0, le=1)
    pdf_native_min_text_page_ratio: float = Field(0.70, ge=0, le=1)
    ocr_dpi: int = Field(144, ge=72, le=600)
    ocr_default_language: str = "por"
    ocr_max_concurrency: int = Field(1, ge=1)
    marker_font_path: Path = Path("./tmp/marker/GoNotoCurrent-Regular.ttf")
    easyocr_model_path: Path = Path("./tmp/easyocr-models")
    max_images_per_document: int = Field(500, ge=0)
    ignore_repeated_images: bool = True
    association_min_confidence: float = Field(0.50, ge=0, le=1)
    association_strong_confidence: float = Field(0.75, ge=0, le=1)
    association_ambiguity_margin: float = Field(0.08, ge=0, le=1)
    association_max_candidates: int = Field(3, ge=1, le=10)
    association_region_weight: float = Field(0.42, ge=0, le=1)
    association_column_weight: float = Field(0.22, ge=0, le=1)
    association_row_weight: float = Field(0.10, ge=0, le=1)
    association_proximity_weight: float = Field(0.28, ge=0, le=1)
    association_code_weight: float = Field(0.08, ge=0, le=1)
    association_code_patterns: list[str] = [
        r"\b[A-Z]{1,6}[- ]?\d{2,6}\b",
        r"\b[A-Z]{1,6}\d{2,6}\b",
    ]
    extraction_worker_poll_seconds: float = Field(2, gt=0)
    extraction_max_attempts: int = Field(3, ge=1)
    extraction_timeout_seconds: int = Field(900, gt=0)
    extraction_job_ttl_seconds: int = Field(3600, ge=60)
    extraction_cleanup_interval_seconds: float = Field(60, gt=0)
    extraction_sse_heartbeat_seconds: float = Field(15, gt=0)
    extraction_sse_timeout_seconds: int = Field(600, gt=0)
    structuring_provider: Literal["openai"] = "openai"
    structuring_model: str = "gpt-5.6-sol"
    openai_api_key: SecretStr | None = None
    structuring_api_key: SecretStr | None = None
    structuring_base_url: str = "https://api.openai.com/v1"
    structuring_api_mode: Literal["responses", "chat_completions"] = "responses"
    structuring_timeout_seconds: int = Field(120, gt=0)
    structuring_max_attempts: int = Field(3, ge=1)
    structuring_worker_poll_seconds: float = Field(2, gt=0)
    order_structuring_prompt_version: str = "1.0.0"
    order_structuring_schema_version: str = "1.0.0"
    structuring_auto_approve_min_confidence: float = Field(0.90, ge=0, le=1)
    base44_api_base_url: str = "https://inalluminio.base44.app/api"
    base44_api_key: SecretStr | None = None
    base44_itens_pedido_path: str = "/entities/ItensPedido/bulk"
    base44_plano_corte_path: str = "/entities/PlanoCorte"
    base44_request_timeout_seconds: float = Field(30, gt=0)
    base44_max_retries: int = Field(3, ge=1, le=10)
    persistence_api_key: SecretStr | None = None
    max_structured_items: int = Field(1000, ge=1)
    delete_physical_file: bool = False
    cors_allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    cors_allow_credentials: bool = True
    cors_allowed_methods: Annotated[list[str], NoDecode] = [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    cors_allowed_headers: Annotated[list[str], NoDecode] = [
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Request-ID",
        "Idempotency-Key",
    ]
    cors_expose_headers: Annotated[list[str], NoDecode] = ["X-Request-ID", "Content-Disposition"]
    cors_max_age: int = Field(3600, ge=0)

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Use Railway's PostgreSQL URL with SQLAlchemy's asyncpg driver."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator(
        "cors_allowed_origins",
        "cors_allowed_methods",
        "cors_allowed_headers",
        "cors_expose_headers",
        mode="before",
    )
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def validate_cors(self) -> "Settings":
        if self.cors_allow_credentials and "*" in self.cors_allowed_origins:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS não pode conter * quando credenciais estão ativas"
            )
        return self

    @property
    def max_pdf_size_bytes(self) -> int:
        return self.max_pdf_size_mb * 1024 * 1024

    @property
    def effective_structuring_api_key(self) -> SecretStr | None:
        return self.structuring_api_key or self.openai_api_key

    @property
    def effective_structuring_base_url(self) -> str:
        base = self.structuring_base_url.rstrip("/")
        return base if base.endswith("/v1") else f"{base}/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
