"""Application configuration."""

from __future__ import annotations

import logging
import warnings

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = {"env_prefix": "AUTOCORRECTOR_", "env_file": ".env", "extra": "ignore"}

    # Server
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = False

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = ""
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    default_llm: str = "claude-sonnet-5-5"
    correction_llm: str = "claude-opus-5-5"

    # Correction pipeline
    max_correction_attempts: int = Field(default=3, ge=1)
    # Reported for every modified output: a fixed heuristic, not a calibrated probability
    correction_confidence_score: float = Field(default=0.92, ge=0.0, le=1.0)

    # Audit
    audit_db_path: str = "autocorrector_audit.db"

    # MCP Auth
    mcp_api_key: str = ""

    # Dashboard Auth
    dashboard_api_key: str = ""

    # Embeddings
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Guardrails AI hub telemetry (OTLP export to the vendor); off unless opted in
    guardrails_telemetry: bool = False

    # NeMo Guardrails
    nemo_rails_enabled: bool = False
    nemo_config_path: str = "configs/nemo"

    # ROI / Analytics
    cost_per_violation: float = 15.0
    cost_per_escalation: float = 25.0

    # Rate limiting
    rate_limit_default: str = "60/minute"

    # CORS
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Multilingual
    supported_languages: list[str] = Field(
        default=["en", "es", "fr", "pt", "de", "it", "nl", "ar", "zh", "ja", "ko", "ru"]
    )
    default_language: str = "auto"

    @model_validator(mode="after")
    def _warn_missing_neo4j_credentials(self) -> Settings:
        if (
            self.neo4j_uri
            and not self.neo4j_uri.startswith("bolt://localhost")
            and (not self.neo4j_user or not self.neo4j_password)
        ):
            warnings.warn(
                "Neo4j URI is configured but credentials are empty. "
                "Set AUTOCORRECTOR_NEO4J_USER and AUTOCORRECTOR_NEO4J_PASSWORD.",
                stacklevel=2,
            )
        return self


def get_settings() -> Settings:
    return Settings()
