# CLAUDE.md — Ally X Personal Automation Agent System

## Project Overview

This project is a suite of lightweight personal automation agents deployed on a **ROG Ally X** (AMD Z1 Extreme, 24GB unified RAM) running headless Ubuntu. All agents are standalone Python services that call a local LLM (**Qwen3.5-9B Q8_0** via Ollama) for reasoning/parsing when needed, and deliver outputs to a personal **Discord server** via webhooks or bot commands.

**Development happens on Claude Code (Opus). Deployment targets Qwen3.5-9B locally.** Every agent must pass its test suite against the local model before it ships.

---

## Architecture

```
ROG Ally X (headless, always-on)
├── Ollama (serving Qwen3.5-9B @ Q8_0, port 11434)
├── Systemd services (one per agent)
│   ├── calendar-sync.service
│   ├── morning-digest.service
│   ├── commute-ping.service
│   ├── discord-reminders.service
│   ├── grocery-optimizer.service
│   ├── package-tracker.service + .timer
│   ├── finance-digest.service
│   └── bill-monitor.service
├── Shared libs (see /lib)
└── Claude Code (dev/iteration only, not always-running)
```

### Key Design Principles

1. **Each agent is a single Python file + config.** No frameworks. No FastAPI unless the agent needs to receive webhooks. Use `schedule` or systemd timers for cron-like behavior.
2. **LLM calls are optional per agent.** If the task can be done deterministically (e.g., calendar sync via API), do it deterministically. Only invoke the LLM when you need parsing, summarization, or natural language generation.
3. **All agent outputs go to Discord.** Each agent posts to its own named channel (`#calendar`, `#commute`, `#reminders`, `#groceries`, `#finance`, `#packages`, `#bills`). Error/status messages go to `#agent-status`.
4. **Fail silently, log loudly.** Agents must not crash on bad LLM output. Catch malformed responses, log the raw output, and either retry once or skip gracefully. Post failures to `#agent-status`.
5. **Secrets live in `.env` files**, loaded via `python-dotenv`. Never hardcode API keys.

---

## Directory Structure

```
~/allyx-agents/
├── CLAUDE.md                  # This file
├── .env                       # API keys (Discord, Google, Microsoft, etc.)
├── lib/
│   ├── llm.py                 # Shared Ollama client wrapper
│   ├── discord_out.py         # Discord webhook/bot utilities
│   ├── schemas.py             # Pydantic models for all LLM response schemas
│   └── test_runner.py         # Model compatibility test harness
├── agents/
│   ├── calendar_sync/
│   │   ├── agent.py
│   │   ├── config.yaml
│   │   └── tests/
│   ├── morning_digest/
│   │   ├── agent.py
│   │   ├── config.yaml
│   │   └── tests/
│   ├── commute_ping/
│   │   ├── agent.py
│   │   ├── config.yaml
│   │   └── tests/
│   ├── discord_reminders/
│   │   ├── agent.py
│   │   ├── config.yaml
│   │   └── tests/
│   ├── grocery_optimizer/
│   │   ├── agent.py
│   │   ├── config.yaml
│   │   └── tests/
│   └── package_tracker/
│       ├── agent.py
│       ├── config.yaml
│       └── tests/
├── deploy/
│   ├── install.sh
│   └── templates/
└── tests/
    └── run_all.py
```

---

## Shared LLM Client (`lib/llm.py`)

All agents call the LLM through this shared wrapper. The wrapper must:

- POST to `http://localhost:11434/api/chat` (Ollama native endpoint)
- Accept a Pydantic model as the expected response schema
- Validate the response against the schema before returning
- Retry once on parse failure with a "please respond in valid JSON" nudge
- Return `None` (not raise) on second failure
- Log raw prompts and responses at DEBUG level
- Include a `timeout` parameter (default 30s)

**Prompt pattern:**
```
You are a personal automation assistant. Respond ONLY with valid JSON matching this schema. No markdown, no explanation, no preamble.

Schema:
{schema_json}

Task:
{task_description}

Input:
{input_data}
```

---

## Code Standards

- **Python 3.11+**. Type hints on all function signatures.
- **Pydantic v2** for all data models and LLM response schemas.
- **`httpx`** for HTTP calls (async-capable, timeout-friendly). Not `requests`.
- **`python-dotenv`** for env loading. Never import secrets at module level.
- **No classes unless necessary.** Prefer functions.
- **Logging:** stdlib `logging` with structured format. Stdout + rotating file in `~/allyx-agents/logs/`.
- **Dependencies:** Pin in `requirements.txt` per agent. Shared deps in root `requirements.txt`.

### LLM Prompt Style Guide

- Always include the JSON schema in the prompt.
- Always include "Respond ONLY with valid JSON. No markdown, no explanation."
- Always include current date/time in time-sensitive prompts.
- Keep prompts under 500 tokens total.
- Never ask the model to do what code can do.

---

## API Keys Required

| Service | Key/Credential | Used By |
|---|---|---|
| Microsoft Graph (M365) | OAuth2 app registration | calendar_sync, morning_digest, grocery_optimizer |
| Google Calendar API | OAuth2 credentials JSON | calendar_sync, morning_digest, commute_ping |
| Google Maps Directions | API key | commute_ping |
| OpenWeatherMap | API key | morning_digest |
| Discord Bot | Bot token + webhook URLs | All agents |
| UPS | OAuth2 client ID + secret | package_tracker |
| FedEx | OAuth2 client ID + secret | package_tracker |
| USPS | Web Tools user ID | package_tracker |
| Yahoo Finance / Finviz | API key or scraping | finance_digest |
