"""recall() — 4D parallel retrieval with reciprocal rank fusion."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from anamnesis.db import Database
from anamnesis.embedder import BaseEmbedder
from anamnesis.models import (
    DimensionScores,
    RecalledMemory,
    RecallFilters,
    RecallRequest,
    RecallResponse,
)

logger = logging.getLogger("anamnesis.recall")

# RRF constant (standard value)
RRF_K = 60


async def recall(
    db: Database,
    embedder: BaseEmbedder,
    request: RecallRequest,
) -> RecallResponse:
    """Retrieve memories using 4D parallel retrieval + reciprocal rank fusion.

    Pipeline:
    1. Embed the query
    2. Run 4 parallel retrieval strategies
    3. Apply filters
    4. Reciprocal Rank Fusion across channels
    5. Strategic weight boost
    6. Return top results with dimension breakdowns
    """
    start = time.monotonic()

    # Validate bank
    bank = await db.get_bank_by_name(request.bank)
    if not bank:
        raise ValueError(f"Memory bank not found: {request.bank}")
    bank_id = str(bank["id"])

    # Get dimension weights (from request override or bank config)
    import json
    weight_factors = request.dimension_weights or (
        json.loads(bank["weight_factors"])
        if isinstance(bank["weight_factors"], str)
        else bank["weight_factors"]
    )

    # Build filter dict for DB queries
    filters_dict = _build_filters(request.filters) if request.filters else None

    # 1. Embed the query
    query_embedding = await embedder.embed(request.query)

    # 2. Run 4 parallel retrieval strategies
    fetch_limit = max(request.limit * 5, 50)

    semantic_task = db.search_semantic(
        bank_id, query_embedding, limit=fetch_limit,
        filters=filters_dict,
    )
    fulltext_task = db.search_fulltext(
        bank_id, request.query, limit=fetch_limit,
        filters=filters_dict,
    )
    temporal_task = db.search_temporal(
        bank_id, limit=fetch_limit,
        filters=filters_dict,
    )

    # For relational: extract entity names from query and find matching entities
    relational_task = _relational_search(
        db, bank_id, request.query, fetch_limit
    )

    semantic_results, fulltext_results, temporal_results, relational_results = (
        await asyncio.gather(
            semantic_task, fulltext_task, temporal_task, relational_task,
            return_exceptions=True,
        )
    )

    # Handle any strategy failures gracefully
    if isinstance(semantic_results, Exception):
        logger.warning("Semantic search failed: %s", semantic_results)
        semantic_results = []
    if isinstance(fulltext_results, Exception):
        logger.warning("Full-text search failed: %s", fulltext_results)
        fulltext_results = []
    if isinstance(temporal_results, Exception):
        logger.warning("Temporal search failed: %s", temporal_results)
        temporal_results = []
    if isinstance(relational_results, Exception):
        logger.warning("Relational search failed: %s", relational_results)
        relational_results = []

    # 3. Reciprocal Rank Fusion
    all_memories: dict[str, dict] = {}  # memory_id -> memory row
    dimension_ranks: dict[str, DimensionScores] = {}  # memory_id -> per-dim scores

    # Collect all unique memories
    for results in [semantic_results, fulltext_results, temporal_results, relational_results]:
        for row in results:
            mid = str(row["id"])
            if mid not in all_memories:
                all_memories[mid] = row
                dimension_ranks[mid] = DimensionScores()

    total_candidates = len(all_memories)

    # Assign RRF scores per dimension
    _assign_rrf_scores(semantic_results, dimension_ranks, "semantic",
                       weight_factors.get("semantic", 0.3))
    _assign_rrf_scores(fulltext_results, dimension_ranks, "semantic",
                       weight_factors.get("semantic", 0.3) * 0.5)  # FT supplements semantic
    _assign_rrf_scores(temporal_results, dimension_ranks, "temporal",
                       weight_factors.get("temporal", 0.2))
    _assign_rrf_scores(relational_results, dimension_ranks, "relational",
                       weight_factors.get("relational", 0.2))

    # 4. Strategic weight boost (Dimension 4)
    strategic_weight_factor = weight_factors.get("strategic", 0.3)
    fused_scores: dict[str, float] = {}
    for mid, dim_scores in dimension_ranks.items():
        rrf_sum = dim_scores.semantic + dim_scores.temporal + dim_scores.relational
        memory_weight = all_memories[mid].get("weight", 1.0)
        strategic_score = (memory_weight / 10.0) * strategic_weight_factor
        dim_scores.strategic = round(strategic_score, 4)
        fused_scores[mid] = rrf_sum + strategic_score

    # 5. Sort by fused score, take top N
    sorted_ids = sorted(fused_scores, key=lambda x: fused_scores[x], reverse=True)
    top_ids = sorted_ids[:request.limit]

    # 6. Build response
    memories = []
    for mid in top_ids:
        row = all_memories[mid]
        memories.append(RecalledMemory(
            id=mid,
            content=row["content"],
            content_type=row["content_type"],
            score=round(fused_scores[mid], 4),
            dimension_scores=dimension_ranks[mid],
            weight=row.get("weight", 1.0),
            confidence=row.get("confidence", 0.8),
            reasoning=row.get("reasoning"),
            authority=row.get("authority", "inferred"),
            source=row.get("source", "unknown"),
            tags=row.get("tags", []),
            created_at=row["created_at"],
            last_accessed_at=row.get("last_accessed_at", row["created_at"]),
        ))

    # Record access
    if top_ids:
        await db.record_access(top_ids, "recall", request.query)

    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "Recalled %d memories from %d candidates in bank %s (%.0fms)",
        len(memories), total_candidates, request.bank, elapsed,
    )

    return RecallResponse(
        memories=memories,
        total_candidates=total_candidates,
        retrieval_time_ms=round(elapsed, 1),
    )


def _assign_rrf_scores(results: list[dict], dimension_ranks: dict[str, DimensionScores],
                       dimension: str, weight: float):
    """Assign weighted RRF scores for a retrieval channel."""
    for rank, row in enumerate(results):
        mid = str(row["id"])
        if mid in dimension_ranks:
            rrf_score = weight / (RRF_K + rank + 1)
            current = getattr(dimension_ranks[mid], dimension)
            setattr(dimension_ranks[mid], dimension, round(current + rrf_score, 6))


async def _relational_search(db: Database, bank_id: str, query: str,
                             limit: int) -> list[dict]:
    """Find memories connected to entities mentioned in the query."""
    # Simple entity extraction from query: split into significant words
    words = [w.strip(".,!?;:'\"()") for w in query.split() if len(w) > 3]
    if not words:
        return []

    # Find matching entities
    entities = await db.find_entities_by_names(bank_id, words)
    if not entities:
        return []

    entity_ids = [str(e["id"]) for e in entities]

    # Expand to connected entities (1-hop)
    connected_ids = await db.get_connected_entity_ids(entity_ids, depth=1)

    # Get memories linked to these entities
    return await db.search_by_entities(bank_id, connected_ids, limit=limit)


def _build_filters(filters: RecallFilters) -> dict:
    """Convert RecallFilters to a dict for DB queries."""
    d = {}
    if filters.content_types:
        d["content_types"] = [ct.value for ct in filters.content_types]
    if filters.min_weight is not None:
        d["min_weight"] = filters.min_weight
    if filters.tags:
        d["tags"] = filters.tags
    if filters.after:
        d["after"] = filters.after
    if filters.before:
        d["before"] = filters.before
    return d
