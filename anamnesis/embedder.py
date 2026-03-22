"""Embedding interface for Anamnesis.

Providers (in order of preference):
1. Voyage AI (Anthropic ecosystem) — best quality, requires VOYAGE_API_KEY
2. Local sentence-transformers — no API key, runs on device
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from anamnesis.config import EmbeddingConfig

logger = logging.getLogger("anamnesis.embedder")


class BaseEmbedder(ABC):
    """Abstract embedding interface."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return embedding dimensions."""


class VoyageEmbedder(BaseEmbedder):
    """Voyage AI embedder (Anthropic ecosystem)."""

    def __init__(self, config: EmbeddingConfig):
        import voyageai
        self._client = voyageai.AsyncClient(api_key=config.voyage_api_key)
        self._model = config.model
        self._dims = config.dimensions
        self._last_call = 0.0
        self._min_interval = 0.1  # Standard rate limits with payment method
        logger.info("Voyage embedder initialized: model=%s, dims=%d", self._model, self._dims)

    async def _rate_limit(self):
        """Respect Voyage's rate limits (3 RPM without payment method)."""
        import asyncio
        import time
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            logger.debug("Rate limiting: waiting %.1fs", wait)
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def embed(self, text: str) -> list[float]:
        await self._rate_limit()
        result = await self._client.embed(
            texts=[text],
            model=self._model,
            input_type="document",
        )
        return result.embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Batch all texts in a single API call to minimize rate limit impact
        await self._rate_limit()
        result = await self._client.embed(
            texts=texts,
            model=self._model,
            input_type="document",
        )
        return result.embeddings

    @property
    def dimensions(self) -> int:
        return self._dims


class LocalEmbedder(BaseEmbedder):
    """Local sentence-transformers embedder — no API key required."""

    def __init__(self, config: EmbeddingConfig):
        from sentence_transformers import SentenceTransformer
        model_name = config.model or "all-MiniLM-L6-v2"
        self._model = SentenceTransformer(model_name)
        self._dims = self._model.get_sentence_embedding_dimension()
        logger.info("Local embedder initialized: model=%s, dims=%d", model_name, self._dims)

    async def embed(self, text: str) -> list[float]:
        embedding = self._model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    @property
    def dimensions(self) -> int:
        return self._dims


def create_embedder(config: EmbeddingConfig) -> BaseEmbedder:
    """Factory to create the configured embedder.

    Falls back gracefully:
    1. If provider=voyage and VOYAGE_API_KEY set → VoyageEmbedder
    2. If provider=local → LocalEmbedder (sentence-transformers)
    3. If voyage key missing → auto-fallback to local
    """
    if config.provider == "voyage":
        if config.voyage_api_key:
            return VoyageEmbedder(config)
        else:
            logger.warning(
                "VOYAGE_API_KEY not set, falling back to local embedder. "
                "Set VOYAGE_API_KEY for production quality."
            )
            config.provider = "local"
            config.model = "all-MiniLM-L6-v2"

    if config.provider == "local":
        return LocalEmbedder(config)

    raise ValueError(
        f"Unknown embedding provider: {config.provider}. Supported: voyage, local"
    )
