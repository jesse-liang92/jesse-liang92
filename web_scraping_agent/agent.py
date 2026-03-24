"""
Web Scraping Agent
==================
Browses any URL (including dynamically rendered pages) via Playwright MCP,
then uses Claude Opus 4.6 to extract structured information such as speakers,
dates, topics, and other text.

Designed for standalone use and for embedding in multi-agent research workflows.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

# ---------------------------------------------------------------------------
# Structured result type
# ---------------------------------------------------------------------------

@dataclass
class ScrapingResult:
    url: str
    title: str = ""
    description: str = ""
    speakers: list[dict[str, str]] = field(default_factory=list)
    dates: str = ""
    topics: list[str] = field(default_factory=list)
    other_content: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "speakers": self.speakers,
            "dates": self.dates,
            "topics": self.topics,
            "other_content": self.other_content,
            "raw_text": self.raw_text,
            "error": self.error,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Playwright MCP server config
# ---------------------------------------------------------------------------

PLAYWRIGHT_MCP_SERVER = {
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--headless"],
}

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT_TEMPLATE = """You are a precise web content extractor.

Your task:
1. Navigate to: {url}
2. Wait for the page to fully load, including any JavaScript-rendered content.
3. Extract ALL relevant information and return it as valid JSON.

Extract these fields (use empty string / empty list if not found):
- title: Page or event title
- description: Main description or summary text
- speakers: List of objects with keys: name, title, affiliation, bio, social_links
- dates: Event date(s) and time(s) as a string
- topics: List of session topics, agenda items, or key themes
- other_content: Any other structured information (schedules, prices, locations, etc.)

{focus_instruction}

Return ONLY a JSON object with exactly these keys:
{{
  "title": "...",
  "description": "...",
  "speakers": [
    {{"name": "...", "title": "...", "affiliation": "...", "bio": "...", "social_links": {{}}}}
  ],
  "dates": "...",
  "topics": ["..."],
  "other_content": {{}}
}}

Important:
- Use Playwright tools to scroll through the entire page
- Wait for lazy-loaded content to appear before extracting
- If speaker bios are hidden behind expandable sections, click them open
- Capture all speakers, not just the first few visible ones
"""

FOCUS_INSTRUCTIONS = {
    "speakers": "Focus especially on extracting complete speaker information.",
    "schedule": "Focus especially on the schedule, agenda, and session topics.",
    "all": "Extract everything thoroughly.",
}


def _build_prompt(url: str, focus: str = "all") -> str:
    focus_instruction = FOCUS_INSTRUCTIONS.get(focus, FOCUS_INSTRUCTIONS["all"])
    return EXTRACTION_PROMPT_TEMPLATE.format(url=url, focus_instruction=focus_instruction)


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

def _parse_result(url: str, result_text: str) -> ScrapingResult:
    """Extract JSON from the agent's response and map it to ScrapingResult."""
    base = ScrapingResult(url=url, raw_text=result_text)

    # Find the outermost JSON object in the response
    match = re.search(r"\{[\s\S]*\}", result_text)
    if not match:
        base.error = "No JSON found in agent response"
        return base

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        base.error = f"JSON parse error: {exc}"
        return base

    base.title = data.get("title", "")
    base.description = data.get("description", "")
    base.speakers = data.get("speakers", [])
    base.dates = data.get("dates", "")
    base.topics = data.get("topics", [])
    base.other_content = data.get("other_content", {})
    return base


# ---------------------------------------------------------------------------
# Core scraping function – usable standalone or inside another agent
# ---------------------------------------------------------------------------

async def scrape_url(
    url: str,
    focus: str = "all",
    model: str = "claude-opus-4-6",
    max_turns: int = 20,
) -> ScrapingResult:
    """
    Browse *url* with Playwright and extract structured information.

    Args:
        url:       The page to scrape.
        focus:     Extraction focus – "all" | "speakers" | "schedule".
        model:     Claude model to use (default: claude-opus-4-6).
        max_turns: Maximum agentic turns before stopping.

    Returns:
        A ScrapingResult with all extracted fields populated.
    """
    prompt = _build_prompt(url, focus)
    result_text = ""

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            model=model,
            max_turns=max_turns,
            mcp_servers={"playwright": PLAYWRIGHT_MCP_SERVER},
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result

    return _parse_result(url, result_text)


# ---------------------------------------------------------------------------
# AgentDefinition – plug directly into multi-agent workflows
# ---------------------------------------------------------------------------

#: Drop this into any `ClaudeAgentOptions(agents={...})` call.
WEB_SCRAPING_AGENT_DEFINITION = AgentDefinition(
    description=(
        "Browses web pages (including JavaScript-rendered single-page apps) "
        "using Playwright and extracts structured information such as speakers, "
        "presenters, event dates, topics, schedules, and other text content. "
        "Accepts a URL and an optional focus parameter ('all' | 'speakers' | 'schedule'). "
        "Returns a JSON object with title, description, speakers, dates, topics, "
        "and other_content fields."
    ),
    prompt=_build_prompt("{url}", "all"),   # placeholder – orchestrator fills {url}
    tools=["playwright"],
)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python agent.py <url> [focus]")
        print("  focus: all | speakers | schedule  (default: all)")
        sys.exit(1)

    url = sys.argv[1]
    focus = sys.argv[2] if len(sys.argv) > 2 else "all"

    print(f"Scraping: {url}  (focus={focus})\n")
    result = await scrape_url(url, focus)

    if result.error:
        print(f"Error: {result.error}")
        print(f"\nRaw output:\n{result.raw_text}")
    else:
        print(result.to_json())


if __name__ == "__main__":
    asyncio.run(_main())
