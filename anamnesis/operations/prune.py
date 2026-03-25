"""prune() — Find and archive stale, decayed, or superseded memories."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from anamnesis.db import Database
from anamnesis.models import PruneRequest, PruneResponse

logger = logging.getLogger("anamnesis.prune")

# Thresholds
WEIGHT_FLOOR = 0.5
UNACCESSED_DAYS = 90
SUPERSEDED_AGE_DAYS = 30


async def prune(db: Database, request: PruneRequest) -> PruneResponse:
    """Identify and optionally archive memories that are candidates for pruning.

    A memory is a prune candidate if ANY of these conditions hold:
    1. status != 'active' (already deactivated by decay_check — decayed/superseded)
    2. Weight below 0.5 AND not accessed in 90 days
    3. Superseded by another memory AND older than 30 days

    Args:
        db: Database instance.
        request: PruneRequest with bank name and dry_run flag.

    Returns:
        PruneResponse with candidate list and archived count.
    """
    bank = await db.get_bank_by_name(request.bank)
    if not bank:
        raise ValueError(f"Memory bank not found: {request.bank}")

    bank_id = str(bank["id"])
    now = datetime.now(timezone.utc)
    candidates = await _find_prune_candidates(db, bank_id, now)

    archived_ids = []

    if not request.dry_run and candidates:
        archived_ids = [c["id"] for c in candidates]
        await db.batch_archive_memories(archived_ids)
        logger.info(
            "Pruned bank %s: %d memories archived",
            request.bank, len(archived_ids),
        )
    else:
        logger.info(
            "Prune %s on bank %s: %d candidates found",
            "dry-run" if request.dry_run else "check",
            request.bank,
            len(candidates),
        )

    return PruneResponse(
        candidates=[
            {
                "id": c["id"],
                "content": c["content"][:200],
                "reason": c["reason"],
                "weight": c["weight"],
                "status": c["status"],
                "last_accessed_at": c["last_accessed_at"],
            }
            for c in candidates
        ],
        archived_count=len(archived_ids),
        dry_run=request.dry_run,
    )


async def restore_memory(db: Database, memory_id: str) -> dict:
    """Restore an archived memory back to active status.

    Args:
        db: Database instance.
        memory_id: UUID of the memory to restore.

    Returns:
        Dict with memory_id and new status.

    Raises:
        ValueError: If memory not found or not in archived status.
    """
    memory = await db.get_memory(memory_id)
    if not memory:
        raise ValueError(f"Memory not found: {memory_id}")

    if memory["status"] != "archived":
        raise ValueError(
            f"Memory {memory_id} is not archived (current status: {memory['status']}). "
            "Only archived memories can be restored."
        )

    await db.update_memory_status(memory_id, "active")
    logger.info("Restored memory %s to active", memory_id)

    return {
        "memory_id": memory_id,
        "status": "active",
        "content": memory["content"][:200],
    }


async def _find_prune_candidates(
    db: Database, bank_id: str, now: datetime
) -> list[dict]:
    """Query for all prune candidates in a bank.

    Returns list of dicts with id, content, reason, weight, status, last_accessed_at.
    """
    candidates = []
    seen_ids = set()

    # Condition 1: status != 'active' (decayed or superseded, but NOT already archived)
    non_active = await db.get_non_active_memories(bank_id)
    for mem in non_active:
        mid = str(mem["id"])
        if mid not in seen_ids:
            seen_ids.add(mid)
            candidates.append({
                "id": mid,
                "content": mem["content"],
                "reason": f"status is '{mem['status']}' (not active)",
                "weight": mem["weight"],
                "status": mem["status"],
                "last_accessed_at": mem.get("last_accessed_at"),
            })

    # Condition 2: Weight below threshold AND not accessed in 90 days
    stale = await db.get_low_weight_stale_memories(
        bank_id, WEIGHT_FLOOR, UNACCESSED_DAYS
    )
    for mem in stale:
        mid = str(mem["id"])
        if mid not in seen_ids:
            seen_ids.add(mid)
            last_acc = mem.get("last_accessed_at", mem["created_at"])
            if last_acc and last_acc.tzinfo is None:
                last_acc = last_acc.replace(tzinfo=timezone.utc)
            idle_days = (now - last_acc).total_seconds() / 86400
            candidates.append({
                "id": mid,
                "content": mem["content"],
                "reason": (
                    f"weight {mem['weight']:.2f} < {WEIGHT_FLOOR} "
                    f"and not accessed in {idle_days:.0f} days"
                ),
                "weight": mem["weight"],
                "status": mem["status"],
                "last_accessed_at": mem.get("last_accessed_at"),
            })

    # Condition 3: Superseded by another memory AND older than 30 days
    superseded = await db.get_old_superseded_memories(bank_id, SUPERSEDED_AGE_DAYS)
    for mem in superseded:
        mid = str(mem["id"])
        if mid not in seen_ids:
            seen_ids.add(mid)
            created = mem["created_at"]
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = (now - created).total_seconds() / 86400
            candidates.append({
                "id": mid,
                "content": mem["content"],
                "reason": (
                    f"superseded by {mem['superseded_by']} "
                    f"and {age_days:.0f} days old"
                ),
                "weight": mem["weight"],
                "status": mem["status"],
                "last_accessed_at": mem.get("last_accessed_at"),
            })

    return candidates
