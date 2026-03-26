# Changelog

## v0.2.0 — 2026-03-26

### Critical Fix: Recall Scoring Normalization

**Impact:** All users who cloned before this release have broken semantic recall. Memories are stored correctly but recall results are sorted by strategic weight instead of semantic relevance.

**Root cause:** RRF (reciprocal rank fusion) scores maxed at ~0.005 while strategic weight scores reached 0.30. Strategic weight was 60x more influential than semantic relevance, making semantic recall effectively random noise. All four dimensions were supposed to contribute proportionally (semantic 30%, temporal 20%, relational 20%, strategic 30%) but the un-normalized RRF scores meant strategic dominated everything.

**Fix:** Normalized all RRF dimension scores to [0,1] before applying weight factors. Each dimension now contributes proportionally to its configured weight factor.

**Before:** Query "current revenue MRR" returns random high-weight memories (social media specs, autonomy directives) with semantic scores of 0.003-0.004.

**After:** Same query returns MRR memory at top semantic rank with score 0.3000, correctly balanced against other dimensions.

### New Features

- **Scoring unit tests** (`tests/test_scoring.py`) — 7 tests that verify dimension balance, normalization bounds, and prevent this class of bug from recurring
- **Enhanced health endpoint** (`GET /api/v1/health`) — now reports embedding provider, embedding health status, scoring normalization state, count of memories missing embeddings, and uptime
- **Startup validation** — on boot, Anamnesis checks embedding provider health, counts failed embeddings, and runs a scoring self-test (all non-blocking, logs only)
- **Embedding failure resilience** — retain now retries once on embedding failure, falls back to local embeddings if primary fails, and stores memory with `embedding_status=failed` if all attempts fail (instead of crashing)
- **`repair-embeddings` CLI** — regenerates embeddings for memories that failed: `python3 -m anamnesis.cli repair-embeddings --bank <name>`
- **`diagnose-scoring` CLI** — shows dimension contribution percentages and flags pathological patterns: `python3 -m anamnesis.cli diagnose-scoring --bank <name> --query "test"`
- **Troubleshooting docs** — README section explaining dimension scores, red flag patterns, and embedding repair

### Database Migration

- Added `embedding_status` column to `memories` table (auto-migrated on startup, defaults to `'complete'`)

## v0.1.0 — 2026-03-22

Initial release: 4D Strategic Memory Engine with retain, recall, reflect, decay_check, reweight, entity graph, MCP server, Python SDK, CLI tools, and Docker support.
