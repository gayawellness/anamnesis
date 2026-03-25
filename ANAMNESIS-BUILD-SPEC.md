# ANAMNESIS: 4D-RAG Memory Engine — Build Spec

> **What this is:** A complete build instruction document for Claude Code. Read the entire file before starting. Build in phase order. Do not skip phases. Test each phase before proceeding.

> **What you're building:** A self-hosted memory engine that stores and retrieves AI agent memories across four dimensions — semantic, temporal, relational, and strategic. The fourth dimension (strategic reasoning) is the novel layer. No existing tool does this.

> **Codename:** Anamnesis (Greek: knowledge recovered through remembering)

---

## CONTEXT: Why This Exists

Current AI agent memory tools (Mem0, Hindsight, Zep) store facts but not judgment. An agent can retrieve "Gaya DTC leads are the priority" but cannot retrieve:
- **Why** that decision was made
- **Under what conditions** it should change
- **How it ranks** against competing priorities right now
- **Who decided it** and with what confidence

Anamnesis adds a strategic metadata envelope to every memory, enabling a `reflect` operation that produces weighted operating directives — not just summaries.

### Integration Model

Anamnesis sits between a planning layer and an execution layer:

```
Planning Layer (human + LLM planning sessions)
    ↓ retain() with strategic metadata
Memory Layer (Anamnesis)
    ↓ recall() and reflect()
Execution Layer (autonomous agents via cron/OpenClaw/Claude Code)
    ↓ retain() execution results back
Memory Layer (feedback loop)
```

### Feature Forge Compatibility

Anamnesis is designed to work with the Feature Forge pattern (disposable sessions, one step per cron tick, filesystem state). Each cron tick:
1. Agent reads `active.json` (what step am I on?)
2. Agent reads `handoff.md` (what did last session do?)
3. Agent calls `anamnesis.recall()` or `anamnesis.reflect()` for strategic context
4. Agent executes the step
5. Agent writes results to `active.json` and `handoff.md`
6. Agent calls `anamnesis.retain()` with any new learnings
7. Session dies cleanly

---

## ARCHITECTURE OVERVIEW

### Four Retrieval Dimensions

Every query fires four parallel retrieval strategies:

| # | Dimension | Strategy | What It Finds |
|---|-----------|----------|---------------|
| 1 | Semantic | Vector similarity (pgvector) | Conceptually related memories |
| 2 | Temporal | Time-aware filtering + decay | Recency, validity windows, sequence |
| 3 | Relational | Property graph traversal | Entity connections across memories |
| 4 | Strategic | Weight + reasoning retrieval | Priority, reasoning, decay conditions |

Results from all four are merged via reciprocal rank fusion + cross-encoder reranking into a single scored list.

### Core Operations

| Operation | Purpose | Returns |
|-----------|---------|---------|
| `retain` | Store memory with strategic metadata | Memory ID |
| `recall` | Multi-strategy retrieval | Scored memory list with metadata |
| `reflect` | Weighted synthesis across memories | Ranked operating directive |
| `decay_check` | Evaluate decay conditions | List of memories to reassess |
| `reweight` | Recalculate strategic weights | Updated weight scores |

---

## TECH STACK

```
Runtime:       Docker (single container)
Database:      PostgreSQL 16 + pgvector extension
Embedding:     Local: nomic-embed-text via Ollama  |  API: OpenAI text-embedding-3-small
Graph:         Property graph tables in PostgreSQL (no Neo4j)
Reranker:      Cross-encoder (ms-marco-MiniLM-L-6-v2 via sentence-transformers)
LLM:           For fact extraction + reflect: configurable (default: claude-haiku-4-5)
API:           FastAPI (REST) + MCP server
SDKs:          Python (primary), TypeScript (secondary)
Language:      Python 3.12+
```

### Why PostgreSQL for everything

- pgvector handles semantic search
- Standard tables + recursive CTEs handle graph traversal
- Timestamp columns + window functions handle temporal queries
- JSON columns handle strategic metadata
- One database to manage, backup, and understand

---

## DATA MODEL

### memories table

```sql
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id         TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(768),

    -- Dimension 2: Temporal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_from      TIMESTAMPTZ,
    valid_until     TIMESTAMPTZ,

    -- Dimension 4: Strategic envelope (stored as JSONB)
    strategic       JSONB NOT NULL DEFAULT '{}',

    -- Metadata
    source_type     TEXT,
    supersedes      UUID[],
    is_active       BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT fk_bank FOREIGN KEY (bank_id) REFERENCES banks(id)
);

CREATE INDEX idx_memories_bank ON memories(bank_id);
CREATE INDEX idx_memories_embedding ON memories USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_memories_active ON memories(bank_id, is_active) WHERE is_active = true;
CREATE INDEX idx_memories_strategic ON memories USING gin (strategic);
CREATE INDEX idx_memories_created ON memories(bank_id, created_at DESC);
```

### Strategic JSONB Schema

Every memory's `strategic` field follows this structure:

```json
{
  "reasoning": "Free text explaining WHY this memory matters",
  "authority": {
    "source": "kali_planning_session | agent_execution | manual",
    "session_id": "optional reference to source session",
    "confidence": 0.0-1.0
  },
  "weight": {
    "base_score": 0.0-1.0,
    "factors": {
      "revenue_proximity": 0.0-1.0,
      "time_sensitivity": 0.0-1.0,
      "dependency_depth": 0.0-1.0,
      "execution_readiness": 0.0-1.0,
      "strategic_alignment": 0.0-1.0
    },
    "computed_at": "ISO timestamp"
  },
  "decay_conditions": [
    {
      "trigger": "date | event | threshold",
      "value": "2026-05-08 | revenue_target_met | 50000",
      "action": "reassess | deprioritize | archive | escalate"
    }
  ],
  "venture": "gaya | practicectrl | rope | openclaw | personal",
  "sprint_phase": "green | red | post_sprint",
  "dependencies": ["entity_id_1", "entity_id_2"],
  "conflicts_with": ["memory_id"],
  "tags": ["hunter", "scorer", "dtc"]
}
```

### entities table

```sql
CREATE TABLE entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    entity_type     TEXT,
    aliases         TEXT[],
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(bank_id, name)
);
```

### entity_relationships table

```sql
CREATE TABLE entity_relationships (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id         TEXT NOT NULL,
    source_entity   UUID NOT NULL REFERENCES entities(id),
    target_entity   UUID NOT NULL REFERENCES entities(id),
    relationship    TEXT NOT NULL,
    properties      JSONB DEFAULT '{}',
    valid_from      TIMESTAMPTZ DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_rel_source ON entity_relationships(source_entity);
CREATE INDEX idx_rel_target ON entity_relationships(target_entity);
CREATE INDEX idx_rel_bank ON entity_relationships(bank_id);
```

### memory_entities junction table

```sql
CREATE TABLE memory_entities (
    memory_id       UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id       UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role            TEXT,
    PRIMARY KEY (memory_id, entity_id)
);
```

### banks table

```sql
CREATE TABLE banks (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    mission         TEXT,
    directives      TEXT[],
    disposition     JSONB DEFAULT '{}',
    weight_factors  JSONB NOT NULL DEFAULT '{
      "revenue_proximity": 0.30,
      "time_sensitivity": 0.25,
      "dependency_depth": 0.20,
      "execution_readiness": 0.15,
      "strategic_alignment": 0.10
    }',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Bank Configuration Example

```json
{
  "id": "gaya_operations",
  "name": "Gaya Wellness Operations",
  "mission": "Generate recurring revenue from Gaya Wellness DTC offers",
  "directives": [
    "GREEN only: sell and deliver existing products",
    "RED forbidden: no new product builds until post-sprint",
    "DTC leads convert 3x faster than B2B — always weight accordingly",
    "Escalate to planning layer for decisions above $10K commitment"
  ],
  "disposition": {
    "risk_tolerance": "moderate",
    "decision_speed": "bias_toward_action",
    "ambiguity_handling": "escalate_if_revenue_impacting"
  },
  "weight_factors": {
    "revenue_proximity": 0.30,
    "time_sensitivity": 0.25,
    "dependency_depth": 0.20,
    "execution_readiness": 0.15,
    "strategic_alignment": 0.10
  }
}
```

---

## PROJECT STRUCTURE

```
anamnesis/
├── docker-compose.yml
├── Dockerfile
├── README.md
├── pyproject.toml
├── anamnesis/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Environment config
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py            # REST endpoints
│   │   └── mcp_server.py        # MCP protocol handler
│   ├── core/
│   │   ├── __init__.py
│   │   ├── retain.py
│   │   ├── recall.py
│   │   ├── reflect.py
│   │   ├── decay.py
│   │   └── reweight.py
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── semantic.py          # pgvector cosine similarity
│   │   ├── keyword.py           # BM25 via tsvector
│   │   ├── graph.py             # Recursive CTE traversal
│   │   ├── temporal.py          # Time-aware filtering
│   │   ├── fusion.py            # Reciprocal rank fusion
│   │   └── reranker.py          # Cross-encoder reranking
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── facts.py             # LLM fact extraction
│   │   └── entities.py          # Entity extraction + resolution
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py        # Async PostgreSQL pool
│   │   ├── migrations/
│   │   │   └── 001_initial.sql
│   │   └── queries.py
│   └── models/
│       ├── __init__.py
│       ├── memory.py            # Pydantic models
│       ├── bank.py
│       └── strategic.py
├── sdk/
│   └── python/
│       ├── anamnesis_client/
│       │   ├── __init__.py
│       │   └── client.py
│       └── pyproject.toml
├── tests/
│   ├── test_retain.py
│   ├── test_recall.py
│   ├── test_reflect.py
│   ├── test_decay.py
│   ├── test_reweight.py
│   └── test_integration.py
└── scripts/
    ├── seed.py                  # Seed bank from markdown files
    └── benchmark.py             # Retrieval quality testing
```

---

## PHASE 1: Foundation (Days 1–3)

### Goal: retain + recall with semantic + BM25 retrieval working end to end

### Tasks

1. **Docker + PostgreSQL setup**
   - `docker-compose.yml` with PostgreSQL 16 + pgvector
   - Init script runs `001_initial.sql` on first boot
   - FastAPI app on port 8888
   - Health check at `GET /health`

2. **Database schema** — create all tables from Data Model section

3. **Embedding pipeline**
   - Config toggle: `EMBEDDING_PROVIDER=ollama|openai`
   - Ollama: `nomic-embed-text` (768 dim)
   - OpenAI: `text-embedding-3-small` (1536 dim) — adjust vector column
   - Function: `async def embed(text: str) -> list[float]`

4. **retain operation**
   - Endpoint: `POST /v1/{bank_id}/retain`
   - Body: `{ "content": "string", "strategic": {...}, "source_type": "string" }`
   - Generate embedding, store memory row, generate tsvector for BM25
   - Skip fact extraction and entity resolution in Phase 1
   - Return memory ID

5. **recall operation (2 strategies)**
   - Endpoint: `POST /v1/{bank_id}/recall`
   - Body: `{ "query": "string", "limit": 10, "min_weight": null, "filters": {...} }`
   - Semantic search (pgvector cosine similarity)
   - BM25 search (tsvector ts_rank)
   - Reciprocal rank fusion (k=60)
   - Apply filters (venture, sprint_phase, time window, min_weight)
   - Return scored list with full strategic metadata

6. **Bank CRUD** — create, get, update bank config

7. **Python SDK (basic)** — `Anamnesis(base_url)` with `retain()`, `recall()`, `create_bank()`

### Phase 1 Tests
- [ ] Retain 10 memories with varying strategic metadata
- [ ] Recall semantic query returns relevant results
- [ ] Recall keyword query returns relevant results
- [ ] Fusion ranking puts best results at top
- [ ] Filters work (venture, time window, min_weight)
- [ ] Bank CRUD works

---

## PHASE 2: Graph + Temporal (Days 4–6)

### Goal: All four retrieval strategies working in parallel

### Tasks

1. **Fact extraction** — LLM extracts atomic facts from content on retain
2. **Entity extraction + resolution** — LLM extracts entities/relationships; alias resolution against existing entities in bank
3. **Graph traversal retrieval** — recursive CTE traverses entity relationships up to N hops; returns connected memories
4. **Temporal retrieval** — parse time references from query; filter by created_at, valid_from/valid_until; recency boost
5. **Parallel retrieval + full fusion** — all four strategies fire via `asyncio.gather`; reciprocal rank fusion merges results
6. **Cross-encoder reranker** — `ms-marco-MiniLM-L-6-v2` reranks fused results

### Phase 2 Tests
- [ ] Entity extraction produces correct entities
- [ ] Alias resolution links variant names to same entity
- [ ] Graph traversal finds memories 2 hops from query entity
- [ ] Temporal query returns only time-appropriate memories
- [ ] All four strategies return results concurrently
- [ ] Fusion + reranking beats any single strategy alone

---

## PHASE 3: Dimension 4 — Strategic Layer (Days 7–9)

### Goal: Weighted reflect produces ranked operating directives

### Tasks

1. **Weighted reflect** — recall all 4 strategies → assemble LLM prompt with bank mission/directives/disposition + retrieved memories ordered by weight → LLM produces ranked directive with reasoning
2. **Weight computation** — `weight = sum(bank.weight_factors[f] * memory.factors[f] for f in factors)`; recomputed on retain, reweight, and optionally during recall
3. **decay_check** — evaluate each active memory's decay conditions (date, event, threshold); execute actions (reassess, deprioritize, archive, escalate)
4. **reweight** — bulk recalculate weights when strategic context changes
5. **Supporting tables** — events, escalations, metrics

### Phase 3 Tests
- [ ] Reflect produces ranked directive with reasoning (not summary)
- [ ] Higher-weight memories rank first in directive
- [ ] Bank directives are respected in reflect output
- [ ] decay_check identifies expired memories
- [ ] reweight changes scores across all active memories
- [ ] Quality difference between reflect with/without strategic metadata is obvious

---

## PHASE 4: Integration + Polish (Days 10–12)

### Goal: MCP server, TypeScript SDK, admin UI, seed script, docs

### Tasks

1. **MCP server** — expose retain, recall, reflect as MCP tools; test with Claude Code and OpenClaw
2. **TypeScript SDK** — same operations as Python SDK
3. **Admin UI** — React app on port 9999; bank config editor, memory browser, entity graph viewer, escalation queue, decay monitor
4. **Seed script** — `python scripts/seed.py --bank gaya_ops --file KALI_CONTEXT.md`; parse markdown into memories; LLM generates strategic metadata
5. **Benchmark script** — test queries, measure retrieval relevance, compare with/without strategic weighting
6. **Documentation** — quickstart, API reference, bank config guide, strategic metadata authoring guide

### Phase 4 Tests
- [ ] MCP client can retain, recall, reflect
- [ ] TypeScript SDK passes same tests as Python
- [ ] Admin UI displays seeded memories
- [ ] Seed script processes real context file into 50+ memories
- [ ] Full round trip: seed → recall → reflect → retain new → recall again

---

## CONFIGURATION

### Environment Variables

```bash
DATABASE_URL=postgresql://anamnesis:password@localhost:5432/anamnesis
EMBEDDING_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OPENAI_API_KEY=sk-...
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
PORT=8888
ADMIN_PORT=9999
LOG_LEVEL=info
```

### Docker Compose

```yaml
version: '3.8'
services:
  anamnesis:
    build: .
    ports:
      - "8888:8888"
      - "9999:9999"
    environment:
      - DATABASE_URL=postgresql://anamnesis:password@db:5432/anamnesis
      - EMBEDDING_PROVIDER=ollama
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - LLM_PROVIDER=anthropic
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - LLM_MODEL=claude-haiku-4-5
    depends_on:
      - db
    volumes:
      - anamnesis_data:/app/data
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: anamnesis
      POSTGRES_USER: anamnesis
      POSTGRES_PASSWORD: password
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./anamnesis/db/migrations:/docker-entrypoint-initdb.d
volumes:
  pg_data:
  anamnesis_data:
```

---

## SUCCESS CRITERIA

1. Recall retrieves relevant memories across all four dimensions for a query like "what should I focus on for Gaya this week"
2. Reflect produces a ranked directive a reasonable person would agree with
3. Feature Forge agent calling recall at session start executes meaningfully better than without
4. Seeded bank answers questions better than raw markdown as context
5. Runs self-hosted on Mac Studio with no external deps beyond LLM API

---

## RISKS + MITIGATIONS

| Risk | Mitigation |
|------|-----------|
| Weight drift from outdated reasoning | decay_check on schedule; reweight on milestones; computed_at timestamps |
| Vague reasoning in retained memories | Reject if reasoning < 20 chars; warn if confidence < 0.5 |
| Over-complex weight formula | Start simple weighted sum; add complexity only when needed |
| LLM cost for reflect | Use Haiku; cache frequent patterns; allow recall-only mode |
| Memory pollution from low-quality retains | Authority hierarchy (planning > execution > manual); auto-consolidation |
| Reranker latency | Load model at startup; batch reranking; timeout with fallback to fusion-only |
