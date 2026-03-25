"""Anamnesis export/import — backup and restore memory banks.

Provides functions for exporting banks to JSON and importing them back.
Works via the REST API (SDK client) for portability, with direct database
access for bulk entity/relationship queries that the API doesn't expose.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

logger = logging.getLogger("anamnesis.export_import")

EXPORT_VERSION = "1.0"


# ── Serialization Helpers ──

def _isoformat(val) -> Optional[str]:
    """Convert a datetime or string to ISO format string, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        return val
    return str(val)


def _serialize_memory(mem: dict) -> dict:
    """Normalize a memory dict from the API into the export schema."""
    return {
        "id": str(mem.get("id", "")),
        "content": mem.get("content", ""),
        "content_type": mem.get("content_type", "fact"),
        "source": mem.get("source", "unknown"),
        "reasoning": mem.get("reasoning"),
        "authority": mem.get("authority", "inferred"),
        "weight": mem.get("weight", 1.0),
        "confidence": mem.get("confidence", 0.8),
        "tags": mem.get("tags", []),
        "decay_condition": mem.get("decay_condition"),
        "supersedes": [str(s) for s in (mem.get("supersedes") or [])],
        "depends_on": [str(d) for d in (mem.get("depends_on") or [])],
        "status": mem.get("status", "active"),
        "access_count": mem.get("access_count", 0),
        "extracted_facts": mem.get("extracted_facts", []),
        "created_at": _isoformat(mem.get("created_at")),
        "last_accessed_at": _isoformat(mem.get("last_accessed_at")),
        "decayed_at": _isoformat(mem.get("decayed_at")),
        "superseded_by": str(mem["superseded_by"]) if mem.get("superseded_by") else None,
    }


def _serialize_entity(ent: dict) -> dict:
    """Normalize an entity dict into the export schema."""
    return {
        "id": str(ent.get("id", "")),
        "name": ent.get("name", ""),
        "entity_type": ent.get("entity_type", ""),
        "aliases": ent.get("aliases", []),
        "description": ent.get("description"),
        "created_at": _isoformat(ent.get("created_at")),
        "updated_at": _isoformat(ent.get("updated_at")),
    }


def _serialize_relationship(edge: dict) -> dict:
    """Normalize an entity edge dict into the export schema."""
    return {
        "id": str(edge.get("id", "")),
        "source_entity_id": str(edge.get("source_entity_id", "")),
        "target_entity_id": str(edge.get("target_entity_id", "")),
        "relation_type": edge.get("relation_type", ""),
        "weight": edge.get("weight", 1.0),
        "memory_id": str(edge["memory_id"]) if edge.get("memory_id") else None,
        "created_at": _isoformat(edge.get("created_at")),
    }


# ── Database Helpers (for data not exposed via API) ──

async def _fetch_all_memories_for_bank(db, bank_id: str) -> list[dict]:
    """Fetch ALL memories (active + decayed + superseded) for a bank."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM memories
               WHERE bank_id = $1::uuid
               ORDER BY created_at ASC""",
            bank_id,
        )
        return [dict(r) for r in rows]


async def _fetch_entities_for_bank(db, bank_id: str) -> list[dict]:
    """Fetch all entities for a bank."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM entities
               WHERE bank_id = $1::uuid
               ORDER BY name ASC""",
            bank_id,
        )
        return [dict(r) for r in rows]


async def _fetch_relationships_for_bank(db, bank_id: str) -> list[dict]:
    """Fetch all entity edges (relationships) for entities in a bank."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ee.* FROM entity_edges ee
               JOIN entities e ON ee.source_entity_id = e.id
               WHERE e.bank_id = $1::uuid
               ORDER BY ee.created_at ASC""",
            bank_id,
        )
        return [dict(r) for r in rows]


async def _fetch_memory_entities_for_bank(db, bank_id: str) -> list[dict]:
    """Fetch all memory-entity links for a bank."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT me.* FROM memory_entities me
               JOIN memories m ON me.memory_id = m.id
               WHERE m.bank_id = $1::uuid""",
            bank_id,
        )
        return [dict(r) for r in rows]


# ── Export (API-based, for CLI use) ──

def export_bank(client: AnamnesisClient, bank_name: str) -> dict:
    """Export a single bank using the API export endpoint.

    Args:
        client: Configured AnamnesisClient instance.
        bank_name: Name of the bank to export.

    Returns:
        Full export dict ready for JSON serialization.
    """
    resp = client._check(client._client.get(f"/export/{bank_name}"))
    return resp


def export_all(client: AnamnesisClient) -> dict:
    """Export all banks by fetching each one via the API.

    Args:
        client: Configured AnamnesisClient instance.

    Returns:
        Full export dict with all banks.
    """
    banks = client.list_banks()
    all_bank_exports = []
    for bank in banks:
        bank_name = bank.get("name", "")
        if not bank_name:
            continue
        try:
            bank_export = client._check(
                client._client.get(f"/export/{bank_name}")
            )
            # The API returns the full wrapper; extract just the bank data
            if "banks" in bank_export:
                all_bank_exports.extend(bank_export["banks"])
            else:
                all_bank_exports.append(bank_export)
        except AnamnesisError as e:
            logger.warning("Failed to export bank %s: %s", bank_name, e)

    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "banks": all_bank_exports,
    }


def import_bank(client: AnamnesisClient, file_path: str,
                merge: bool = False) -> dict:
    """Import banks from a JSON backup file via the API.

    Args:
        client: Configured AnamnesisClient instance.
        file_path: Path to the JSON backup file.
        merge: If True, skip memories that already exist (by ID).
               If False (default), fail on conflicts.

    Returns:
        Import result dict from the API.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup file not found: {file_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    body = {
        "data": data,
        "merge": merge,
    }
    resp = client._check(client._client.post("/import", json=body))
    return resp


# ── Export (Database-direct, for API endpoint use) ──

async def export_bank_from_db(db, bank_name: str) -> dict:
    """Export a single bank directly from the database.

    This is the server-side implementation called by the API endpoint.
    It has direct database access and can export everything including
    entities, relationships, and memory-entity links.

    Args:
        db: Anamnesis Database instance.
        bank_name: Name of the bank to export.

    Returns:
        Export dict for a single bank.

    Raises:
        ValueError: If the bank does not exist.
    """
    bank = await db.get_bank_by_name(bank_name)
    if not bank:
        raise ValueError(f"Bank not found: {bank_name}")

    bank_id = str(bank["id"])

    # Fetch all data in parallel-ish (sequential but fast DB queries)
    memories_raw = await _fetch_all_memories_for_bank(db, bank_id)
    entities_raw = await _fetch_entities_for_bank(db, bank_id)
    relationships_raw = await _fetch_relationships_for_bank(db, bank_id)
    memory_entities_raw = await _fetch_memory_entities_for_bank(db, bank_id)

    # Parse JSONB fields from bank config
    directives = bank.get("directives", [])
    if isinstance(directives, str):
        directives = json.loads(directives)
    weight_factors = bank.get("weight_factors", {})
    if isinstance(weight_factors, str):
        weight_factors = json.loads(weight_factors)
    write_agents = bank.get("write_agents", [])
    if isinstance(write_agents, str):
        write_agents = json.loads(write_agents)

    # Serialize
    memories = [_serialize_memory(m) for m in memories_raw]
    entities = [_serialize_entity(e) for e in entities_raw]
    relationships = [_serialize_relationship(r) for r in relationships_raw]

    # Serialize memory-entity links
    memory_entity_links = [
        {
            "memory_id": str(me["memory_id"]),
            "entity_id": str(me["entity_id"]),
            "role": me.get("role", "mentioned"),
        }
        for me in memory_entities_raw
    ]

    bank_export = {
        "config": {
            "name": bank["name"],
            "mission": bank["mission"],
            "directives": directives,
            "disposition": bank.get("disposition", "balanced"),
            "weight_factors": weight_factors,
            "default_decay_days": bank.get("default_decay_days", 90),
            "write_agents": write_agents,
        },
        "memories": memories,
        "entities": entities,
        "relationships": relationships,
        "memory_entity_links": memory_entity_links,
    }

    return bank_export


async def export_all_from_db(db) -> dict:
    """Export all banks from the database.

    Args:
        db: Anamnesis Database instance.

    Returns:
        Full export dict with all banks.
    """
    banks = await db.list_banks()
    bank_exports = []
    for bank in banks:
        bank_name = bank["name"]
        try:
            bank_data = await export_bank_from_db(db, bank_name)
            bank_exports.append(bank_data)
        except Exception as e:
            logger.warning("Failed to export bank %s: %s", bank_name, e)

    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "banks": bank_exports,
    }


async def import_bank_to_db(db, embedder, data: dict,
                            merge: bool = False) -> dict:
    """Import banks from an export dict directly into the database.

    This is the server-side implementation called by the API endpoint.

    Args:
        db: Anamnesis Database instance.
        embedder: Embedder instance for generating embeddings on imported memories.
        data: The export dict (with "version", "banks", etc.).
        merge: If True, skip memories/entities that already exist.
               If False, raise on conflict.

    Returns:
        Summary dict with counts of imported items.
    """
    import numpy as np

    version = data.get("version", "1.0")
    banks_data = data.get("banks", [])

    if not banks_data:
        return {
            "imported_banks": 0,
            "imported_memories": 0,
            "imported_entities": 0,
            "imported_relationships": 0,
            "skipped_memories": 0,
            "skipped_entities": 0,
            "errors": [],
        }

    total_memories = 0
    total_entities = 0
    total_relationships = 0
    skipped_memories = 0
    skipped_entities = 0
    imported_banks = 0
    errors = []

    for bank_data in banks_data:
        config = bank_data.get("config", {})
        bank_name = config.get("name")
        if not bank_name:
            errors.append("Bank entry missing 'config.name', skipped")
            continue

        # Find or create the bank
        existing_bank = await db.get_bank_by_name(bank_name)
        if existing_bank:
            bank_id = str(existing_bank["id"])
            if not merge:
                # In non-merge mode, update bank config to match backup
                await db.update_bank(
                    bank_id,
                    mission=config.get("mission", existing_bank["mission"]),
                    directives=config.get("directives"),
                    disposition=config.get("disposition"),
                    weight_factors=config.get("weight_factors"),
                    default_decay_days=config.get("default_decay_days"),
                    write_agents=config.get("write_agents"),
                )
        else:
            new_bank = await db.create_bank(
                name=bank_name,
                mission=config.get("mission", ""),
                directives=config.get("directives", []),
                disposition=config.get("disposition", "balanced"),
                weight_factors=config.get("weight_factors", {
                    "semantic": 0.30, "temporal": 0.20,
                    "relational": 0.20, "strategic": 0.30,
                }),
                default_decay_days=config.get("default_decay_days", 90),
                write_agents=config.get("write_agents", []),
            )
            bank_id = str(new_bank["id"])

        imported_banks += 1

        # Build entity ID mapping (old ID -> new ID) for relationship restoration
        entity_id_map: dict[str, str] = {}

        # Import entities first (needed for relationship and memory-entity links)
        for ent_data in bank_data.get("entities", []):
            old_entity_id = ent_data.get("id", "")
            try:
                entity = await db.find_or_create_entity(
                    bank_id=bank_id,
                    name=ent_data["name"],
                    entity_type=ent_data["entity_type"],
                    description=ent_data.get("description"),
                )
                entity_id_map[old_entity_id] = str(entity["id"])
                total_entities += 1
            except Exception as e:
                if merge:
                    skipped_entities += 1
                    logger.debug("Skipped entity %s: %s", ent_data.get("name"), e)
                else:
                    errors.append(f"Entity '{ent_data.get('name')}': {e}")

        # Build memory ID mapping (old ID -> new ID)
        memory_id_map: dict[str, str] = {}

        # Import memories
        for mem_data in bank_data.get("memories", []):
            old_memory_id = mem_data.get("id", "")
            content = mem_data.get("content", "")
            if not content:
                errors.append(f"Memory {old_memory_id}: empty content, skipped")
                continue

            # Check if memory already exists (by content match in merge mode)
            if merge:
                existing = await _find_memory_by_content(
                    db, bank_id, content
                )
                if existing:
                    memory_id_map[old_memory_id] = str(existing["id"])
                    skipped_memories += 1
                    continue

            # Generate embedding for the imported memory
            try:
                embedding = await embedder.embed(content)
            except Exception as e:
                logger.warning(
                    "Embedding failed for memory %s, importing without embedding: %s",
                    old_memory_id, e,
                )
                embedding = None

            try:
                vec = np.array(embedding, dtype=np.float32) if embedding else None
                # Use direct SQL to preserve original metadata
                async with db.transaction() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO memories
                           (bank_id, content, content_type, source, embedding,
                            reasoning, authority, weight, confidence, decay_condition,
                            supersedes, depends_on, tags, extracted_facts, status,
                            access_count)
                           VALUES ($1::uuid, $2, $3, $4, $5,
                                   $6, $7, $8, $9, $10,
                                   $11::uuid[], $12::uuid[], $13::text[], $14::jsonb,
                                   $15, $16)
                           RETURNING id""",
                        bank_id,
                        content,
                        mem_data.get("content_type", "fact"),
                        mem_data.get("source", "import"),
                        vec,
                        mem_data.get("reasoning"),
                        mem_data.get("authority", "inferred"),
                        float(mem_data.get("weight", 1.0)),
                        float(mem_data.get("confidence", 0.8)),
                        mem_data.get("decay_condition"),
                        [],  # supersedes — cannot preserve old UUIDs
                        [],  # depends_on — cannot preserve old UUIDs
                        mem_data.get("tags", []),
                        json.dumps(mem_data.get("extracted_facts", [])),
                        mem_data.get("status", "active"),
                        mem_data.get("access_count", 0),
                    )
                    new_id = str(row["id"])
                    memory_id_map[old_memory_id] = new_id
                    total_memories += 1

            except Exception as e:
                errors.append(f"Memory '{content[:60]}...': {e}")
                logger.warning("Failed to import memory %s: %s", old_memory_id, e)

        # Restore memory-entity links
        for link in bank_data.get("memory_entity_links", []):
            old_mem_id = link.get("memory_id", "")
            old_ent_id = link.get("entity_id", "")
            new_mem_id = memory_id_map.get(old_mem_id)
            new_ent_id = entity_id_map.get(old_ent_id)
            if new_mem_id and new_ent_id:
                try:
                    await db.link_memory_entity(
                        new_mem_id, new_ent_id,
                        role=link.get("role", "mentioned"),
                    )
                except Exception as e:
                    logger.debug("Failed to link memory-entity: %s", e)

        # Restore entity relationships
        for rel in bank_data.get("relationships", []):
            old_src = rel.get("source_entity_id", "")
            old_tgt = rel.get("target_entity_id", "")
            new_src = entity_id_map.get(old_src)
            new_tgt = entity_id_map.get(old_tgt)
            if new_src and new_tgt:
                linked_memory = memory_id_map.get(rel.get("memory_id", ""))
                try:
                    await db.create_entity_edge(
                        source_id=new_src,
                        target_id=new_tgt,
                        relation_type=rel.get("relation_type", "related"),
                        memory_id=linked_memory,
                        weight=float(rel.get("weight", 1.0)),
                    )
                    total_relationships += 1
                except Exception as e:
                    logger.debug("Failed to create entity edge: %s", e)

    return {
        "imported_banks": imported_banks,
        "imported_memories": total_memories,
        "imported_entities": total_entities,
        "imported_relationships": total_relationships,
        "skipped_memories": skipped_memories,
        "skipped_entities": skipped_entities,
        "errors": errors,
    }


async def _find_memory_by_content(db, bank_id: str, content: str) -> Optional[dict]:
    """Find a memory by exact content match within a bank (for merge dedup)."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM memories
               WHERE bank_id = $1::uuid AND content = $2
               LIMIT 1""",
            bank_id, content,
        )
        return dict(row) if row else None
