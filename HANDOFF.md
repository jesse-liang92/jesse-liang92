# Ally X Agent System — Session Handoff

## What This Is

A suite of personal automation agents for a **ROG Ally X** running headless Ubuntu.
Each agent is a standalone Python service calling a local LLM (Qwen3.5-9B via Ollama)
and posting outputs to a personal Discord server.

**Repo:** `jesse-liang92/jesse-liang92`
**Branch:** `claude/build-ally-automation-agents-PrhG8`
**Local path:** `C:\Users\Jesse\allyx-agents`

---

## Current State — What's Been Built

### Shared Libraries (`lib/`)

| File | Purpose |
|---|---|
| `lib/llm.py` | Ollama client wrapper — posts to `localhost:11434/api/chat`, validates response against Pydantic schema, retries once with JSON nudge, returns `None` on failure |
| `lib/discord_out.py` | `send_message`, `send_embed`, `post_error`, `post_status` via Discord webhooks |
| `lib/schemas.py` | Pydantic v2 models: `MorningDigestResponse`, `LocationResolutionResponse`, `ReminderParseResponse`, `GroceryOptimizerResponse`, `PackageStatusResponse`, `FinanceDigestResponse`, `BillAlertResponse` |
| `lib/test_runner.py` | Model-swap test harness used by `tests/run_all.py` |

### Agents Built

| Agent | LLM? | Schedule | Discord channel | Status |
|---|---|---|---|---|
| `agents/calendar_sync` | No | Every 15 min (systemd timer) | `#agent-status` on errors only | Complete + tested |
| `agents/morning_digest` | Yes | Daily 06:00 PT | `#calendar` + Obsidian daily note | Complete + tested |
| `agents/commute_ping` | Optional | Daily 05:30 PT | `#commute` | Complete + tested |
| `agents/discord_reminders` | Yes | Always-on bot | `#reminders` | Complete + tested |
| `agents/grocery_optimizer` | Yes | Saturday 08:00 PT | `#groceries` | Complete + tested |
| `agents/package_tracker` | Optional | Every 2 hours (systemd timer) | `#packages` | Complete + tested |
| `agents/finance_digest` | Yes | Weekdays 1:00 PM PT | `#finance` | Complete + tested |
| `agents/bill_monitor` | — | — | `#bills` | **Not yet built** |

### Deploy

- `deploy/install.sh` — installs and enables all systemd services/timers on the Ally X
- `deploy/templates/` — per-agent `.service` and `.timer` unit file templates

### Tests

Each built agent has three test files:
- `test_structural.py` — schema conformance (some need Ollama)
- `test_behavioral.py` — reasoning quality with known inputs (needs Ollama)
- `test_adversarial.py` — edge cases and malformed inputs

**Tests run locally on Windows (no Ollama needed):**
```
agents/calendar_sync/tests/        → 16/16 pass
agents/commute_ping/tests/         → 19/19 pass (deterministic tests only)
agents/discord_reminders/tests/    → 4/4 pass  (DB logic only)
agents/grocery_optimizer/tests/    → 1/1 pass  (embed builder only)
agents/package_tracker/tests/      → 22/22 pass (DB + schema + embed + edge cases)
agents/finance_digest/tests/       → 26/26 pass (schema + alerts + embed + edge cases)
```

**Tests requiring Ollama (run on the Ally X):**
```
python tests/run_all.py --suite structural   # must be 100% before deploy
python tests/run_all.py --suite behavioral   # must be >90% before deploy
```

---

## What Still Needs To Be Done

### ~~1. `finance_digest` agent~~ DONE

Built and tested (26/26 local tests pass). Uses `yfinance` for data, LLM for summary, posts to `#finance` weekdays at 1:00 PM PT.

---

### 2. `bill_monitor` agent (deprioritized)
**Purpose:** Track recurring bills, alert when due within 7 days, flag overdue.

**Suggested approach:**
- Store bill schedule in local SQLite (name, amount, due day of month, last paid)
- Trigger alerts to `#bills` when due date is approaching
- LLM usage: minimal — only for parsing bill confirmation emails if needed
- Schema already defined in `lib/schemas.py` as `BillAlertResponse`
- Schedule: Daily check, morning

---

### 3. Deployment on the Ally X

Once agents are done and tests pass on the local model:
```bash
# On the Ally X (Ubuntu, headless)
git clone -b claude/build-ally-automation-agents-PrhG8 \
    https://github.com/jesse-liang92/jesse-liang92.git ~/allyx-agents
cd ~/allyx-agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with all API keys

# Authenticate Microsoft (one-time, opens device code flow)
python agents/calendar_sync/agent.py --dry-run

# Run full test suite against Qwen model
python tests/run_all.py --suite structural   # must be 100%
python tests/run_all.py                      # full suite

# Deploy systemd services
sudo bash deploy/install.sh
```

---

## Architecture Decisions Made

1. **No frameworks** — each agent is a single `agent.py` + `config.yaml`. No FastAPI, no Celery.
2. **`httpx` not `requests`** — async-capable, better timeout handling.
3. **Pydantic v2** for all LLM response schemas — strict validation, clear error messages.
4. **LLM is optional per agent** — `calendar_sync` has zero LLM calls. `commute_ping` only calls LLM for ambiguous location strings.
5. **`--dry-run` flag on every agent** — prints what would be posted, no Discord calls, no writes.
6. **Fail silently, log loudly** — agents catch all LLM failures, log raw output, post to `#agent-status`, and continue.
7. **Microsoft auth via MSAL device code flow** — one-time interactive auth, then token refresh from cache at `~/.config/allyx/ms_token_cache.json`.

---

## Code Conventions

- Python 3.11+, type hints on all signatures
- `from dotenv import load_dotenv` at top of each agent; never import secrets at module level
- Logging: `logging.basicConfig` with `RotatingFileHandler` → `~/allyx-agents/logs/<agent>.log`
- All agents add `PROJECT_ROOT` to `sys.path` so `from lib import llm` works without install
- `import logging.handlers` must be explicit (not just `import logging`)

---

## LLM Prompt Pattern (used in `lib/llm.py`)

```
You are a personal automation assistant. Respond ONLY with valid JSON matching this schema.
No markdown, no explanation, no preamble.

Schema:
{schema_json}

Task:
{task_description}

Input:
{input_data}
```

- Temperature: 0.1 (low for structured output)
- Default timeout: 30s (45s for morning_digest, 20s for discord_reminders)
- Retry: once, with nudge "Your previous response was not valid JSON. Respond ONLY with valid JSON."

---

## Environment Variables (see `.env.example`)

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b-q8_0

MS_TENANT_ID=
MS_CLIENT_ID=

GOOGLE_MAPS_API_KEY=
OPENWEATHERMAP_API_KEY=

HOME_ADDRESS=
BIOSPACE_ADDRESS=11150 Santa Monica Blvd, Los Angeles, CA 90025

UPS_CLIENT_ID=
UPS_CLIENT_SECRET=
FEDEX_CLIENT_ID=
FEDEX_CLIENT_SECRET=
USPS_USER_ID=

DISCORD_BOT_TOKEN=
DISCORD_CALENDAR_WEBHOOK=
DISCORD_COMMUTE_WEBHOOK=
DISCORD_REMINDERS_WEBHOOK=
DISCORD_GROCERIES_WEBHOOK=
DISCORD_FINANCE_WEBHOOK=
DISCORD_PACKAGES_WEBHOOK=
DISCORD_BILLS_WEBHOOK=
DISCORD_STATUS_WEBHOOK=
```

---

## Test Runner

```bash
# All tests
python tests/run_all.py

# Specific agent
python tests/run_all.py --agent morning_digest

# Only structural (fast, for model swap validation)
python tests/run_all.py --suite structural

# Verbose
python tests/run_all.py --verbose

# Capture fixtures from live model
python tests/run_all.py --capture-fixtures --agent morning_digest
```

**Model swap procedure:**
1. `ollama pull new-model` + update `OLLAMA_MODEL` in `.env`
2. `python tests/run_all.py --suite structural` — must be 100%
3. `python tests/run_all.py --suite behavioral` — must be ≥90%
4. If below 90%, adjust prompts or revert model
