# Anamnesis Memory Schema

Reference documentation for the structure, validation rules, and weight mechanics
of memories stored in Anamnesis.

---

## Table of Contents

- [Memory Fields](#memory-fields)
- [Strategic Envelope](#strategic-envelope)
- [Memory Banks](#memory-banks)
- [Authority Hierarchy](#authority-hierarchy)
- [Weight Calculation](#weight-calculation)
- [Decay Conditions](#decay-conditions)
- [Tag Conventions](#tag-conventions)
- [Entity Graph](#entity-graph)
- [Examples](#examples)
- [Reasoning Quality Guide](#reasoning-quality-guide)

---

## Memory Fields

Every memory stored via the `retain` endpoint consists of the following fields:

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `bank` | `string` | Name of the target memory bank. Must match an existing bank. |
| `content` | `string` | The memory itself — a natural-language statement of what happened, was decided, or was learned. |

### Optional Fields (with defaults)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content_type` | `enum` | `"fact"` | Category of memory. One of: `fact`, `decision`, `observation`, `instruction`, `event`. |
| `source` | `string` | `"unknown"` | Who or what created this memory (e.g., `"claude-code"`, `"user"`, `"api-sync"`). |
| `reasoning` | `string` | `null` | Strategic reasoning explaining **why** this memory matters. Critical for reflect quality. |
| `authority` | `enum` | `"inferred"` | Trust level of the source. One of: `explicit`, `system`, `inferred`. See [Authority Hierarchy](#authority-hierarchy). |
| `confidence` | `float` | `0.8` | Source's confidence in the memory's accuracy. Range: `0.0` to `1.0`. |
| `decay_condition` | `string` | `null` | When this memory should expire. See [Decay Conditions](#decay-conditions). |
| `tags` | `string[]` | `[]` | Categorical labels for filtering and grouping. See [Tag Conventions](#tag-conventions). |
| `supersedes` | `uuid[]` | `[]` | IDs of memories this one replaces. Superseded memories are marked `superseded` status. |
| `depends_on` | `uuid[]` | `[]` | IDs of memories this one logically depends on. |

### System-Managed Fields (read-only)

These are set automatically and returned in responses but cannot be set via the API:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `uuid` | Unique identifier, generated on insert. |
| `bank_id` | `uuid` | Foreign key to the parent memory bank. |
| `embedding` | `vector` | Dense vector representation for semantic search (default 1024 dimensions). |
| `created_at` | `timestamptz` | When the memory was retained. |
| `last_accessed_at` | `timestamptz` | Last time this memory was retrieved via recall or reflect. |
| `access_count` | `integer` | Total number of times retrieved. |
| `weight` | `float` | Strategic importance score (0.0 to 10.0). See [Weight Calculation](#weight-calculation). |
| `status` | `enum` | Lifecycle state: `active`, `decayed`, `archived`, `superseded`. |
| `decayed_at` | `timestamptz` | When the memory was marked as decayed (null if active). |
| `superseded_by` | `uuid` | ID of the memory that replaced this one (null if not superseded). |
| `extracted_facts` | `jsonb` | Subject-predicate-object triples extracted by LLM during retain. |
| `search_vector` | `tsvector` | Auto-generated full-text search index over `content`. |

---

## Strategic Envelope

The "strategic envelope" is the combination of fields that give a memory its
strategic weight and context — beyond just the raw content. These fields are
what separate a useful memory from noise:

```
┌──────────────────────────────────────────────────────┐
│  content        "We decided to focus on B2B first"   │
│  ─────────────────────────────────────────────────── │
│  reasoning      "B2B has shorter sales cycles and    │
│                  our pipeline already has 3 warm      │
│                  leads. B2C requires ad spend we      │
│                  don't have budget for."              │
│  authority      "explicit"                           │
│  confidence     0.95                                 │
│  content_type   "decision"                           │
│  tags           ["strategy", "revenue", "q1-sprint"] │
│  decay_condition "after:90d"                         │
│  depends_on     ["<uuid of market analysis memory>"] │
│  supersedes     ["<uuid of old 'focus on B2C' mem>"] │
└──────────────────────────────────────────────────────┘
```

The envelope is what enables reflect to produce ranked operating directives
rather than flat summaries. Without reasoning, reflect has nothing to
synthesize against the bank's mission and directives.

---

## Memory Banks

A memory bank is an isolated namespace that groups related memories.
Each bank has its own mission, directives, and retrieval tuning.

### Bank Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `string` | *(required)* | Unique identifier for the bank. Use lowercase with underscores (e.g., `project_alpha`). |
| `mission` | `string` | *(required)* | The bank's purpose. Used as context in reflect synthesis. |
| `directives` | `string[]` | `[]` | Priority-ordered instructions that guide reflect's synthesis. |
| `disposition` | `string` | `"balanced"` | Personality modifier for reflect output (e.g., `"balanced"`, `"aggressive"`, `"conservative"`). |
| `weight_factors` | `object` | See below | Dimension weights for 4D recall scoring. |
| `default_decay_days` | `integer` | `90` | Default TTL for memories without explicit decay conditions. |

### Default Weight Factors

```json
{
  "semantic": 0.30,
  "temporal": 0.20,
  "relational": 0.20,
  "strategic": 0.30
}
```

These control how recall scores are blended across the four retrieval dimensions:
- **Semantic** — vector similarity between query and memory content
- **Temporal** — recency and access frequency
- **Relational** — entity graph connectivity
- **Strategic** — memory weight (authority, confidence, connectivity)

---

## Authority Hierarchy

Authority determines the trust level of a memory and directly caps its
initial weight. The system currently supports three authority levels:

| Authority | Who Sets It | Initial Weight Cap | Description |
|-----------|------------|-------------------|-------------|
| `explicit` | User or planning layer | **8.0** | The user stated this directly. Highest trust for user-sourced memories. |
| `system` | Automated processes | **2.0** | Auto-extracted by system processes (e.g., data syncs, scrapers). |
| `inferred` | AI agents | **1.0** | AI-derived from context. Lowest initial trust — must earn weight via reweight. |

### Authority Multipliers (used in weight formula)

| Authority | Multiplier |
|-----------|-----------|
| `explicit` | 2.0 |
| `system` | 1.5 |
| `inferred` | 1.0 |

### Why Inferred Memories Start Low

This is intentional, not a bug. AI-generated memories begin at low weight because
they haven't been validated. The reweight cycle gradually increases weight for
memories that prove useful (accessed frequently, connected to many entities). This
prevents an agent from flooding a bank with high-weight noise.

---

## Weight Calculation

### Initial Weight (on retain)

```
raw_weight = authority_multiplier * confidence * connectivity_bonus
weight = clamp(raw_weight, 0.0, authority_cap)
```

Where:
- `authority_multiplier` — see table above (explicit=2.0, system=1.5, inferred=1.0)
- `confidence` — the confidence value submitted with the memory (0.0 to 1.0)
- `connectivity_bonus` — `1.0 + 0.1 * min(entity_count, 10)` — memories linked to more entities get a small boost

**Example calculations:**

| Authority | Confidence | Entities | Raw Weight | Cap | Final Weight |
|-----------|-----------|----------|-----------|-----|-------------|
| explicit | 0.95 | 3 | 2.0 * 0.95 * 1.3 = 2.47 | 8.0 | **2.47** |
| explicit | 0.9 | 8 | 2.0 * 0.9 * 1.8 = 3.24 | 8.0 | **3.24** |
| system | 0.8 | 2 | 1.5 * 0.8 * 1.2 = 1.44 | 2.0 | **1.44** |
| system | 0.9 | 5 | 1.5 * 0.9 * 1.5 = 2.03 | 2.0 | **2.0** (capped) |
| inferred | 0.8 | 0 | 1.0 * 0.8 * 1.0 = 0.80 | 1.0 | **0.80** |
| inferred | 0.9 | 4 | 1.0 * 0.9 * 1.4 = 1.26 | 1.0 | **1.0** (capped) |

### Reweight Formula (periodic recalculation)

The reweight operation recalculates weights for all active memories in a bank.
It introduces a **temporal factor** that is not present in initial weight calculation:

```
weight = authority_base * confidence * temporal_factor * connectivity_factor
weight = clamp(weight, 0.0, 10.0)
```

Where:
- `authority_base` — same multipliers as initial calculation
- `confidence` — stored confidence value
- `temporal_factor` — `0.5 + 0.5 * (1.0 / (1.0 + days_since_access / 30.0))`
- `connectivity_factor` — `1.0 + 0.1 * min(entity_connections, 10)`

**Temporal factor behavior:**
- Just accessed (0 days): `0.5 + 0.5 * 1.0 = 1.0`
- 30 days since access: `0.5 + 0.5 * 0.5 = 0.75`
- 90 days since access: `0.5 + 0.5 * 0.25 = 0.625`
- 365 days since access: `0.5 + 0.5 * 0.076 = 0.538`

The temporal factor ranges from 0.5 (very stale) to 1.0 (just accessed), meaning
frequently-recalled memories maintain higher weight while unused memories gradually
fade but never drop below half their base weight.

### Reweight Ceiling per Authority

The global reweight cap is currently **10.0** for all authority levels. The planned
per-authority ceilings (from the hardening roadmap) are:

| Authority | Initial Cap | Reweight Ceiling (planned) |
|-----------|------------|--------------------------|
| `explicit` | 8.0 | 10.0 |
| `system` | 2.0 | 6.0 |
| `inferred` | 1.0 | 4.0 |

> **Note:** Per-authority reweight ceilings are documented here as the target
> design. Until enforced in code, the global cap of 10.0 applies during reweight.

---

## Decay Conditions

Decay conditions define when a memory should automatically transition from
`active` to `decayed` status. Decayed memories are excluded from recall and
reflect results.

### Valid Formats

| Format | Example | Behavior |
|--------|---------|----------|
| `after:Nd` | `after:30d` | Decay N days after creation |
| `after:Nw` | `after:4w` | Decay N weeks after creation |
| `when:superseded` | `when:superseded` | Decay when another memory supersedes this one |
| `when:unaccessed:Nd` | `when:unaccessed:60d` | Decay if not accessed in N days |
| `never` | `never` | Never decay (permanent memory) |
| *(null/omitted)* | | No automatic decay; subject to bank-level default |

### Choosing a Decay Condition

- **Sprint-specific decisions:** `after:90d` — relevant for a quarter, then fade
- **Versioned facts** (e.g., pricing, config): `when:superseded` — valid until replaced
- **Session outcomes:** `when:unaccessed:60d` — keep if useful, fade if forgotten
- **Core identity/mission memories:** `never` — always relevant
- **Temporary context:** `after:7d` or `after:2w` — short-lived relevance

---

## Tag Conventions

Tags are freeform string arrays, but consistent tagging improves recall filtering.

### Recommended Tag Patterns

| Pattern | Examples | Purpose |
|---------|----------|---------|
| Domain area | `strategy`, `revenue`, `infrastructure`, `operations` | Filter by business domain |
| Temporal scope | `q1-2026`, `sprint-3`, `weekly` | Filter by time period |
| Priority marker | `critical`, `blocker`, `nice-to-have` | Quick priority filtering |
| Content marker | `architecture`, `do-not-modify`, `deprecated` | Special handling rules |
| Source context | `session-summary`, `handoff`, `standup` | Track where knowledge came from |
| System marker | `auto-extracted`, `needs-review` | Flag automated content |

### Tag Rules

- Use lowercase with hyphens (e.g., `do-not-modify`, not `DoNotModify`)
- Keep tags short and consistent across a bank
- Avoid overly specific tags that only one memory will ever use
- Memories tagged `architecture` or `do-not-modify` are surfaced in boot briefings as rules

---

## Entity Graph

Anamnesis extracts entities from memory content and builds a knowledge graph.
This powers the **relational** dimension of 4D recall.

### How It Works

1. During `retain`, if an LLM client is configured, the system extracts
   **subject-predicate-object triples** from the content
2. Subjects and objects become **entities** in the graph
3. Predicates become **edges** between entities
4. Each entity is linked to the memories it appears in via the `memory_entities` junction table

### Entity Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Canonical entity name (case-insensitive matching) |
| `entity_type` | `string` | Category (default: `"concept"`) |
| `aliases` | `string[]` | Alternative names for fuzzy matching |
| `description` | `string` | Optional description |

### Edge Fields

| Field | Type | Description |
|-------|------|-------------|
| `source_entity_id` | `uuid` | Origin entity |
| `target_entity_id` | `uuid` | Target entity |
| `relation_type` | `string` | The predicate (e.g., `"depends on"`, `"replaces"`, `"owns"`) |
| `weight` | `float` | Edge strength (default: 1.0) |
| `memory_id` | `uuid` | The memory this relationship was extracted from |

---

## Examples

### Low-Weight Memory (inferred, minimal envelope)

```json
{
  "bank": "project_alpha",
  "content": "The login page loaded slowly during testing.",
  "content_type": "observation",
  "source": "test-runner",
  "authority": "inferred",
  "confidence": 0.7,
  "tags": ["performance"]
}
```

**Resulting weight:** `1.0 * 0.7 * 1.0 = 0.70` (capped at 1.0, so **0.70**)

This memory has minimal strategic value — no reasoning, no connections, no
decay condition. It will exist but carry little influence in reflect.

---

### Medium-Weight Memory (system, with reasoning)

```json
{
  "bank": "project_alpha",
  "content": "API response times averaged 340ms this week, up from 280ms last week.",
  "content_type": "fact",
  "source": "monitoring-agent",
  "reasoning": "21% performance degradation may indicate a scaling issue. Correlates with the database migration deployed on Monday. Worth investigating before the next release.",
  "authority": "system",
  "confidence": 0.9,
  "decay_condition": "after:30d",
  "tags": ["performance", "api", "monitoring"],
  "depends_on": ["<uuid of db-migration memory>"]
}
```

**Resulting weight:** `1.5 * 0.9 * 1.0 = 1.35` (capped at 2.0, so **1.35**)

This memory has useful reasoning that connects the observation to a cause.
Reflect can use this to surface the performance trend and suggest investigation.

---

### High-Weight Memory (explicit, full envelope)

```json
{
  "bank": "project_alpha",
  "content": "Revenue target for Q2 is $50K MRR. Current run rate is $18K. Primary growth lever is enterprise tier upsells from existing accounts.",
  "content_type": "decision",
  "source": "user",
  "reasoning": "Enterprise upsells have a 40% close rate vs 8% for cold outbound. Three accounts are in active expansion conversations. Focusing here maximizes revenue per hour of effort. Cold outbound should continue at maintenance level but not absorb primary focus.",
  "authority": "explicit",
  "confidence": 0.95,
  "decay_condition": "after:90d",
  "tags": ["strategy", "revenue", "q2-target", "critical"],
  "supersedes": ["<uuid of old Q1 target memory>"]
}
```

**Resulting weight:** `2.0 * 0.95 * 1.0 = 1.90` (capped at 8.0, so **1.90**)

Even though this starts at 1.90, as entities are extracted and linked, and the
memory is accessed frequently through reflect, reweight cycles will increase it.
The rich reasoning gives reflect the context it needs to prioritize correctly.

---

### Maximum-Weight Memory (explicit, high connectivity, well-accessed)

After several reweight cycles, a frequently-accessed explicit memory with 6 entity
connections might look like:

```
weight = 2.0 * 0.95 * 1.0 * 1.6 = 3.04
```

With temporal_factor at 1.0 (just accessed) and connectivity_factor at 1.6
(6 entities), the memory reaches its natural maximum for its inputs. Reweight
cycles keep it elevated as long as it remains actively recalled.

---

## Reasoning Quality Guide

The `reasoning` field is the most underestimated field in the schema. It is
the primary input to reflect's synthesis quality. Without good reasoning,
reflect can only summarize content — it cannot produce strategic directives.

### Bad Reasoning vs. Good Reasoning

Consider a memory about choosing a tech stack:

**The memory content (same in both cases):**
> "We chose PostgreSQL over MongoDB for the data layer."

---

**Bad reasoning:**

> "PostgreSQL is a good database."

This tells reflect nothing. Why PostgreSQL? Why not MongoDB? What constraints
drove the decision? When reflect synthesizes a response to "What should I
consider before adding a new data model?" it has no strategic context to work
with. It can only parrot: "You use PostgreSQL."

---

**Good reasoning:**

> "MongoDB would have required separate infrastructure for full-text search
> and vector similarity. PostgreSQL with pg_trgm and pgvector extensions
> consolidates all three capabilities (relational, full-text, vector) into a
> single service, reducing operational complexity from 3 services to 1.
> This matters because we're a small team and every additional service is a
> maintenance burden. The trade-off is that PostgreSQL's document storage is
> less flexible than MongoDB's, but our schema is stable enough that this
> isn't a concern."

Now reflect has everything it needs:
- **The constraint** (small team, operational burden)
- **The trade-off** (flexibility vs simplicity)
- **The assumption** (stable schema)
- **When to revisit** (if schema becomes highly dynamic)

When an agent asks "Should we add a Redis cache?" reflect can synthesize:
*"The team has an explicit preference for consolidating infrastructure to
reduce maintenance burden [1]. Before adding Redis, verify that PostgreSQL's
built-in caching and connection pooling aren't sufficient. Previous decisions
prioritized fewer services over optimal per-service performance."*

### The Rule of Thumb

Good reasoning answers three questions:
1. **Why this choice over alternatives?** (decision rationale)
2. **What constraints drove it?** (context that may change)
3. **When would this need revisiting?** (invalidation conditions)

If your reasoning doesn't address at least two of these, reflect's synthesis
will be shallow.

---

## Retain API Response

When a memory is successfully retained, the response includes:

```json
{
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "extracted_facts": [
    {
      "subject": "project_alpha",
      "predicate": "uses",
      "object": "PostgreSQL"
    }
  ],
  "entities_linked": ["project_alpha", "PostgreSQL"],
  "weight": 1.90,
  "weight_note": "Weight 1.9 assigned via explicit authority (base=2.0, confidence=0.95, connectivity=1.0)."
}
```

If the weight was capped, `weight_note` explains why:

```json
{
  "weight": 1.0,
  "weight_note": "Initial weight capped at 1.0 for inferred-authority source (raw: 1.26). Use reweight cycles to increase based on validation."
}
```

---

## Database Schema Summary

Anamnesis uses PostgreSQL with the `pgvector` and `pg_trgm` extensions.

### Tables

| Table | Purpose |
|-------|---------|
| `memory_banks` | Bank configuration (mission, directives, weight factors) |
| `memories` | All stored memories with embeddings, metadata, and strategic envelope |
| `entities` | Knowledge graph nodes (people, concepts, systems) |
| `entity_edges` | Knowledge graph edges (relationships between entities) |
| `memory_entities` | Junction table linking memories to entities |
| `memory_accesses` | Access log for temporal scoring |

### Key Indexes

- `idx_memories_bank_id` — fast lookup by bank
- `idx_memories_status` — filter active/decayed/archived
- `idx_memories_search` — GIN index on `search_vector` for full-text search
- `idx_memories_tags` — GIN index on `tags` array for tag filtering
- Vector index on `embedding` for semantic similarity search (pgvector)
