# ANAMNESIS — Post-Launch Hardening Spec

> **For Claude Code:** Read this entire document before starting. These are product-level 
> fixes to the Anamnesis repo that prevent new users from hitting a critical onboarding 
> failure. Build in order. Test each section before moving to the next.

> **Context:** The first real-world reboot after installing Anamnesis exposed a critical 
> gap — new sessions don't know Anamnesis exists and attempt to "fix" its intentional 
> design decisions. Additionally, the reflect operation identified six architectural gaps 
> that need to be addressed before public launch.

> **IMPORTANT:** You are implementing changes to the Anamnesis product for ALL users, 
> not just for our local setup. Think like a product engineer, not a personal assistant.

---

## FIX 1: Boot Protocol Generator (CLI)

### Problem
New users install Anamnesis, wire it to their agent, and everything works — until they 
start a new session. The new session has no idea Anamnesis exists and either ignores it 
or tries to modify it. There is no automated way to generate the "first breath" 
instructions that an agent needs in its config file.

### Solution
Add a CLI command that generates a ready-to-paste boot protocol for any agent platform.

```bash
python3 -m anamnesis.cli generate-boot-prompt --bank gaya_operations --format claude-code
python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format generic
```

### Supported Formats
- `claude-code` — generates CLAUDE.md block with MCP tool calls (anamnesis_reflect, anamnesis_recall)
- `openai` — generates system prompt block with API calls
- `generic` — generates curl-based instructions that work with any agent

### Output Template (claude-code format)
The generated block should include:
1. Mandatory first action: call anamnesis_reflect with the bank name and a full briefing question
2. Secondary action: call anamnesis_recall for recent session handoff
3. Rules: never modify Anamnesis internals without reflect + spec review + user approval
4. Session end protocol: retain session summary before closing
5. Reference docs: where to find BUILD-SPEC.md if architectural changes are needed

### Implementation
- New file: `anamnesis/cli/generate_boot.py`
- Templates stored as string constants or Jinja2 templates
- Bank name injected into template
- Output printed to stdout (user copies it) or written to file with `--output` flag

### Test
- Run generator for each format
- Paste claude-code output into a test CLAUDE.md
- Verify a new Claude Code session follows the boot sequence correctly

---

## FIX 2: Boot Briefing Endpoint

### Problem
The `reflect` endpoint is general-purpose — it answers any strategic question. But the 
"I just woke up and know nothing" use case is specific and critical. It needs a dedicated 
endpoint optimized for cold-start sessions.

### Solution
New endpoint: `POST /api/v1/boot/{bank_id}`

### Request Body
```json
{
  "agent_name": "optional — for personalized briefing",
  "include_recent_sessions": true,
  "max_tokens": 2000
}
```

### Response
A structured boot package:
```json
{
  "mission": "The bank's mission statement",
  "directives": ["Directive 1", "Directive 2"],
  "top_priorities": [
    {
      "content": "Fix HUNTER scorer calibration",
      "weight": 9.2,
      "reasoning": "Blocks DTC lead flow, fastest revenue path",
      "dependencies": ["apify_async"]
    }
  ],
  "recent_outcomes": [
    {
      "content": "Scorer threshold lowered to 65",
      "when": "2026-03-24",
      "source": "claude-code"
    }
  ],
  "active_decay_alerts": [
    {
      "memory_id": "xxx",
      "content": "Sprint deadline May 8",
      "condition": "date-based",
      "status": "approaching"
    }
  ],
  "architecture_rules": [
    "Do not modify Anamnesis source code without reflect + spec review + user approval",
    "Weight system is intentional — do not 'fix' calibrated ranges",
    "Bank isolation is a hard boundary — do not cross-write"
  ],
  "gaps_identified": ["List of known gaps from last reflect"],
  "cold_start_warning": true,
  "hours_since_last_query": 18.5
}
```

### Implementation
- New route in `anamnesis/api/routes.py`
- Pulls bank config for mission/directives
- Runs recall for highest-weight active memories (top 5-10)
- Runs recall for most recent session outcomes (last 24-48 hours)
- Checks decay conditions for any approaching/triggered alerts
- Filters for memories tagged with "architecture" or "do-not-modify" for rules
- Calculates hours since last query to this bank
- Returns structured JSON (no LLM call needed — this is pure data assembly)

### Why This Is Different From Reflect
- `reflect` uses an LLM to synthesize — slower, costs tokens, better for nuanced questions
- `boot` is a structured data pull — fast, free, deterministic, perfect for session start
- Agents should call `boot` first (instant orientation), then `reflect` if they need deeper synthesis

### CLI Support
```bash
python3 -m anamnesis.cli boot --bank gaya_operations
```

### Test
- Call boot endpoint on a bank with 50+ memories
- Verify response contains mission, top priorities, recent outcomes
- Verify cold_start_warning is true when bank hasn't been queried recently
- Verify response returns in under 500ms (no LLM call)

---

## FIX 3: MCP Server Self-Documentation

### Problem
When Anamnesis connects to Claude Code via MCP, the tool descriptions don't tell the 
session to call reflect first. A new session sees the tools but doesn't know the protocol.

### Solution
Update MCP tool descriptions to include behavioral instructions.

### Updated Tool Descriptions

**anamnesis_reflect:**
```
Strategic synthesis across your memory bank. Returns a ranked operating directive 
with reasoning, citations, and gap analysis.

IMPORTANT: Call this tool FIRST in every new session before doing any other work. 
Your memory from previous sessions lives here. Without calling reflect, you are 
operating without strategic context and may make decisions that conflict with 
established priorities.
```

**anamnesis_retain:**
```
Store a memory with strategic reasoning. Every significant decision, outcome, 
or learning should be retained so future sessions have context.

Call this before ending any session to preserve continuity.

IMPORTANT: Do NOT retain modifications to Anamnesis architecture or design 
decisions without explicit user approval.
```

**anamnesis_recall:**
```
Search memories across 4 dimensions (semantic, temporal, relational, strategic).
Use this when you need specific context about a topic rather than a full briefing.
```

### Implementation
- Update tool descriptions in `anamnesis/mcp/server.py`
- The descriptions should be concise but include the behavioral nudges

### Test
- Connect MCP server to a fresh Claude Code session
- Verify tool descriptions appear in the tool listing
- Verify Claude Code calls reflect first without being told to in CLAUDE.md

---

## FIX 4: Onboarding Documentation (README Update)

### Problem
The README shows how to use the API but doesn't tell people how to wire their agent 
to actually USE Anamnesis automatically on reboot. Every user will hit the same 
amnesia problem without a guide.

### Solution
Add a new section to README.md between "CLI" and "Architecture" called 
"Wiring Your Agent."

### Content

#### Section: "Wiring Your Agent"

Explain the three-step integration:

1. **Seed your bank** — How to do initial retains with good strategic metadata. 
   Emphasize that reasoning quality determines reflect quality. Include examples 
   of good vs bad reasoning.

2. **Configure your agent's boot sequence** — Explain that the agent's config file 
   (CLAUDE.md, system prompt, SOUL.md, whatever) must include a mandatory first 
   action to call reflect. Show the CLI generator: 
   `python3 -m anamnesis.cli generate-boot-prompt --bank my_bank --format claude-code`
   Include copy-paste examples for Claude Code, OpenAI agents, and generic setups.

3. **Configure session end protocol** — Explain that sessions must retain outcomes 
   before closing. Show example retain calls for session summaries.

#### Section: "Common Pitfalls"

1. **"My new session tried to rewrite Anamnesis"** — Explain why this happens 
   (session lacks context, goes into problem-solve mode) and how the boot sequence 
   prevents it.

2. **"Reflect gives vague answers"** — Explain that reflect quality depends on 
   reasoning quality in retained memories. Show before/after of a memory with 
   bad reasoning vs good reasoning.

3. **"My agent ignores Anamnesis"** — Explain MCP tool description nudges and 
   why CLAUDE.md boot sequence is critical.

---

## FIX 5: Backup and Export (Gap #1 from Reflect)

### Problem
If the Docker container or PostgreSQL volume is lost, all memories are gone. 
No backup or export procedure exists.

### Solution

#### Export CLI
```bash
# Export entire bank to JSON
python3 -m anamnesis.cli export --bank gaya_operations --output backup.json

# Export all banks
python3 -m anamnesis.cli export --all --output full_backup.json
```

#### Import CLI
```bash
# Import from backup
python3 -m anamnesis.cli import --file backup.json

# Import with merge (don't overwrite existing memories)
python3 -m anamnesis.cli import --file backup.json --merge
```

#### Export Format
```json
{
  "version": "1.0",
  "exported_at": "ISO timestamp",
  "banks": [
    {
      "config": { "name": "...", "mission": "...", "directives": [...] },
      "memories": [
        {
          "id": "uuid",
          "content": "...",
          "reasoning": "...",
          "strategic": { ... },
          "entities": [...],
          "facts": [...],
          "created_at": "...",
          "weight": 4.2
        }
      ],
      "entities": [...],
      "relationships": [...]
    }
  ]
}
```

#### API Endpoints
```
GET /api/v1/export/{bank_id}  — returns JSON backup of a bank
POST /api/v1/import           — imports from JSON backup
```

#### Automated Backup
Add an optional environment variable:
```
ANAMNESIS_BACKUP_DIR=/path/to/backups
ANAMNESIS_BACKUP_INTERVAL_HOURS=24
```
When set, Anamnesis automatically exports all banks to the directory on the interval. 
Files named `anamnesis_backup_YYYY-MM-DD_HHMMSS.json`.

### Implementation
- New files: `anamnesis/cli/export.py`, `anamnesis/cli/import_cmd.py`
- New routes in `anamnesis/api/routes.py`
- Backup scheduler as optional background task in `anamnesis/main.py`

### Test
- Export a bank with 50+ memories
- Delete and recreate the bank
- Import from backup
- Verify all memories, entities, and relationships restored
- Run reflect and compare output before and after — should be identical

---

## FIX 6: Memory Schema Documentation (Gap #2 from Reflect)

### Problem
No documented schema for memories — fields, required attributes, validation rules. 
New users don't know what a well-formed memory looks like.

### Solution
Add a `SCHEMA.md` file to the repo root documenting:
- All memory fields with types and descriptions
- Required vs optional fields
- Strategic envelope structure
- Weight calculation formula
- Valid authority values and their hierarchy
- Valid decay condition formats
- Tag conventions
- Examples of well-formed memories at different weight levels

Also add inline validation in the retain endpoint that returns helpful error 
messages when required fields are missing or malformed.

---

## FIX 7: Memory Pruning and Archival (Gap #3 from Reflect)

### Problem
No process for handling memory growth. Banks will grow indefinitely.

### Solution

#### Automatic Archival
Memories that meet ANY of these conditions are candidates for archival:
- `is_active = false` (already deactivated by decay_check)
- Weight below 0.5 AND not accessed in 90 days
- Superseded by another memory AND older than 30 days

#### Archive Storage
Archived memories move to an `archived_memories` table with the same schema. 
They're excluded from recall and reflect but can be restored.

#### CLI
```bash
# Preview what would be archived
python3 -m anamnesis.cli prune --bank gaya_operations --dry-run

# Execute archival
python3 -m anamnesis.cli prune --bank gaya_operations

# Restore an archived memory
python3 -m anamnesis.cli restore --memory-id <uuid>
```

#### Scheduled Pruning
Optional environment variable:
```
ANAMNESIS_PRUNE_INTERVAL_DAYS=7
```

---

## FIX 8: Weight Assignment Rules (Gap #6 from Reflect)

### Problem
No documented rules for who can assign what weight levels. The current cap behavior 
confused a new session into thinking it was a bug.

### Solution
Document and enforce a weight authority hierarchy:

| Source | Max Initial Weight | Can Be Reweighted To |
|--------|-------------------|---------------------|
| `kali` (planning layer) | 10 | 10 |
| `explicit` (user manual) | 8 | 10 (via reweight) |
| `agent_execution` | 4 | 8 (via reweight after validation) |
| `system` (auto-extracted) | 2 | 6 (via reweight) |
| `inferred` | 1 | 4 (via reweight) |

The current cap at 4 for agent-sourced memories IS INTENTIONAL. Document this 
prominently in SCHEMA.md and in the retain endpoint's response:

```json
{
  "memory_id": "xxx",
  "weight": 1.52,
  "weight_note": "Initial weight capped at source-authority level. Use reweight cycles to increase based on validation."
}
```

### Implementation
- Enforce weight caps in retain based on authority field
- Return weight_note in retain response explaining the cap
- Document in SCHEMA.md

---

## FIX 9: Read-Only Cross-Bank Access (Gap #4 from Reflect)

### Problem  
No explicit access control model for cross-bank queries. Can one agent read 
another agent's bank?

### Solution
Default policy: **read-yes, write-no.**

- Any agent can recall/reflect against any bank (useful for cross-venture synthesis)
- Only the bank's designated agents can retain to it
- Bank config gets a new optional field: `write_agents: ["claude-code", "neville"]`
- If `write_agents` is empty/null, any agent can write (backward compatible)
- If `write_agents` is set, retain rejects requests from unlisted agents

### Implementation
- Add `write_agents` field to banks table
- Add agent identification to retain requests (new optional `agent` field)
- Enforce in retain endpoint
- Document in README

---

## BUILD ORDER

1. Fix 3 (MCP self-documentation) — smallest change, biggest immediate impact
2. Fix 1 (Boot protocol generator) — prevents the reboot problem for all users
3. Fix 2 (Boot endpoint) — fast structured orientation for cold starts  
4. Fix 4 (README update) — documentation prevents support burden
5. Fix 8 (Weight assignment rules) — prevents the exact "bug fix" incident
6. Fix 6 (Schema docs) — reference material for proper memory authoring
7. Fix 5 (Backup/export) — data safety before growth
8. Fix 7 (Pruning) — needed at scale but not urgent at 50 memories
9. Fix 9 (Cross-bank access) — needed for multi-agent setups

## IMPORTANT REMINDERS FOR CLAUDE CODE:

- You are modifying the Anamnesis PRODUCT, not just local config
- Every change should benefit ALL users, not just Shweta's setup
- Do not hardcode bank names, agent names, or business-specific context
- Test each fix before moving to the next
- Commit after each fix with a descriptive message
- Do not modify the weight calculation formula or reranking logic — those are working correctly
