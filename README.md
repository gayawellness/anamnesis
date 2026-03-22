# Anamnesis

**4D Strategic Memory Engine for Autonomous AI Agents**

Most AI memory systems store facts. Anamnesis stores *wisdom* — not just what happened, but **why it matters**, **how much it matters** relative to everything else, and **when it stops being true**.

```
Normal AI memory is a filing cabinet.
Anamnesis is a chief of staff who's read every file, knows the priorities,
understands the deadlines, and gives you a briefing every morning.
```

## The Problem

Every memory system today (Mem0, Zep, Hindsight, RAG) solves retrieval — "find the relevant thing." None of them solve judgment — "what matters most right now and why."

Your agents can remember that "Enterprise leads are a priority" but cannot remember *why* that decision was made, *under what conditions* it should change, or *how it ranks* against competing priorities.

The result: agents that remember everything but understand nothing.

## The Solution: 4 Dimensions of Memory

Anamnesis retrieves memories across four dimensions simultaneously:

| Dimension | What It Finds | Example |
|-----------|---------------|---------|
| **Semantic** | Conceptually related memories | "lead prioritization" → finds "enterprise converts at 34%" |
| **Temporal** | Recent and frequently accessed | "current priorities" → filters to active context |
| **Relational** | Connected entities | "API v2 dependencies" → finds engineering, enterprise, pipeline |
| **Strategic** | Weighted by importance + reasoning | "what matters most" → ranked by strategic weight with full reasoning |

Dimension 4 is what's new. Every memory carries a **strategic envelope**:

- **Reasoning** — *why* this was stored
- **Authority** — who decided this (explicit > system > inferred)
- **Weight** — how important relative to everything else (0-10)
- **Decay conditions** — when this stops being true
- **Dependencies** — what other memories this connects to
- **Supersedes** — what older memories this replaces

## Quickstart (60 seconds)

```bash
# 1. Clone and configure
git clone https://github.com/shwetapateldr/anamnesis.git
cd anamnesis
cp .env.example .env
# Edit .env with your API keys (Anthropic or OpenAI + optionally Voyage AI)

# 2. Start everything
docker compose up -d

# 3. Create a memory bank
curl -X POST http://localhost:8400/api/v1/banks \
  -H "Content-Type: application/json" \
  -d '{"name": "my_project", "mission": "Ship the product", "directives": ["Revenue first", "Ship weekly"]}'

# 4. Store a memory with reasoning
curl -X POST http://localhost:8400/api/v1/retain \
  -H "Content-Type: application/json" \
  -d '{
    "bank": "my_project",
    "content": "Enterprise customers convert at 34% from demo",
    "reasoning": "2x better than SMB. Focus sales energy here.",
    "authority": "explicit",
    "tags": ["revenue", "enterprise"]
  }'

# 5. Get a strategic briefing
curl -X POST http://localhost:8400/api/v1/reflect \
  -H "Content-Type: application/json" \
  -d '{
    "bank": "my_project",
    "question": "What should I focus on today?",
    "context": "3 engineers, $50K runway"
  }'
```

The `reflect` response isn't a list of facts. It's a **ranked operating directive** with reasoning, citations, and gap analysis.

## Python SDK

```bash
pip install httpx  # the only dependency
```

```python
from anamnesis.sdk import AnamnesisClient

client = AnamnesisClient(base_url="http://localhost:8400")

# Store a decision with full reasoning
client.retain(
    bank="my_project",
    content="Paused mobile app to focus on API v2",
    reasoning="API v2 unlocks $200K enterprise pipeline. Mobile serves free users only.",
    authority="explicit",
    tags=["decision", "prioritization"],
    decay_condition="after:90d",  # revisit in 90 days
)

# Get today's strategic briefing
directive = client.reflect(
    bank="my_project",
    question="What should I focus on this week?",
)
print(directive["synthesis"])

# Search with 4D retrieval
memories = client.recall(
    bank="my_project",
    query="What converts best?",
    min_weight=3.0,
)
```

## MCP Server (Claude Code / Any MCP Client)

Anamnesis ships with an MCP server for direct integration with Claude Code:

```json
// .mcp.json
{
  "mcpServers": {
    "anamnesis": {
      "command": "python",
      "args": ["-m", "anamnesis.mcp.server"],
      "env": {
        "ANAMNESIS_URL": "http://localhost:8400"
      }
    }
  }
}
```

7 tools available: `anamnesis_retain`, `anamnesis_recall`, `anamnesis_reflect`, `anamnesis_remember`, `anamnesis_search_entities`, `anamnesis_list_banks`, `anamnesis_bank_stats`

## CLI

```bash
# Strategic briefing
python -m anamnesis.cli reflect --bank my_project --question "What should I focus on?"

# Store a decision
python -m anamnesis.cli retain --bank my_project \
  --content "Chose Postgres over MongoDB" \
  --reasoning "Need pgvector for embeddings, ACID for memory integrity"

# Search
python -m anamnesis.cli recall --bank my_project --query "database decisions"

# Health check
python -m anamnesis.cli health
```

## Architecture

```
┌─────────────────────────────────────────┐
│  Planning Layer (human or AI)           │
│  → Decisions with reasoning → retain()  │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  ANAMNESIS                              │
│  FastAPI REST API + MCP Server          │
│  PostgreSQL + pgvector                  │
│                                         │
│  4D Retrieval → RRF Fusion →            │
│  Strategic Weight Boost → Reranking     │
│                                         │
│  Operations:                            │
│    retain    → store with reasoning     │
│    recall    → 4D search                │
│    reflect   → weighted synthesis       │
│    decay     → prune stale memories     │
│    reweight  → recalculate priorities   │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  Execution Layer (any agent/model)      │
│  → recall() + reflect() before acting   │
│  → retain() outcomes back               │
└─────────────────────────────────────────┘
```

## Core Operations

| Operation | What It Does |
|-----------|-------------|
| **retain** | Store a memory with embedding, fact extraction, entity resolution, and strategic weight calculation |
| **recall** | 4D parallel retrieval (semantic + fulltext + temporal + relational) with reciprocal rank fusion and strategic weight boost |
| **reflect** | LLM-powered synthesis that produces ranked operating directives weighted by strategic importance — not summaries |
| **decay_check** | Evaluate decay conditions and archive stale memories automatically |
| **reweight** | Recalculate strategic weights across a bank when context changes |

## Memory Banks

Banks are namespaced memory collections with their own strategic configuration:

```python
client.create_bank(
    name="sales_ops",
    mission="Close enterprise deals and grow pipeline",
    directives=[
        "Enterprise ($10K+ ACV) over SMB",
        "Product-led trials convert best — don't cold call",
        "Q2 target: $200K pipeline",
    ],
    disposition="balanced",  # or "aggressive", "conservative"
    weight_factors={
        "semantic": 0.30,    # conceptual relevance
        "temporal": 0.20,    # recency
        "relational": 0.20,  # entity connections
        "strategic": 0.30,   # importance weight
    },
)
```

The bank's mission and directives shape how `reflect` synthesizes answers. Different banks can have different personalities.

## Embedding Providers

| Provider | Quality | Cost | Setup |
|----------|---------|------|-------|
| **Voyage AI** (recommended) | Best | $0.02/1M tokens (200M free) | `VOYAGE_API_KEY` |
| **Local** (sentence-transformers) | Good | Free | No key needed |

## LLM Providers (for reflect + fact extraction)

| Provider | Model | Setup |
|----------|-------|-------|
| **Anthropic** (recommended) | claude-haiku-4-5 | `ANTHROPIC_API_KEY` |
| **OpenAI** | gpt-4o-mini | `OPENAI_API_KEY` |

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANAMNESIS_DB_HOST` | No | localhost | PostgreSQL host |
| `ANAMNESIS_DB_PORT` | No | 5432 | PostgreSQL port |
| `ANAMNESIS_DB_NAME` | No | anamnesis | Database name |
| `ANAMNESIS_DB_USER` | No | anamnesis | Database user |
| `ANAMNESIS_DB_PASSWORD` | No | anamnesis_dev | Database password |
| `ANAMNESIS_EMBEDDING_PROVIDER` | No | voyage | `voyage` or `local` |
| `VOYAGE_API_KEY` | If voyage | — | Voyage AI API key |
| `ANTHROPIC_API_KEY` | Recommended | — | For reflect + fact extraction |
| `ANAMNESIS_PORT` | No | 8400 | Server port |
| `ANAMNESIS_API_KEY` | No | — | API key for auth |

## The Name

*Anamnesis* (Greek: ἀνάμνησις) — the philosophical concept that knowledge is not learned but *remembered*. The engine gives agents access to judgment they did not generate themselves, stored by a planning layer that did.

## Why This Matters

Every agent framework today has the same memory gap. They can retrieve facts but can't make judgment calls. Anamnesis bridges that gap by storing the *reasoning* alongside the *knowledge*.

Intelligence knows the answer. Wisdom knows which answer matters. Anamnesis gives your agents wisdom.

## License

MIT — use it for anything, commercially or otherwise.

## Contributing

PRs welcome. If you're building on Anamnesis, we'd love to hear about it.
