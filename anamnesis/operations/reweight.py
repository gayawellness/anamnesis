"""reweight() — Recalculate strategic weights across a memory bank."""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

from anamnesis.db import Database
from anamnesis.models import ReweightRequest, ReweightResponse

logger = logging.getLogger("anamnesis.reweight")

AUTHORITY_BASE = {
    "explicit": 2.0,
    "system": 1.5,
    "inferred": 1.0,
}


async def reweight(db: Database, request: ReweightRequest) -> ReweightResponse:
    """Recalculate strategic weights for all active memories in a bank.

    Weight formula:
        weight = authority_base * confidence * temporal_factor * connectivity_factor

    Where:
    - authority_base: explicit=2.0, system=1.5, inferred=1.0
    - temporal_factor: 0.5 + 0.5 * (1 / (1 + days_since_access/30))
    - connectivity_factor: 1.0 + 0.1 * min(entity_connections, 10)
    """
    bank = await db.get_bank_by_name(request.bank)
    if not bank:
        raise ValueError(f"Memory bank not found: {request.bank}")

    memories = await db.get_active_memories(str(bank["id"]))
    if not memories:
        return ReweightResponse(
            updated_count=0,
            weight_stats={"mean": 0, "median": 0, "min": 0, "max": 0},
        )

    now = datetime.now(timezone.utc)
    updates: list[tuple[str, float]] = []
    new_weights: list[float] = []

    for mem in memories:
        authority = mem.get("authority", "inferred")
        confidence = mem.get("confidence", 0.8)
        entity_count = mem.get("entity_count", 0)

        # Temporal factor
        last_accessed = mem.get("last_accessed_at", mem["created_at"])
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        days_since_access = max(0, (now - last_accessed).total_seconds() / 86400)
        temporal_factor = 0.5 + 0.5 * (1.0 / (1.0 + days_since_access / 30.0))

        # Connectivity factor
        connectivity_factor = 1.0 + 0.1 * min(entity_count, 10)

        # Authority base
        auth_base = AUTHORITY_BASE.get(authority, 1.0)

        # Compute weight
        weight = auth_base * confidence * temporal_factor * connectivity_factor
        weight = round(min(max(weight, 0.0), 10.0), 2)

        mem_id = str(mem["id"])
        updates.append((mem_id, weight))
        new_weights.append(weight)

    await db.batch_update_weights(updates)

    weight_stats = {
        "mean": round(statistics.mean(new_weights), 2),
        "median": round(statistics.median(new_weights), 2),
        "min": round(min(new_weights), 2),
        "max": round(max(new_weights), 2),
    }

    logger.info(
        "Reweighted %d memories in bank %s: mean=%.2f, range=[%.2f, %.2f]",
        len(updates), request.bank,
        weight_stats["mean"], weight_stats["min"], weight_stats["max"],
    )

    return ReweightResponse(
        updated_count=len(updates),
        weight_stats=weight_stats,
    )
