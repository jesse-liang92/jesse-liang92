#!/usr/bin/env python3
"""
run_all.py — Master test runner for all Ally X agents.

Usage:
    # Run all suites against all agents
    python tests/run_all.py

    # Run tests for a specific agent only
    python tests/run_all.py --agent morning_digest

    # Run only structural tests (fast, for model swap validation)
    python tests/run_all.py --suite structural

    # Run structural + behavioral but skip adversarial
    python tests/run_all.py --suite structural --suite behavioral

    # Capture new fixtures from current model (enables FIXTURE_CAPTURE=1)
    python tests/run_all.py --capture-fixtures --agent morning_digest

    # Verbose output (show each test name)
    python tests/run_all.py --verbose

Model swap procedure:
    1. ollama pull new-model && update OLLAMA_MODEL in .env
    2. python tests/run_all.py --suite structural        # must be 100%
    3. python tests/run_all.py --suite behavioral        # review regressions
    4. If behavioral < 90% pass rate, do NOT deploy. Adjust prompts or revert.
"""

import argparse
import os
import pathlib
import sys

# Ensure project root is importable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.test_runner import run_all, print_summary, capture_fixtures, AGENTS, SUITES


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ally X agent test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        choices=AGENTS,
        metavar="AGENT",
        help=f"Agent to test. Can be specified multiple times. Choices: {', '.join(AGENTS)}",
    )
    parser.add_argument(
        "--suite",
        action="append",
        dest="suites",
        choices=SUITES,
        metavar="SUITE",
        help=f"Test suite to run. Can be specified multiple times. Choices: {', '.join(SUITES)}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show individual test names and results",
    )
    parser.add_argument(
        "--capture-fixtures",
        action="store_true",
        help="Enable FIXTURE_CAPTURE mode and print fixture output path",
    )
    args = parser.parse_args()

    # Load .env for OLLAMA_URL / OLLAMA_MODEL
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    model = os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0")
    url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    print(f"Model: {model}  |  Ollama: {url}")

    if args.capture_fixtures:
        for agent in (args.agents or AGENTS):
            out_dir = PROJECT_ROOT / "agents" / agent / "tests" / "fixtures"
            capture_fixtures(agent, out_dir)
        return 0

    results = run_all(
        agents=args.agents,
        suites=args.suites,
        verbose=args.verbose,
    )
    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
