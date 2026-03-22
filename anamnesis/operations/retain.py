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

    # 2. Generate embedding
    embedding = await embedder.embed(request.content)

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
    weight = _calculate_weight(
        authority=request.authority,
        confidence=request.confidence,
        entity_count=len(entities_linked),
    )

    # 6. Insert memory
    facts_dicts = [f.model_dump() for f in extracted_facts]
    row = await db.insert_memory(
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

    return RetainResponse(
        memory_id=memory_id,
        extracted_facts=extracted_facts,
        entities_linked=entities_linked,
        weight=weight,
    )


def _calculate_weight(authority: Authority, confidence: float,
                      entity_count: int) -> float:
    """Calculate initial strategic weight for a memory."""
    base = AUTHORITY_MULTIPLIERS.get(authority, 1.0)
    connectivity_bonus = 1.0 + 0.1 * min(entity_count, 10)
    weight = base * confidence * connectivity_bonus
    return round(min(max(weight, 0.0), 10.0), 2)


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
