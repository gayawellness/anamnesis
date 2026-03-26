# Anamnesis — 4D Strategic Memory Engine

Anamnesis gives AI agents persistent, strategically-weighted memory across sessions. Instead of flat fact storage, it stores *why* decisions were made, *under what conditions* they change, and *how important* they are relative to everything else.

## Quick Start

```bash
# 1. Start PostgreSQL (requires pgvector extension)
brew services start postgresql@17

# 2. Create database
createdb anamnesis
psql anamnesis -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 3. Start the server
./anamnesis/scripts/start_server.sh
# or manually:
PYTHONPATH=/path/to/repo ANAMNESIS_EMBEDDING_PROVIDER=local \
  uvicorn anamnesis.api.app:create_app --host 127.0.0.1 --port 8400 --factory

# 4. Create a memory bank
curl -X POST http://localhost:8400/api/v1/banks \
  -H "Content-Type: application/json" \
  -d '{"name": "my_bank", "mission": "Track operational context", "directives": ["Priority 1"]}'

# 5. Store a memory
curl -X POST http://localhost:8400/api/v1/retain \
  -H "Content-Type: application/json" \
  -d '{"bank": "my_bank", "content": "We decided to prioritize X because Y", "reasoning": "Y is the fastest path to revenue", "authority": "explicit"}'

# 6. Query memories
curl -X POST http://localhost:8400/api/v1/recall \
  -H "Content-Type: application/json" \
  -d '{"bank": "my_bank", "query": "what are our priorities?"}'
```

## Core Operations

| Operation | Purpose | When to Use |
|-----------|---------|-------------|
| **retain** | Store a memory with strategic metadata | After decisions, learnings, outcomes |
| **recall** | 4D search (semantic + temporal + relational + strategic) | Need specific context on a topic |
| **reflect** | LLM synthesis into ranked directives | "What should I focus on?" questions |
| **decay_check** | Evaluate and archive stale memories | Periodic maintenance |
| **reweight** | Recalculate weights across a bank | After access pattern changes |

## Wiring Your Agent

### The Reboot Problem

AI agents lose all context when a session ends. Anamnesis solves this — but only if your agent knows to *call Anamnesis on startup*. Without proper wiring, a new session will:
- Not know Anamnesis exists
- Attempt to "fix" the intentional weight system
- Make decisions that conflict with established priorities

### Three-Step Integration

#### 1. Seed Your Bank

Quality in = quality out. The `reasoning` field is what makes reflect useful.

**Bad memory:**
```json
{"content": "We use PostgreSQL", "reasoning": "database choice"}
```

**Good memory:**
```json
{
  "content": "We chose PostgreSQL + pgvector over Pinecone for memory storage",
  "reasoning": "Need vector similarity search but also relational queries for entity graph. Pgvector gives both in one system. Pinecone would require a separate DB for relationships, doubling infrastructure cost and complexity.",
  "authority": "explicit",
  "content_type": "decision"
}
```

#### 2. Configure Your Agent's Boot Sequence

Use the CLI generator to produce a ready-to-paste boot protocol:

```bash
# For Claude Code (generates CLAUDE.md block)
python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format claude-code

# For OpenAI agents (generates system prompt)
python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format openai

# For any agent (generates curl instructions)
python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format generic

# Write to file
python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format claude-code --output CLAUDE.md
```

Paste the output into your agent's config file. The boot sequence ensures every session starts by calling `reflect` for strategic context and `recall` for recent handoff notes.

#### 3. Configure Session End Protocol

Sessions must retain outcomes before closing. The boot protocol includes this instruction, but enforce it in your workflow:

```json
{
  "bank": "my_bank",
  "content": "Session accomplished X, decided Y, next session should focus on Z",
  "content_type": "event",
  "reasoning": "Captures handoff context so next session starts oriented",
  "authority": "inferred",
  "source": "my-agent"
}
```

### MCP Integration (Claude Code)

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "anamnesis": {
      "command": "python3",
      "args": ["-m", "anamnesis.mcp.server"],
      "env": {
        "PYTHONPATH": "/path/to/repo",
        "ANAMNESIS_URL": "http://localhost:8400"
      }
    }
  }
}
```

The MCP tool descriptions include behavioral nudges that instruct Claude Code to call `reflect` first in every session.

## Common Pitfalls

### "My new session tried to rewrite Anamnesis"

This happens when a session starts without context. It sees the weight system, notices the ~4.0 ceiling on auto-calculated weights, and assumes it's a bug. It's not — the ceiling is intentional:

- Initial weights are calculated from authority + confidence + connectivity
- The scale goes to 10 to accommodate reweight cycles
- Reweight uses temporal access patterns to raise/lower weights over time
- Frequently accessed, highly connected memories naturally rise above the initial ceiling

**Fix:** Ensure your agent's config includes the boot sequence (Step 2 above).

### "Reflect gives vague answers"

Reflect quality depends on reasoning quality in retained memories. If you store facts without reasoning, reflect has nothing to synthesize from.

**Fix:** Always include `reasoning` when calling retain. Explain *why* the memory matters, not just *what* it is.

### "My agent ignores Anamnesis"

The MCP tool descriptions include instructions to call reflect first, but some agents may still need explicit instructions in their config file.

**Fix:** Use `generate-boot-prompt` to create a config block and paste it at the very top of your agent's instruction file (before any other content).

## Architecture

### Memory Banks

Isolated memory spaces. Each agent or project gets its own bank. Banks have:
- **Mission** — what the bank tracks
- **Directives** — standing priorities
- **Weight factors** — how to blend the 4 dimensions during recall
- **Decay settings** — when memories expire

### 4D Retrieval

Recall searches across four dimensions simultaneously:
- **Semantic** (0.30) — vector similarity via embeddings
- **Temporal** (0.20) — recency and access frequency
- **Relational** (0.20) — entity graph connections
- **Strategic** (0.30) — explicit weight from authority and validation

Results are fused using Reciprocal Rank Fusion (RRF).

### Weight System

Weights are **intentionally calibrated**. Do not modify the scoring logic.

| Source | Max Initial Weight | Can Reach via Reweight |
|--------|-------------------|----------------------|
| `explicit` (user/planning layer) | 8.0 | 10.0 |
| `system` (auto-extracted) | 2.0 | 6.0 |
| `inferred` (AI-derived) | 1.0 | 4.0 |

Initial weights are conservative. Reweight cycles raise them based on:
- Temporal access patterns (frequently used → higher weight)
- Entity connectivity (well-connected → higher weight)
- Confidence validation

### Entity Graph

Memories are automatically decomposed into subject-predicate-object triples. Entities are resolved and linked, creating a knowledge graph that the relational dimension searches during recall.

## CLI Reference

```bash
# Generate boot protocol
python3 -m anamnesis.cli generate-boot-prompt --bank <name> --format <format>

# Export a bank
python3 -m anamnesis.cli export --bank <name> --output backup.json

# Export all banks
python3 -m anamnesis.cli export --all --output full_backup.json

# Import from backup
python3 -m anamnesis.cli import --file backup.json

# Import with merge (don't overwrite)
python3 -m anamnesis.cli import --file backup.json --merge

# Diagnose scoring quality for a query
python3 -m anamnesis.cli diagnose-scoring --bank <name> --query "test query"

# Repair memories with missing or failed embeddings
python3 -m anamnesis.cli repair-embeddings --bank <name>
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/v1/health | Health check |
| POST | /api/v1/banks | Create a bank |
| GET | /api/v1/banks | List all banks |
| POST | /api/v1/retain | Store a memory |
| POST | /api/v1/recall | Search memories |
| POST | /api/v1/reflect | Strategic synthesis |
| POST | /api/v1/decay-check | Run decay check |
| POST | /api/v1/reweight | Recalculate weights |
| GET | /api/v1/export/{bank_name} | Export bank to JSON |
| POST | /api/v1/import | Import from JSON |

## Troubleshooting

### Understanding Recall Scores

Every recalled memory includes a `dimension_scores` breakdown showing how much each retrieval dimension contributed to the final ranking:

| Dimension | Range | What it measures |
|-----------|-------|-----------------|
| `semantic` | 0.0–1.0 | How conceptually similar the memory content is to your query, based on embedding vector similarity. Higher = closer semantic match. |
| `temporal` | 0.0–1.0 | Recency and time-relevance. More recently created or accessed memories score higher. |
| `relational` | 0.0–1.0 | Entity graph connections. Memories sharing entities (people, concepts, systems) with the query score higher. |
| `strategic` | 0.0–1.0 | The memory's strategic weight, normalized from its weight envelope (weight / 10 * weight_factor). Higher-weight memories score higher here. |

The final score is the sum of all four dimension scores, each scaled by the bank's configured `weight_factors` (default: semantic 30%, temporal 20%, relational 20%, strategic 30%).

### Red Flag Patterns

**All semantic scores nearly identical (e.g., all 0.003–0.004):** Score normalization is likely broken. In a healthy system, the top semantic match should score near 0.30 (the semantic weight factor) while poor matches score near 0. Run `diagnose-scoring` to verify:

```bash
python3 -m anamnesis.cli diagnose-scoring --bank my_bank --query "a known topic"
```

**Semantic scores are always zero:** Embeddings are not being generated. Check:
1. Your embedding provider is configured (`ANAMNESIS_EMBEDDING_PROVIDER` in `.env`)
2. If using Voyage, your `VOYAGE_API_KEY` is valid
3. Run `repair-embeddings` to fix any memories with missing vectors

**Strategic dimension dominates (>50% of total score for every result):** Check that your bank's `weight_factors` sum to approximately 1.0 and that no single factor exceeds 0.5.

### Embedding Repair

If memories were stored while the embedding provider was down, they won't appear in semantic search. Repair them:

```bash
# Repair embeddings for a specific bank
python3 -m anamnesis.cli repair-embeddings --bank my_bank

# Repair all banks
python3 -m anamnesis.cli repair-embeddings
```

The health endpoint (`GET /api/v1/health`) reports `memories_missing_embeddings` count so you can monitor this.
