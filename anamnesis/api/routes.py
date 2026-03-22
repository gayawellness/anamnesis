"""REST API routes for Anamnesis."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from anamnesis.models import (
    BankCreate,
    BankResponse,
    BankUpdate,
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
    ReweightRequest,
    ReweightResponse,
)
from anamnesis.operations.recall import recall
from anamnesis.operations.retain import retain

logger = logging.getLogger("anamnesis.routes")

router = APIRouter()


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
    return BankResponse(
        id=str(row["id"]),
        name=row["name"],
        mission=row["mission"],
        directives=dirs,
        disposition=row["disposition"],
        weight_factors=wf,
        default_decay_days=row["default_decay_days"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        memory_count=(stats or {}).get("memory_count", 0),
        entity_count=(stats or {}).get("entity_count", 0),
    )
