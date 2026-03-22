"""reflect() — Weighted synthesis via LLM using bank config + retrieved memories."""

from __future__ import annotations

import json
import logging
import re

from anamnesis.db import Database
from anamnesis.embedder import BaseEmbedder
from anamnesis.models import (
    RecallRequest,
    ReflectRequest,
    ReflectResponse,
    SynthesisStyle,
)
from anamnesis.operations.recall import recall

logger = logging.getLogger("anamnesis.reflect")

REFLECT_SYSTEM_PROMPT = """You are Anamnesis, a strategic memory synthesis engine.

BANK MISSION: {mission}

BANK DIRECTIVES (in priority order):
{directives}

DISPOSITION: {disposition}

You have been given retrieved memories ordered by relevance, each with a strategic weight (0-10 scale) and source attribution. Your task is to synthesize a response to the user's question.

RULES:
- Weight higher-authority and higher-weight memories more heavily in your synthesis
- Cite memory numbers in brackets like [1], [2] when referencing specific memories
- Flag any contradictions between memories
- Identify gaps — what information would be useful but is missing?
- {style_instruction}

RETRIEVED MEMORIES:
{memories_block}

Respond with a JSON object:
{{
  "synthesis": "Your synthesized response here",
  "cited_memories": ["id1", "id2"],
  "confidence": 0.85,
  "gaps_identified": ["Gap 1", "Gap 2"]
}}
"""

STYLE_INSTRUCTIONS = {
    SynthesisStyle.STRATEGIC: (
        "Produce a ranked operating directive, not a summary. "
        "Lead with the highest-priority action and explain WHY based on the strategic reasoning in the memories."
    ),
    SynthesisStyle.FACTUAL: (
        "Produce a factual, concise answer. Stick to what the memories state. "
        "Do not speculate beyond the data."
    ),
    SynthesisStyle.NARRATIVE: (
        "Produce a coherent narrative that tells the story across the memories. "
        "Connect the dots and show how decisions evolved over time."
    ),
}


async def reflect(
    db: Database,
    embedder: BaseEmbedder,
    llm_client,
    request: ReflectRequest,
) -> ReflectResponse:
    """Synthesize an answer from retrieved memories, weighted by strategic importance.

    Pipeline:
    1. recall() top N memories
    2. Build synthesis prompt with bank config
    3. LLM call → weighted directive
    4. Parse citations and gaps
    """
    # 1. Recall relevant memories
    recall_request = RecallRequest(
        bank=request.bank,
        query=request.question,
        limit=request.max_memories,
    )
    recall_result = await recall(db, embedder, recall_request)

    if not recall_result.memories:
        return ReflectResponse(
            synthesis="No relevant memories found for this question.",
            cited_memories=[],
            confidence=0.0,
            gaps_identified=["No memories in this bank match the query"],
        )

    # 2. Get bank config
    bank = await db.get_bank_by_name(request.bank)
    directives = bank["directives"]
    if isinstance(directives, str):
        directives = json.loads(directives)

    # Build memories block
    memories_block_parts = []
    memory_id_map: dict[int, str] = {}  # 1-indexed → memory_id
    for i, mem in enumerate(recall_result.memories, 1):
        memory_id_map[i] = mem.id
        memories_block_parts.append(
            f"[{i}] (weight: {mem.weight:.1f}, source: {mem.source}, "
            f"{mem.created_at.strftime('%Y-%m-%d')})\n{mem.content}"
        )
    memories_block = "\n\n".join(memories_block_parts)

    directives_text = "\n".join(f"  {i}. {d}" for i, d in enumerate(directives, 1))
    style_instruction = STYLE_INSTRUCTIONS.get(
        request.synthesis_style, STYLE_INSTRUCTIONS[SynthesisStyle.STRATEGIC]
    )

    # 3. LLM synthesis
    system_prompt = REFLECT_SYSTEM_PROMPT.format(
        mission=bank["mission"],
        directives=directives_text or "  (none set)",
        disposition=bank["disposition"],
        style_instruction=style_instruction,
        memories_block=memories_block,
    )

    user_prompt = request.question
    if request.context:
        user_prompt = f"{request.question}\n\nAdditional context: {request.context}"

    try:
        result = llm_client.generate_json(system_prompt, user_prompt)
    except Exception as e:
        logger.warning("JSON parse failed, falling back to text generation: %s", e)
        text = llm_client.generate(system_prompt, user_prompt)
        return ReflectResponse(
            synthesis=text,
            cited_memories=[m.id for m in recall_result.memories[:5]],
            confidence=0.6,
            gaps_identified=[],
        )

    # 4. Parse response
    synthesis = result.get("synthesis", str(result))
    raw_cited = result.get("cited_memories", [])

    # Resolve cited memory references (could be indices or IDs)
    cited_ids = []
    for ref in raw_cited:
        if isinstance(ref, int) and ref in memory_id_map:
            cited_ids.append(memory_id_map[ref])
        elif isinstance(ref, str):
            cited_ids.append(ref)

    # Also extract [N] references from synthesis text
    bracket_refs = re.findall(r'\[(\d+)\]', synthesis)
    for ref_str in bracket_refs:
        ref_num = int(ref_str)
        if ref_num in memory_id_map:
            mid = memory_id_map[ref_num]
            if mid not in cited_ids:
                cited_ids.append(mid)

    confidence = result.get("confidence", 0.7)
    if isinstance(confidence, str):
        try:
            confidence = float(confidence)
        except ValueError:
            confidence = 0.7

    gaps = result.get("gaps_identified", [])

    logger.info(
        "Reflect produced synthesis (%d chars, %d citations, confidence=%.2f)",
        len(synthesis), len(cited_ids), confidence,
    )

    return ReflectResponse(
        synthesis=synthesis,
        cited_memories=cited_ids,
        confidence=confidence,
        gaps_identified=gaps,
    )
