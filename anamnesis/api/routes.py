"""REST API routes for Anamnesis."""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request

from anamnesis.models import (
    BankCreate,
    BankResponse,
    BankUpdate,
    BootDecayAlert,
    BootOutcome,
    BootPriority,
    BootRequest,
    BootResponse,
    BulkRetainRequest,
    BulkRetainResponse,
    DecayCheckRequest,
    DecayCheckResponse,
    HealthResponse,
    RecallRequest,
    RecallResponse,
    ReflectRequest,
    ReflectResponse,
    RetainRequest,
    RetainResponse,
    PruneRequest,
    PruneResponse,
    RestoreResponse,
    ReweightRequest,
    ReweightResponse,
)
from anamnesis.operations.recall import recall
from anamnesis.operations.retain import retain

logger = logging.getLogger("anamnesis.routes")

router = APIRouter()

# Valid decay condition patterns (see SCHEMA.md for full documentation)
_VALID_DECAY_PATTERNS = [
    r"^after:\d+[dw]$",       # after:30d, after:4w
    r"^when:superseded$",      # when:superseded
    r"^when:unaccessed:\d+d$", # when:unaccessed:60d
    r"^never$",                # never
]


def _validate_retain_request(body: "RetainRequest") -> list[str]:
    """Validate a retain request and return a list of human-readable error messages.

    Returns an empty list if the request is valid. Soft hints (reasoning quality)
    are logged as warnings but do not block the request.
    """
    errors = []

    # Content validation
    if not body.content or not body.content.strip():
        errors.append(
            "Field 'content' is required and cannot be empty. "
            "Provide a natural-language statement of what was decided, learned, or observed."
        )
    elif len(body.content.strip()) < 10:
        errors.append(
            "Field 'content' is too short (minimum 10 characters). "
            "Memories should be meaningful statements, not fragments."
        )

    # Bank validation
    if not body.bank or not body.bank.strip():
        errors.append(
            "Field 'bank' is required. Specify the name of the target memory bank."
        )

    # Confidence validation
    if body.confidence < 0.0 or body.confidence > 1.0:
        errors.append(
            f"Field 'confidence' must be between 0.0 and 1.0 (got {body.confidence})."
        )

    # Decay condition validation
    if body.decay_condition is not None and body.decay_condition != "":
        if not any(re.match(p, body.decay_condition) for p in _VALID_DECAY_PATTERNS):
            errors.append(
                f"Invalid decay_condition: '{body.decay_condition}'. "
                "Valid formats: 'after:Nd', 'after:Nw', 'when:superseded', "
                "'when:unaccessed:Nd', 'never'. See SCHEMA.md for details."
            )

    # Reasoning quality hint (warning only, does not block request)
    if not body.reasoning and body.authority.value == "explicit":
        logger.warning(
            "Explicit-authority retain without reasoning — reflect quality will be reduced. "
            "See SCHEMA.md 'Reasoning Quality Guide'."
        )

    return errors


def _get_db(request: Request):
    return request.app.state.db


def _get_embedder(request: Request):
    return request.app.state.embedder


def _get_llm(request: Request):
    return request.app.state.llm_client


# ── Health ──

@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    db = _get_db(request)
    config = request.app.state.config
    db_ok = await db.is_healthy()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db_connected=db_ok,
        embedding_configured=config.embedding.is_configured,
        llm_configured=config.llm.is_configured,
        memory_count=await db.total_memory_count() if db_ok else 0,
        bank_count=await db.total_bank_count() if db_ok else 0,
    )


# ── Banks ──

@router.post("/banks", response_model=BankResponse)
async def create_bank(body: BankCreate, request: Request):
    db = _get_db(request)
    existing = await db.get_bank_by_name(body.name)
    if existing:
        raise HTTPException(400, f"Bank already exists: {body.name}")
    row = await db.create_bank(
        name=body.name,
        mission=body.mission,
        directives=body.directives,
        disposition=body.disposition,
        weight_factors=body.weight_factors,
        default_decay_days=body.default_decay_days,
        write_agents=body.write_agents,
    )
    return _bank_row_to_response(row)


@router.get("/banks", response_model=list[BankResponse])
async def list_banks(request: Request):
    db = _get_db(request)
    rows = await db.list_banks()
    results = []
    for row in rows:
        stats = await db.get_bank_stats(str(row["id"]))
        results.append(_bank_row_to_response(row, stats))
    return results


@router.get("/banks/{bank_id}", response_model=BankResponse)
async def get_bank(bank_id: str, request: Request):
    db = _get_db(request)
    row = await db.get_bank(bank_id)
    if not row:
        raise HTTPException(404, "Bank not found")
    stats = await db.get_bank_stats(bank_id)
    return _bank_row_to_response(row, stats)


@router.put("/banks/{bank_id}", response_model=BankResponse)
async def update_bank(bank_id: str, body: BankUpdate, request: Request):
    db = _get_db(request)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    row = await db.update_bank(bank_id, **updates)
    if not row:
        raise HTTPException(404, "Bank not found")
    stats = await db.get_bank_stats(bank_id)
    return _bank_row_to_response(row, stats)


# ── Core Operations ──

@router.post("/retain", response_model=RetainResponse)
async def retain_memory(body: RetainRequest, request: Request):
    # Inline validation with helpful error messages
    errors = _validate_retain_request(body)
    if errors:
        raise HTTPException(422, detail={"validation_errors": errors})

    db = _get_db(request)
    embedder = _get_embedder(request)
    llm = _get_llm(request)
    try:
        return await retain(db, embedder, body, llm_client=llm)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Retain failed: %s", e)
        raise HTTPException(500, f"Retain failed: {e}")


@router.post("/recall", response_model=RecallResponse)
async def recall_memories(body: RecallRequest, request: Request):
    db = _get_db(request)
    embedder = _get_embedder(request)
    try:
        return await recall(db, embedder, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Recall failed: %s", e)
        raise HTTPException(500, f"Recall failed: {e}")


@router.post("/reflect", response_model=ReflectResponse)
async def reflect_memories(body: ReflectRequest, request: Request):
    llm = _get_llm(request)
    if not llm:
        raise HTTPException(503, "LLM not configured — reflect requires an LLM")
    db = _get_db(request)
    embedder = _get_embedder(request)
    try:
        from anamnesis.operations.reflect import reflect
        return await reflect(db, embedder, llm, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Reflect failed: %s", e)
        raise HTTPException(500, f"Reflect failed: {e}")


@router.post("/decay-check", response_model=DecayCheckResponse)
async def decay_check(body: DecayCheckRequest, request: Request):
    db = _get_db(request)
    try:
        from anamnesis.operations.decay_check import decay_check as do_decay
        return await do_decay(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/reweight", response_model=ReweightResponse)
async def reweight(body: ReweightRequest, request: Request):
    db = _get_db(request)
    try:
        from anamnesis.operations.reweight import reweight as do_reweight
        return await do_reweight(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Prune & Restore ──

@router.post("/prune/{bank_name}", response_model=PruneResponse)
async def prune_bank(bank_name: str, body: PruneRequest, request: Request):
    """Identify and optionally archive stale, decayed, or superseded memories.

    Use dry_run=true (the default) to preview candidates without archiving.
    Set dry_run=false to actually archive the candidate memories.
    """
    db = _get_db(request)
    # Override the bank field with the path parameter
    body.bank = bank_name
    try:
        from anamnesis.operations.prune import prune as do_prune
        return await do_prune(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Prune failed: %s", e)
        raise HTTPException(500, f"Prune failed: {e}")


@router.post("/restore/{memory_id}", response_model=RestoreResponse)
async def restore_memory_endpoint(memory_id: str, request: Request):
    """Restore an archived memory back to active status."""
    db = _get_db(request)
    try:
        from anamnesis.operations.prune import restore_memory
        result = await restore_memory(db, memory_id)
        return RestoreResponse(**result)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Restore failed: %s", e)
        raise HTTPException(500, f"Restore failed: {e}")


# ── Boot Briefing ──

@router.post("/boot/{bank_name}", response_model=BootResponse)
async def boot_briefing(bank_name: str, body: BootRequest, request: Request):
    """Cold-start boot briefing — pure data assembly, no LLM call.

    Returns a structured package of mission, directives, top priorities,
    recent outcomes, decay alerts, architecture rules, and gap indicators
    for instant agent orientation.
    """
    import re
    from datetime import datetime, timezone

    db = _get_db(request)

    # Resolve bank by name
    bank = await db.get_bank_by_name(bank_name)
    if not bank:
        raise HTTPException(404, f"Memory bank not found: {bank_name}")

    bank_id = str(bank["id"])
    now = datetime.now(timezone.utc)

    # 1. Mission & directives from bank config
    mission = bank["mission"] or ""
    directives_raw = bank["directives"]
    if isinstance(directives_raw, str):
        directives_raw = json.loads(directives_raw)
    directives: list[str] = directives_raw or []

    # 2. Top priorities — highest-weight active memories
    top_memories = await db.get_top_weighted_memories(bank_id, limit=10)
    top_priorities = []
    for mem in top_memories:
        deps = []
        if mem.get("depends_on"):
            deps = [str(d) for d in mem["depends_on"]]
        top_priorities.append(BootPriority(
            content=mem["content"],
            weight=round(float(mem["weight"]), 2),
            reasoning=mem.get("reasoning"),
            dependencies=deps,
        ))

    # 3. Recent outcomes — memories created in last 48 hours
    recent_outcomes = []
    if body.include_recent_sessions:
        recent_mems = await db.get_recent_memories(bank_id, hours=48, limit=10)
        for mem in recent_mems:
            created = mem["created_at"]
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            recent_outcomes.append(BootOutcome(
                content=mem["content"],
                when=created.strftime("%Y-%m-%d"),
                source=mem.get("source", "unknown"),
            ))

    # 4. Decay alerts — active memories with decay conditions approaching
    decayable = await db.get_decayable_memories(bank_id)
    decay_alerts = []
    for mem in decayable:
        condition = mem.get("decay_condition", "")
        if not condition or condition == "never":
            continue
        status = _evaluate_decay_proximity(condition, mem, now)
        if status:
            decay_alerts.append(BootDecayAlert(
                memory_id=str(mem["id"]),
                content=mem["content"],
                condition=condition,
                status=status,
            ))

    # 5. Architecture rules — memories tagged "architecture" or "do-not-modify"
    arch_tags = ["architecture", "do-not-modify", "rule", "constraint"]
    arch_memories = await db.get_memories_by_tags(bank_id, arch_tags, limit=20)
    architecture_rules = [mem["content"] for mem in arch_memories]

    # 6. Gaps — look for memories tagged "gap" or with content_type "observation"
    #    that mention gaps or missing functionality
    gap_memories = await db.get_memories_by_tags(bank_id, ["gap", "missing"], limit=10)
    gaps_identified = [mem["content"] for mem in gap_memories]

    # 7. Cold start detection — hours since last query
    last_access = await db.get_last_access_time(bank_id)
    hours_since: float | None = None
    cold_start = True
    if last_access:
        if last_access.tzinfo is None:
            last_access = last_access.replace(tzinfo=timezone.utc)
        delta = (now - last_access).total_seconds() / 3600.0
        hours_since = round(delta, 1)
        cold_start = delta > 6.0  # Consider cold if >6 hours since last query

    return BootResponse(
        mission=mission,
        directives=directives,
        top_priorities=top_priorities,
        recent_outcomes=recent_outcomes,
        active_decay_alerts=decay_alerts,
        architecture_rules=architecture_rules,
        gaps_identified=gaps_identified,
        cold_start_warning=cold_start,
        hours_since_last_query=hours_since,
    )


def _evaluate_decay_proximity(condition: str, memory: dict,
                               now: "datetime") -> str | None:
    """Check if a decay condition is approaching or triggered.

    Returns 'approaching' (>75% through), 'triggered', or None.
    """
    import re
    from datetime import timezone

    # after:Nd or after:Nw
    after_match = re.match(r"after:(\d+)([dw])", condition)
    if after_match:
        amount = int(after_match.group(1))
        unit = after_match.group(2)
        total_days = amount if unit == "d" else amount * 7
        created = memory["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).total_seconds() / 86400
        if age_days >= total_days:
            return "triggered"
        if total_days > 0 and age_days / total_days >= 0.75:
            return "approaching"
        return None

    # when:unaccessed:Nd
    unaccessed_match = re.match(r"when:unaccessed:(\d+)d", condition)
    if unaccessed_match:
        days = int(unaccessed_match.group(1))
        last_accessed = memory.get("last_accessed_at", memory["created_at"])
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        idle_days = (now - last_accessed).total_seconds() / 86400
        if idle_days >= days:
            return "triggered"
        if days > 0 and idle_days / days >= 0.75:
            return "approaching"
        return None

    # when:superseded
    if condition == "when:superseded":
        if memory.get("superseded_by") is not None:
            return "triggered"
        return None

    return None


@router.post("/bulk-retain", response_model=BulkRetainResponse)
async def bulk_retain(body: BulkRetainRequest, request: Request):
    db = _get_db(request)
    embedder = _get_embedder(request)
    llm = _get_llm(request)

    memory_ids = []
    errors = []
    for i, mem_req in enumerate(body.memories):
        try:
            result = await retain(db, embedder, mem_req, llm_client=llm)
            memory_ids.append(result.memory_id)
        except Exception as e:
            errors.append(f"Memory {i}: {e}")
            logger.warning("Bulk retain item %d failed: %s", i, e)

    return BulkRetainResponse(
        retained_count=len(memory_ids),
        memory_ids=memory_ids,
        errors=errors,
    )


# ── Individual Memory Access ──

@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str, request: Request):
    db = _get_db(request)
    row = await db.get_memory(memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    # Convert to serializable dict
    result = {k: v for k, v in row.items() if k != "embedding" and k != "search_vector"}
    result["id"] = str(result["id"])
    result["bank_id"] = str(result["bank_id"])
    return result


# ── Helpers ──

def _bank_row_to_response(row: dict, stats: dict = None) -> BankResponse:
    wf = row["weight_factors"]
    if isinstance(wf, str):
        wf = json.loads(wf)
    dirs = row["directives"]
    if isinstance(dirs, str):
        dirs = json.loads(dirs)
    wa = row.get("write_agents", [])
    if isinstance(wa, str):
        wa = json.loads(wa)
    return BankResponse(
        id=str(row["id"]),
        name=row["name"],
        mission=row["mission"],
        directives=dirs,
        disposition=row["disposition"],
        weight_factors=wf,
        default_decay_days=row["default_decay_days"],
        write_agents=wa or [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        memory_count=(stats or {}).get("memory_count", 0),
        entity_count=(stats or {}).get("entity_count", 0),
    )
