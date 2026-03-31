"""
Multi-Agent Research Workflow
==============================
Shows how to embed the Web Scraping Agent as a subagent in a larger research
pipeline. The orchestrator agent decides which URLs to scrape, delegates to
the web scraping subagent, then synthesises the results.

Usage:
    python multi_agent_research.py "<research question>"

Example:
    python multi_agent_research.py "Who are the speakers at PyCon US 2025?"
"""

import asyncio
import sys

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

# Import the scraping agent's definition – it wires up Playwright MCP for us
from web_scraping_agent.agent import WEB_SCRAPING_AGENT_DEFINITION


# ---------------------------------------------------------------------------
# Multi-agent research pipeline
# ---------------------------------------------------------------------------

ORCHESTRATOR_SYSTEM_PROMPT = """You are a research orchestrator with access to a
web scraping subagent that can browse any URL (including JS-rendered pages) and
extract structured information like speakers, topics, dates, and schedules.

Workflow:
1. Analyse the research question.
2. Identify the most relevant URLs to scrape (use web search knowledge or the
   user-supplied URLs).
3. Call the `web-scraper` subagent with each URL, specifying the appropriate focus.
4. Aggregate the results into a clear, well-structured research summary.
5. Highlight key findings, speakers, dates, and any gaps in the data.

Be thorough. If a page lists conference speakers, extract all of them, not just
a sample.
"""


async def research(question: str) -> str:
    """Run the multi-agent research pipeline and return the final report."""
    result_text = ""

    async for message in query(
        prompt=question,
        options=ClaudeAgentOptions(
            model="claude-opus-4-6",
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            max_turns=40,
            allowed_tools=["Agent"],          # orchestrator delegates to subagents
            agents={
                "web-scraper": WEB_SCRAPING_AGENT_DEFINITION,
            },
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result

    return result_text


# ---------------------------------------------------------------------------
# Scrape-then-summarise: simpler two-step pattern
# ---------------------------------------------------------------------------

async def scrape_and_summarise(url: str, question: str) -> str:
    """
    Step 1 – scrape the URL with the web scraping agent.
    Step 2 – pass the extracted data to a summarisation agent.
    """
    from web_scraping_agent.agent import scrape_url

    print(f"Step 1: Scraping {url} …")
    scraped = await scrape_url(url)

    if scraped.error:
        return f"Scraping failed: {scraped.error}"

    print(f"Step 2: Summarising ({len(scraped.speakers)} speakers found) …")
    summary_prompt = f"""
Research question: {question}

Scraped data from {url}:
{scraped.to_json()}

Please write a detailed research summary answering the question using the
scraped data above. Include all speaker names, affiliations, and topics found.
"""

    result_text = ""
    async for message in query(
        prompt=summary_prompt,
        options=ClaudeAgentOptions(model="claude-opus-4-6"),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result

    return result_text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nExamples:")
        print("  python multi_agent_research.py 'Who speaks at NeurIPS 2024?'")
        print("  python multi_agent_research.py 'https://example.com/conference speakers'")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"\nResearch question: {question}\n{'─'*60}\n")

    report = await research(question)
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
