"""FastAPI application factory for Anamnesis."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anamnesis.config import AnamnesisConfig
from anamnesis.db import Database
from anamnesis.embedder import create_embedder
from anamnesis.llm import create_llm_client

logger = logging.getLogger("anamnesis.api")


async def _startup_checks(app: FastAPI) -> None:
    """Run non-blocking validation checks on startup.

    All checks log results but never prevent startup — the system should
    be usable even in a degraded state.
    """
    embedder = app.state.embedder
    db = app.state.db
    config = app.state.config

    # 1. Embedding provider check
    try:
        await embedder.embed("startup health check")
        logger.info(
            "Embedding provider '%s' is healthy",
            config.embedding.provider,
        )
    except Exception as e:
        logger.critical(
            "No working embedding provider. Semantic search will not work. "
            "Error: %s", e,
        )

    # 2. Missing embeddings check
    try:
        missing = await db.count_failed_embeddings()
        if missing > 0:
            logger.warning(
                "%d memories missing embeddings. "
                "Run: python3 -m anamnesis.cli repair-embeddings --bank <name>",
                missing,
            )
    except Exception as e:
        logger.warning("Could not check for missing embeddings: %s", e)

    # 3. Scoring normalization check (self-recall test)
    try:
        bank_count = await db.total_bank_count()
        if bank_count > 0:
            banks = await db.list_banks()
            for bank in banks:
                bank_id = str(bank["id"])
                # Get a random memory to self-test
                memories = await db.get_top_weighted_memories(bank_id, limit=1)
                if not memories:
                    continue

                test_mem = memories[0]
                content = test_mem["content"]
                query_embedding = await embedder.embed(content)
                results = await db.search_semantic(
                    bank_id, query_embedding, limit=5
                )
                if results:
                    top_id = str(results[0]["id"])
                    test_id = str(test_mem["id"])
                    if top_id == test_id:
                        logger.info(
                            "Scoring check passed for bank '%s': "
                            "self-recall returned correct memory at #1",
                            bank["name"],
                        )
                    else:
                        logger.warning(
                            "Scoring check warning for bank '%s': "
                            "self-recall did not return exact match at #1. "
                            "Top result was a different memory. "
                            "This may indicate embedding quality issues.",
                            bank["name"],
                        )
                break  # Only test first bank to keep startup fast
    except Exception as e:
        logger.warning("Scoring self-check failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    config = AnamnesisConfig()

    # Initialize embedder first to get actual dimensions
    embedder = create_embedder(config.embedding)
    app.state.embedder = embedder

    # Initialize database with embedder's actual dimensions
    db = Database(config.db, embedding_dims=embedder.dimensions)
    await db.connect()
    app.state.db = db

    # Initialize LLM client (optional, for fact extraction + reflect)
    app.state.llm_client = create_llm_client()

    app.state.config = config
    app.state.startup_time = time.monotonic()

    # Run startup validation checks (non-blocking, logs only)
    await _startup_checks(app)

    logger.info("Anamnesis started on port %d", config.server.port)
    yield

    # Shutdown
    await db.close()
    logger.info("Anamnesis shut down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Anamnesis",
        description="4D Strategic Memory Engine for Autonomous AI Agents",
        version="0.2.1",
        lifespan=lifespan,
    )

    from anamnesis.api.routes import router
    app.include_router(router, prefix="/api/v1")

    return app
