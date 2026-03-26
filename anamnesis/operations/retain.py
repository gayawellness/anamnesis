"""retain() — Store a memory with full processing pipeline."""

from __future__ import annotations

import logging
import time
from typing import Optional

from anamnesis.db import Database
from anamnesis.embedder import BaseEmbedder
from anamnesis.models import (
    Authority,
    ExtractedFact,
    RetainRequest,
    RetainResponse,
)

logger = logging.getLogger("anamnesis.retain")

# Authority multipliers for weight calculation
AUTHORITY_MULTIPLIERS = {
    Authority.EXPLICIT: 2.0,
    Authority.SYSTEM: 1.5,
    Authority.INFERRED: 1.0,
}

# Maximum initial weight per authority level.
# Memories can exceed these caps via reweight cycles after validation.
AUTHORITY_WEIGHT_CAPS = {
    Authority.EXPLICIT: 8.0,
    Authority.SYSTEM: 2.0,
    Authority.INFERRED: 1.0,
}


async def retain(
    db: Database,
    embedder: BaseEmbedder,
    request: RetainRequest,
    llm_client=None,
) -> RetainResponse:
    """Store a memory with embedding, fact extraction, entity resolution, and weight calculation.

    Pipeline:
    1. Validate bank exists
    2. Generate embedding
    3. Extract facts (LLM, if client provided)
    4. Resolve entities and build graph edges
    5. Calculate strategic weight
    6. Insert memory
    """
    start = time.monotonic()

    # 1. Validate bank
    bank = await db.get_bank_by_name(request.bank)
    if not bank:
        raise ValueError(f"Memory bank not found: {request.bank}")
    bank_id = str(bank["id"])

    # 1b. Enforce write_agents access control
    _enforce_write_access(bank, request.source)

    # 2. Generate embedding (with retry and fallback)
    embedding, embedding_status, embedding_warning = await _generate_embedding_safe(
        embedder, request.content
    )

    # 3. Extract facts (if LLM available)
    extracted_facts: list[ExtractedFact] = []
    if llm_client:
        extracted_facts = await _extract_facts(llm_client, request.content)

    # 4. Entity resolution + graph edges
    entities_linked: list[str] = []
    if extracted_facts:
        entities_linked = await _resolve_entities(
            db, bank_id, extracted_facts, memory_id=None  # will link after insert
        )

    # 5. Calculate strategic weight
    weight, weight_note = _calculate_weight(
        authority=request.authority,
        confidence=request.confidence,
        entity_count=len(entities_linked),
    )

    # 6. Insert memory
    facts_dicts = [f.model_dump() for f in extracted_facts]
    insert_kwargs = dict(
        bank_id=bank_id,
        content=request.content,
        content_type=request.content_type.value,
        source=request.source,
        embedding=embedding,
        reasoning=request.reasoning,
        authority=request.authority.value,
        weight=weight,
        confidence=request.confidence,
        decay_condition=request.decay_condition,
        supersedes=request.supersedes,
        depends_on=request.depends_on,
        tags=request.tags,
        extracted_facts=facts_dicts,
    )
    if embedding_status != "complete":
        insert_kwargs["embedding_status"] = embedding_status
    row = await db.insert_memory(**insert_kwargs)

    memory_id = str(row["id"])

    # Link entities to the newly created memory
    for entity_name in entities_linked:
        entities = await db.find_entities_by_names(bank_id, [entity_name])
        for ent in entities:
            await db.link_memory_entity(memory_id, str(ent["id"]))

    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "Retained memory %s in bank %s (%.0fms, weight=%.2f, %d facts, %d entities)",
        memory_id, request.bank, elapsed, weight,
        len(extracted_facts), len(entities_linked),
    )

    response = RetainResponse(
        memory_id=memory_id,
        extracted_facts=extracted_facts,
        entities_linked=entities_linked,
        weight=weight,
        weight_note=weight_note,
    )
    if embedding_warning:
        response.warning = embedding_warning
        response.embedding_status = embedding_status
    return response


async def _generate_embedding_safe(
    embedder: BaseEmbedder, content: str
) -> tuple[list[float] | None, str, str | None]:
    """Generate embedding with retry and local fallback.

    Returns:
        (embedding, status, warning) tuple.
        status is "complete", "fallback", or "failed".
        warning is None on success, a message on degraded/failed.
    """
    import asyncio

    # Attempt 1: primary provider
    try:
        embedding = await embedder.embed(content)
        return embedding, "complete", None
    except Exception as e:
        logger.warning("Embedding attempt 1 failed: %s", e)

    # Attempt 2: retry after 2 seconds
    await asyncio.sleep(2)
    try:
        embedding = await embedder.embed(content)
        return embedding, "complete", None
    except Exception as e:
        logger.warning("Embedding attempt 2 failed: %s", e)

    # Attempt 3: local fallback (if primary is not already local)
    try:
        from anamnesis.config import EmbeddingConfig
        from anamnesis.embedder import LocalEmbedder
        fallback_config = EmbeddingConfig(
            provider="local", model="all-MiniLM-L6-v2",
        )
        fallback = LocalEmbedder(fallback_config)
        embedding = await fallback.embed(content)
        return embedding, "fallback", (
            "Primary embedding provider failed. Used local fallback. "
            "Memory is searchable but may have reduced semantic quality."
        )
    except Exception as e:
        logger.error("Local embedding fallback also failed: %s", e)

    # All attempts failed — store without embedding
    logger.error("All embedding attempts failed for content: %.60s...", content)
    return None, "failed", (
        "Embedding generation failed. Memory stored but will not appear "
        "in semantic search until repaired. "
        "Run: python3 -m anamnesis.cli repair-embeddings --bank <name>"
    )


def _calculate_weight(authority: Authority, confidence: float,
                      entity_count: int) -> tuple[float, str]:
    """Calculate initial strategic weight for a memory.

    Returns (weight, weight_note) tuple. Weight is capped per authority level.
    Memories can exceed initial caps via reweight cycles after validation.
    """
    base = AUTHORITY_MULTIPLIERS.get(authority, 1.0)
    connectivity_bonus = 1.0 + 0.1 * min(entity_count, 10)
    raw_weight = base * confidence * connectivity_bonus
    cap = AUTHORITY_WEIGHT_CAPS.get(authority, 4.0)
    weight = round(min(max(raw_weight, 0.0), cap), 2)

    if raw_weight > cap:
        note = (
            f"Initial weight capped at {cap} for {authority.value}-authority source "
            f"(raw: {raw_weight:.2f}). Use reweight cycles to increase based on validation."
        )
    else:
        note = (
            f"Weight {weight} assigned via {authority.value} authority "
            f"(base={base}, confidence={confidence}, connectivity={connectivity_bonus:.1f})."
        )
    return weight, note


def _enforce_write_access(bank: dict, source: str) -> None:
    """Check if the source agent is allowed to write to this bank.

    If write_agents is empty or not set, any agent can write (backward compatible).
    If write_agents is set, only listed agents may retain to this bank.
    """
    import json

    write_agents = bank.get("write_agents", [])
    # Handle JSONB stored as string (depends on driver behavior)
    if isinstance(write_agents, str):
        try:
            write_agents = json.loads(write_agents)
        except (json.JSONDecodeError, TypeError):
            write_agents = []

    # Empty list = open access (backward compatible)
    if not write_agents:
        return

    if source not in write_agents:
        raise ValueError(
            f"Source '{source}' is not authorized to write to bank '{bank['name']}'. "
            f"Authorized write agents: {write_agents}"
        )


async def _extract_facts(llm_client, content: str) -> list[ExtractedFact]:
    """Use LLM to extract subject-predicate-object triples from content."""
    try:
        system_prompt = (
            "Extract atomic facts as subject-predicate-object triples from the text. "
            "Return a JSON array of objects with keys: subject, predicate, object. "
            "Keep facts concise. Maximum 5 triples. If no clear facts, return []."
        )
        result = llm_client.generate_json(system_prompt, content)
        if isinstance(result, list):
            return [ExtractedFact(**f) for f in result[:5]]
        elif isinstance(result, dict) and "facts" in result:
            return [ExtractedFact(**f) for f in result["facts"][:5]]
        return []
    except Exception as e:
        logger.warning("Fact extraction failed: %s", e)
        return []


async def _resolve_entities(db: Database, bank_id: str,
                            facts: list[ExtractedFact],
                            memory_id: Optional[str] = None) -> list[str]:
    """Resolve entities from extracted facts and create graph edges."""
    entity_names = set()
    for fact in facts:
        entity_names.add(fact.subject)
        entity_names.add(fact.object)

    linked_names = []
    entity_map: dict[str, str] = {}  # name -> entity_id

    for name in entity_names:
        if not name or len(name) < 2:
            continue
        entity = await db.find_or_create_entity(
            bank_id=bank_id,
            name=name,
            entity_type="concept",  # default; can be refined later
        )
        entity_map[name] = str(entity["id"])
        linked_names.append(name)

    # Create edges from facts
    for fact in facts:
        source_id = entity_map.get(fact.subject)
        target_id = entity_map.get(fact.object)
        if source_id and target_id and source_id != target_id:
            await db.create_entity_edge(
                source_id=source_id,
                target_id=target_id,
                relation_type=fact.predicate,
                memory_id=memory_id,
            )

    return linked_names
