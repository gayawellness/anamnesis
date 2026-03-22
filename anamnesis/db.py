"""PostgreSQL + pgvector database layer for Anamnesis."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from pgvector.asyncpg import register_vector

from anamnesis.config import DatabaseConfig

logger = logging.getLogger("anamnesis.db")

# ── Schema ──

SCHEMA_SQL = """
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Memory Banks
CREATE TABLE IF NOT EXISTS memory_banks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    mission TEXT NOT NULL,
    directives JSONB NOT NULL DEFAULT '[]',
    disposition TEXT NOT NULL DEFAULT 'balanced',
    weight_factors JSONB NOT NULL DEFAULT '{{"semantic": 0.30, "temporal": 0.20, "relational": 0.20, "strategic": 0.30}}',
    default_decay_days INTEGER DEFAULT 90,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Memories
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id UUID NOT NULL REFERENCES memory_banks(id) ON DELETE CASCADE,

    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'fact',
    source TEXT NOT NULL DEFAULT 'unknown',

    embedding vector({dims}),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    access_count INTEGER NOT NULL DEFAULT 0,

    reasoning TEXT,
    authority TEXT NOT NULL DEFAULT 'inferred',
    weight REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 0.8,
    decay_condition TEXT,
    supersedes UUID[] DEFAULT '{{}}',
    depends_on UUID[] DEFAULT '{{}}',
    tags TEXT[] DEFAULT '{{}}',

    status TEXT NOT NULL DEFAULT 'active',
    decayed_at TIMESTAMPTZ,
    superseded_by UUID,

    extracted_facts JSONB DEFAULT '[]',

    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX IF NOT EXISTS idx_memories_bank_id ON memories(bank_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_search ON memories USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_memories_content_type ON memories(content_type);

-- Entities
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id UUID NOT NULL REFERENCES memory_banks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{{}}',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(bank_id, name, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_entities_bank_name ON entities(bank_id, name);

-- Entity Edges
CREATE TABLE IF NOT EXISTS entity_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    memory_id UUID REFERENCES memories(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_edges_source ON entity_edges(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_edges_target ON entity_edges(target_entity_id);

-- Memory-Entity junction
CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'mentioned',
    PRIMARY KEY (memory_id, entity_id)
);

-- Access log for temporal scoring
CREATE TABLE IF NOT EXISTS memory_accesses (
    id BIGSERIAL PRIMARY KEY,
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    access_type TEXT NOT NULL DEFAULT 'recall',
    query_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_accesses_memory ON memory_accesses(memory_id);
"""


class Database:
    """Async PostgreSQL connection pool with pgvector support."""

    def __init__(self, config: DatabaseConfig, embedding_dims: int = 1024):
        self.config = config
        self.embedding_dims = embedding_dims
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Initialize connection pool and run migrations."""
        self._pool = await asyncpg.create_pool(
            self.config.dsn,
            min_size=2,
            max_size=10,
            init=self._init_connection,
        )
        await self._run_migrations()
        logger.info("Database connected: %s", self.config.dsn.split("@")[-1])

    async def _init_connection(self, conn: asyncpg.Connection):
        """Register pgvector type on each new connection."""
        await register_vector(conn)

    async def _run_migrations(self):
        """Apply schema migrations."""
        schema = SCHEMA_SQL.format(dims=self.embedding_dims)
        async with self.acquire() as conn:
            await conn.execute(schema)
        logger.info("Schema migrations applied (vector dims=%d)", self.embedding_dims)

    async def close(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database connection pool closed")

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        """Acquire a connection and start a transaction."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def is_healthy(self) -> bool:
        """Check if database is reachable."""
        try:
            async with self.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ── Bank Operations ──

    async def create_bank(self, name: str, mission: str, directives: list,
                          disposition: str, weight_factors: dict,
                          default_decay_days: int) -> dict:
        async with self.transaction() as conn:
            row = await conn.fetchrow(
                """INSERT INTO memory_banks (name, mission, directives, disposition,
                   weight_factors, default_decay_days)
                   VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, $6)
                   RETURNING *""",
                name, mission, _to_json(directives), disposition,
                _to_json(weight_factors), default_decay_days,
            )
            return dict(row)

    async def get_bank_by_name(self, name: str) -> Optional[dict]:
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memory_banks WHERE name = $1", name
            )
            return dict(row) if row else None

    async def get_bank(self, bank_id: str) -> Optional[dict]:
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memory_banks WHERE id = $1::uuid", bank_id
            )
            return dict(row) if row else None

    async def list_banks(self) -> list[dict]:
        async with self.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM memory_banks ORDER BY name")
            return [dict(r) for r in rows]

    async def update_bank(self, bank_id: str, **kwargs) -> Optional[dict]:
        sets = []
        vals = []
        idx = 1
        for key, val in kwargs.items():
            if val is not None:
                if key in ("directives", "weight_factors"):
                    sets.append(f"{key} = ${idx}::jsonb")
                    vals.append(_to_json(val))
                else:
                    sets.append(f"{key} = ${idx}")
                    vals.append(val)
                idx += 1
        if not sets:
            return await self.get_bank(bank_id)
        sets.append(f"updated_at = NOW()")
        vals.append(bank_id)
        query = f"UPDATE memory_banks SET {', '.join(sets)} WHERE id = ${idx}::uuid RETURNING *"
        async with self.transaction() as conn:
            row = await conn.fetchrow(query, *vals)
            return dict(row) if row else None

    async def get_bank_stats(self, bank_id: str) -> dict:
        async with self.acquire() as conn:
            mem_count = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE bank_id = $1::uuid AND status = 'active'",
                bank_id,
            )
            entity_count = await conn.fetchval(
                "SELECT COUNT(*) FROM entities WHERE bank_id = $1::uuid",
                bank_id,
            )
            return {"memory_count": mem_count or 0, "entity_count": entity_count or 0}

    # ── Memory Operations ──

    async def insert_memory(self, bank_id: str, content: str, content_type: str,
                            source: str, embedding: list[float],
                            reasoning: Optional[str], authority: str,
                            weight: float, confidence: float,
                            decay_condition: Optional[str],
                            supersedes: list[str], depends_on: list[str],
                            tags: list[str],
                            extracted_facts: list[dict]) -> dict:
        import numpy as np
        vec = np.array(embedding, dtype=np.float32)

        async with self.transaction() as conn:
            row = await conn.fetchrow(
                """INSERT INTO memories
                   (bank_id, content, content_type, source, embedding,
                    reasoning, authority, weight, confidence, decay_condition,
                    supersedes, depends_on, tags, extracted_facts)
                   VALUES ($1::uuid, $2, $3, $4, $5,
                           $6, $7, $8, $9, $10,
                           $11::uuid[], $12::uuid[], $13::text[], $14::jsonb)
                   RETURNING *""",
                bank_id, content, content_type, source, vec,
                reasoning, authority, weight, confidence, decay_condition,
                supersedes or [], depends_on or [], tags or [],
                _to_json(extracted_facts),
            )

            # Mark superseded memories
            if supersedes:
                mem_id = str(row["id"])
                await conn.execute(
                    """UPDATE memories SET status = 'superseded',
                       superseded_by = $1::uuid
                       WHERE id = ANY($2::uuid[])""",
                    mem_id, supersedes,
                )

            return dict(row)

    async def get_memory(self, memory_id: str) -> Optional[dict]:
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memories WHERE id = $1::uuid", memory_id
            )
            return dict(row) if row else None

    async def list_memories(self, bank_id: str, status: str = "active",
                            limit: int = 50, offset: int = 0) -> list[dict]:
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM memories
                   WHERE bank_id = $1::uuid AND status = $2
                   ORDER BY weight DESC, created_at DESC
                   LIMIT $3 OFFSET $4""",
                bank_id, status, limit, offset,
            )
            return [dict(r) for r in rows]

    async def update_memory_weight(self, memory_id: str, weight: float):
        async with self.acquire() as conn:
            await conn.execute(
                "UPDATE memories SET weight = $1 WHERE id = $2::uuid",
                weight, memory_id,
            )

    async def update_memory_status(self, memory_id: str, status: str):
        async with self.acquire() as conn:
            extra = ", decayed_at = NOW()" if status == "decayed" else ""
            await conn.execute(
                f"UPDATE memories SET status = $1{extra} WHERE id = $2::uuid",
                status, memory_id,
            )

    async def record_access(self, memory_ids: list[str], access_type: str,
                            query_text: Optional[str] = None):
        """Record access and update last_accessed_at + access_count."""
        if not memory_ids:
            return
        async with self.transaction() as conn:
            await conn.execute(
                """UPDATE memories
                   SET last_accessed_at = NOW(), access_count = access_count + 1
                   WHERE id = ANY($1::uuid[])""",
                memory_ids,
            )
            for mid in memory_ids:
                await conn.execute(
                    """INSERT INTO memory_accesses (memory_id, access_type, query_text)
                       VALUES ($1::uuid, $2, $3)""",
                    mid, access_type, query_text,
                )

    # ── Retrieval Strategies ──

    async def search_semantic(self, bank_id: str, embedding: list[float],
                              limit: int = 50, status: str = "active",
                              filters: Optional[dict] = None) -> list[dict]:
        """Vector similarity search using pgvector."""
        import numpy as np
        vec = np.array(embedding, dtype=np.float32)

        where_clauses = ["bank_id = $1::uuid", "status = $2", "embedding IS NOT NULL"]
        params: list = [bank_id, status, vec, limit]
        pidx = 5

        if filters:
            where_clauses, params, pidx = _apply_filters(
                where_clauses, params, pidx, filters
            )

        where = " AND ".join(where_clauses)
        async with self.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT *, 1 - (embedding <=> $3) AS similarity
                    FROM memories
                    WHERE {where}
                    ORDER BY embedding <=> $3
                    LIMIT $4""",
                *params,
            )
            return [dict(r) for r in rows]

    async def search_fulltext(self, bank_id: str, query: str,
                              limit: int = 50, status: str = "active",
                              filters: Optional[dict] = None) -> list[dict]:
        """Full-text search using tsvector."""
        where_clauses = [
            "bank_id = $1::uuid",
            "status = $2",
            "search_vector @@ plainto_tsquery('english', $3)",
        ]
        params: list = [bank_id, status, query, limit]
        pidx = 5

        if filters:
            where_clauses, params, pidx = _apply_filters(
                where_clauses, params, pidx, filters
            )

        where = " AND ".join(where_clauses)
        async with self.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT *, ts_rank(search_vector, plainto_tsquery('english', $3)) AS rank
                    FROM memories
                    WHERE {where}
                    ORDER BY rank DESC
                    LIMIT $4""",
                *params,
            )
            return [dict(r) for r in rows]

    async def search_temporal(self, bank_id: str, limit: int = 50,
                              status: str = "active",
                              filters: Optional[dict] = None) -> list[dict]:
        """Temporal retrieval: recent + frequently accessed."""
        where_clauses = ["bank_id = $1::uuid", "status = $2"]
        params: list = [bank_id, status, limit]
        pidx = 4

        if filters:
            where_clauses, params, pidx = _apply_filters(
                where_clauses, params, pidx, filters
            )

        where = " AND ".join(where_clauses)
        async with self.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT *,
                    (0.7 * (1.0 / (1.0 + EXTRACT(EPOCH FROM NOW() - last_accessed_at) / 86400.0))
                     + 0.3 * LEAST(access_count::real / 10.0, 1.0)) AS temporal_score
                    FROM memories
                    WHERE {where}
                    ORDER BY temporal_score DESC
                    LIMIT $3""",
                *params,
            )
            return [dict(r) for r in rows]

    async def search_by_entities(self, bank_id: str, entity_ids: list[str],
                                 limit: int = 50, status: str = "active") -> list[dict]:
        """Relational retrieval via entity graph (1-hop from given entities)."""
        if not entity_ids:
            return []
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT m.*, 1.0 AS relational_score
                   FROM memories m
                   JOIN memory_entities me ON m.id = me.memory_id
                   WHERE m.bank_id = $1::uuid
                     AND m.status = $2
                     AND me.entity_id = ANY($3::uuid[])
                   ORDER BY m.weight DESC
                   LIMIT $4""",
                bank_id, status, entity_ids, limit,
            )
            return [dict(r) for r in rows]

    # ── Entity Operations ──

    async def find_or_create_entity(self, bank_id: str, name: str,
                                    entity_type: str,
                                    description: Optional[str] = None) -> dict:
        """Find entity by fuzzy name match or create new one."""
        async with self.transaction() as conn:
            # Exact match first
            row = await conn.fetchrow(
                """SELECT * FROM entities
                   WHERE bank_id = $1::uuid AND LOWER(name) = LOWER($2)
                     AND entity_type = $3""",
                bank_id, name, entity_type,
            )
            if row:
                return dict(row)

            # Alias match
            row = await conn.fetchrow(
                """SELECT * FROM entities
                   WHERE bank_id = $1::uuid AND LOWER($2) = ANY(
                     SELECT LOWER(unnest(aliases))
                   )""",
                bank_id, name,
            )
            if row:
                return dict(row)

            # Create new
            row = await conn.fetchrow(
                """INSERT INTO entities (bank_id, name, entity_type, description)
                   VALUES ($1::uuid, $2, $3, $4)
                   ON CONFLICT (bank_id, name, entity_type) DO UPDATE
                   SET updated_at = NOW()
                   RETURNING *""",
                bank_id, name, entity_type, description,
            )
            return dict(row)

    async def link_memory_entity(self, memory_id: str, entity_id: str,
                                 role: str = "mentioned"):
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO memory_entities (memory_id, entity_id, role)
                   VALUES ($1::uuid, $2::uuid, $3)
                   ON CONFLICT DO NOTHING""",
                memory_id, entity_id, role,
            )

    async def create_entity_edge(self, source_id: str, target_id: str,
                                 relation_type: str, memory_id: Optional[str] = None,
                                 weight: float = 1.0):
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO entity_edges
                   (source_entity_id, target_entity_id, relation_type, memory_id, weight)
                   VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5)""",
                source_id, target_id, relation_type, memory_id, weight,
            )

    async def get_connected_entity_ids(self, entity_ids: list[str],
                                       depth: int = 2) -> list[str]:
        """Get entity IDs connected within N hops."""
        if not entity_ids:
            return []
        visited = set(entity_ids)
        frontier = list(entity_ids)

        async with self.acquire() as conn:
            for _ in range(depth):
                if not frontier:
                    break
                rows = await conn.fetch(
                    """SELECT DISTINCT target_entity_id AS eid FROM entity_edges
                       WHERE source_entity_id = ANY($1::uuid[])
                       UNION
                       SELECT DISTINCT source_entity_id AS eid FROM entity_edges
                       WHERE target_entity_id = ANY($1::uuid[])""",
                    frontier,
                )
                next_frontier = []
                for r in rows:
                    eid = str(r["eid"])
                    if eid not in visited:
                        visited.add(eid)
                        next_frontier.append(eid)
                frontier = next_frontier

        return list(visited)

    async def find_entities_by_names(self, bank_id: str,
                                     names: list[str]) -> list[dict]:
        """Find entities matching a list of names (case-insensitive)."""
        if not names:
            return []
        lower_names = [n.lower() for n in names]
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM entities
                   WHERE bank_id = $1::uuid AND LOWER(name) = ANY($2::text[])""",
                bank_id, lower_names,
            )
            return [dict(r) for r in rows]

    # ── Decay Operations ──

    async def get_decayable_memories(self, bank_id: str) -> list[dict]:
        """Get active memories with decay conditions."""
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM memories
                   WHERE bank_id = $1::uuid
                     AND status = 'active'
                     AND decay_condition IS NOT NULL""",
                bank_id,
            )
            return [dict(r) for r in rows]

    async def get_active_memories(self, bank_id: str) -> list[dict]:
        """Get all active memories for reweighting."""
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """SELECT m.*, COUNT(me.entity_id) AS entity_count
                   FROM memories m
                   LEFT JOIN memory_entities me ON m.id = me.memory_id
                   WHERE m.bank_id = $1::uuid AND m.status = 'active'
                   GROUP BY m.id""",
                bank_id,
            )
            return [dict(r) for r in rows]

    async def batch_update_weights(self, updates: list[tuple[str, float]]):
        """Batch update memory weights: [(memory_id, new_weight), ...]."""
        if not updates:
            return
        async with self.transaction() as conn:
            await conn.executemany(
                "UPDATE memories SET weight = $2 WHERE id = $1::uuid",
                updates,
            )

    # ── Stats ──

    async def total_memory_count(self) -> int:
        async with self.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM memories") or 0

    async def total_bank_count(self) -> int:
        async with self.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM memory_banks") or 0


# ── Helpers ──

def _to_json(obj) -> str:
    import json
    return json.dumps(obj)


def _apply_filters(where_clauses: list, params: list, pidx: int,
                   filters: dict) -> tuple[list, list, int]:
    """Apply optional recall filters to a query."""
    if filters.get("content_types"):
        where_clauses.append(f"content_type = ANY(${pidx}::text[])")
        params.append(filters["content_types"])
        pidx += 1
    if filters.get("min_weight") is not None:
        where_clauses.append(f"weight >= ${pidx}")
        params.append(filters["min_weight"])
        pidx += 1
    if filters.get("tags"):
        where_clauses.append(f"tags && ${pidx}::text[]")
        params.append(filters["tags"])
        pidx += 1
    if filters.get("after"):
        where_clauses.append(f"created_at >= ${pidx}")
        params.append(filters["after"])
        pidx += 1
    if filters.get("before"):
        where_clauses.append(f"created_at <= ${pidx}")
        params.append(filters["before"])
        pidx += 1
    return where_clauses, params, pidx
