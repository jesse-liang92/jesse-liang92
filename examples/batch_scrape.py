"""
Batch Scraping Example
======================
Scrape multiple URLs concurrently and write a consolidated JSON report.

Usage:
    python batch_scrape.py url1 url2 url3 ...

Example:
    python batch_scrape.py \
        https://pycon.us/speakers \
        https://neurips.cc/speakers \
        https://icml.cc/Conferences/2025/invited
"""

import asyncio
import json
import sys
from pathlib import Path

# Adjust path so we can import the agent package from examples/
sys.path.insert(0, str(Path(__file__).parent.parent))
from web_scraping_agent.agent import ScrapingResult, scrape_url


async def scrape_all(urls: list[str], focus: str = "speakers") -> list[dict]:
    """Scrape all URLs concurrently and return a list of result dicts."""
    tasks = [scrape_url(url, focus) for url in urls]
    results: list[ScrapingResult] = await asyncio.gather(*tasks)
    return [r.to_dict() for r in results]


async def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print(__doc__)
        sys.exit(1)

    focus = "speakers"
    print(f"Scraping {len(urls)} URL(s) concurrently (focus={focus}) …\n")

    results = await scrape_all(urls, focus)

    # Summary
    total_speakers = sum(len(r.get("speakers", [])) for r in results)
    print(f"Done. Total speakers found: {total_speakers}\n")
    for r in results:
        n = len(r.get("speakers", []))
        err = r.get("error", "")
        marker = "✓" if not err else "✗"
        print(f"  {marker} {r['url']}  →  {n} speaker(s)" + (f"  [{err}]" if err else ""))

    # Write consolidated report
    report_path = Path("scraping_report.json")
    report_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull report written to: {report_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
