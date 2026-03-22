"""Configuration for Anamnesis memory engine."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass
class DatabaseConfig:
    host: str = field(default_factory=lambda: _optional("ANAMNESIS_DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(_optional("ANAMNESIS_DB_PORT", "5432")))
    name: str = field(default_factory=lambda: _optional("ANAMNESIS_DB_NAME", "anamnesis"))
    user: str = field(default_factory=lambda: _optional("ANAMNESIS_DB_USER", "anamnesis"))
    password: str = field(default_factory=lambda: _optional("ANAMNESIS_DB_PASSWORD", "anamnesis_dev"))

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def async_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass
class EmbeddingConfig:
    provider: str = field(default_factory=lambda: _optional("ANAMNESIS_EMBEDDING_PROVIDER", "voyage"))
    model: str = field(default_factory=lambda: _optional("ANAMNESIS_EMBEDDING_MODEL", "voyage-3-lite"))
    voyage_api_key: str = field(default_factory=lambda: _optional("VOYAGE_API_KEY"))
    dimensions: int = field(default_factory=lambda: int(_optional("ANAMNESIS_EMBEDDING_DIMS", "512")))

    @property
    def is_configured(self) -> bool:
        if self.provider == "voyage":
            return bool(self.voyage_api_key)
        return True


@dataclass
class LLMConfig:
    """Config for the cheap/fast LLM used for fact extraction and reflect."""
    provider: str = field(default_factory=lambda: _optional("AI_PROVIDER", "claude"))
    reflect_model: str = field(default_factory=lambda: _optional("ANAMNESIS_REFLECT_MODEL", "claude-haiku-4-5-20251001"))
    anthropic_api_key: str = field(default_factory=lambda: _optional("ANTHROPIC_API_KEY"))

    @property
    def is_configured(self) -> bool:
        if self.provider in ("claude", "anthropic"):
            return bool(self.anthropic_api_key)
        return True


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: _optional("ANAMNESIS_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_optional("ANAMNESIS_PORT", "8400")))
    api_key: str = field(default_factory=lambda: _optional("ANAMNESIS_API_KEY", ""))
    debug: bool = field(default_factory=lambda: _optional("ANAMNESIS_DEBUG", "false").lower() == "true")


@dataclass
class AnamnesisConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
