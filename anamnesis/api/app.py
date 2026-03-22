"""FastAPI application factory for Anamnesis."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anamnesis.config import AnamnesisConfig
from anamnesis.db import Database
from anamnesis.embedder import create_embedder
from anamnesis.llm import create_llm_client

logger = logging.getLogger("anamnesis.api")


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
        version="0.1.0",
        lifespan=lifespan,
    )

    from anamnesis.api.routes import router
    app.include_router(router, prefix="/api/v1")

    return app
