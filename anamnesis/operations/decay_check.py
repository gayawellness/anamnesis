"""decay_check() — Evaluate decay conditions and archive stale memories."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from anamnesis.db import Database
from anamnesis.models import DecayCheckRequest, DecayCheckResponse

logger = logging.getLogger("anamnesis.decay_check")


async def decay_check(db: Database, request: DecayCheckRequest) -> DecayCheckResponse:
    """Evaluate decay conditions for all active memories in a bank.

    Supported decay conditions:
    - "after:Nd" — decay after N days from creation
    - "after:Nw" — decay after N weeks from creation
    - "when:superseded" — decay when superseded_by is set
    - "when:unaccessed:Nd" — decay if not accessed in N days
    - "never" — never decay
    """
    bank = await db.get_bank_by_name(request.bank)
    if not bank:
        raise ValueError(f"Memory bank not found: {request.bank}")

    memories = await db.get_decayable_memories(str(bank["id"]))
    now = datetime.now(timezone.utc)

    decayed_ids = []

    for mem in memories:
        condition = mem.get("decay_condition", "")
        if not condition or condition == "never":
            continue

        should_decay = _evaluate_condition(condition, mem, now)
        if should_decay:
            mem_id = str(mem["id"])
            await db.update_memory_status(mem_id, "decayed")
            decayed_ids.append(mem_id)

    if decayed_ids:
        logger.info(
            "Decay check on bank %s: %d memories decayed",
            request.bank, len(decayed_ids),
        )
    else:
        logger.info("Decay check on bank %s: no memories decayed", request.bank)

    return DecayCheckResponse(
        decayed_count=len(decayed_ids),
        decayed_ids=decayed_ids,
    )


def _evaluate_condition(condition: str, memory: dict, now: datetime) -> bool:
    """Evaluate a single decay condition."""
    # after:Nd or after:Nw
    after_match = re.match(r"after:(\d+)([dw])", condition)
    if after_match:
        amount = int(after_match.group(1))
        unit = after_match.group(2)
        days = amount if unit == "d" else amount * 7
        created = memory["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).total_seconds() / 86400
        return age_days >= days

    # when:superseded
    if condition == "when:superseded":
        return memory.get("superseded_by") is not None

    # when:unaccessed:Nd
    unaccessed_match = re.match(r"when:unaccessed:(\d+)d", condition)
    if unaccessed_match:
        days = int(unaccessed_match.group(1))
        last_accessed = memory.get("last_accessed_at", memory["created_at"])
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        idle_days = (now - last_accessed).total_seconds() / 86400
        return idle_days >= days

    logger.warning("Unknown decay condition: %s", condition)
    return False
