"""
Model compatibility test harness.

Used by tests/run_all.py to run structural, behavioral, and adversarial
test suites against the currently configured Ollama model.

Also provides helpers for capturing LLM fixtures from live model runs.
"""

import importlib
import json
import logging
import os
import pathlib
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

AGENTS = [
    "morning_digest",
    "commute_ping",
    "discord_reminders",
    "grocery_optimizer",
]

SUITES = ["structural", "behavioral", "adversarial"]


def run_suite(agent: str, suite: str, verbose: bool = False) -> dict[str, Any]:
    """
    Run a single test suite for an agent.

    Returns a dict with keys: agent, suite, passed, failed, errors, duration_s
    """
    test_module_path = f"agents.{agent}.tests.test_{suite}"
    start = time.monotonic()
    passed = failed = 0
    errors: list[str] = []

    try:
        module = importlib.import_module(test_module_path)
    except ModuleNotFoundError:
        return {
            "agent": agent,
            "suite": suite,
            "passed": 0,
            "failed": 0,
            "errors": [f"Module not found: {test_module_path}"],
            "duration_s": 0.0,
        }

    test_fns = [
        (name, fn)
        for name, fn in vars(module).items()
        if name.startswith("test_") and callable(fn)
    ]

    for name, fn in test_fns:
        try:
            fn()
            passed += 1
            if verbose:
                print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            msg = f"FAIL  {name}: {exc}"
            errors.append(msg)
            if verbose:
                print(f"  {msg}")
        except Exception as exc:
            failed += 1
            msg = f"ERROR {name}: {type(exc).__name__}: {exc}"
            errors.append(msg)
            if verbose:
                print(f"  {msg}")

    duration = time.monotonic() - start
    return {
        "agent": agent,
        "suite": suite,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration_s": round(duration, 2),
    }


def run_all(
    agents: list[str] | None = None,
    suites: list[str] | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Run all requested suites and return results list."""
    target_agents = agents or AGENTS
    target_suites = suites or SUITES
    results = []

    for agent in target_agents:
        for suite in target_suites:
            if verbose:
                print(f"\n[{agent}] {suite}")
            result = run_suite(agent, suite, verbose=verbose)
            results.append(result)

    return results


def print_summary(results: list[dict[str, Any]]) -> int:
    """Print a summary table and return exit code (0=pass, 1=fail)."""
    total_passed = total_failed = 0
    print("\n" + "=" * 60)
    print(f"{'AGENT':<22} {'SUITE':<14} {'PASS':>5} {'FAIL':>5} {'TIME':>7}")
    print("-" * 60)
    for r in results:
        total_passed += r["passed"]
        total_failed += r["failed"]
        status = "OK" if r["failed"] == 0 else "FAIL"
        print(
            f"{r['agent']:<22} {r['suite']:<14} {r['passed']:>5} {r['failed']:>5} "
            f"{r['duration_s']:>6.1f}s  {status}"
        )
        for err in r["errors"]:
            print(f"    {err}")
    print("=" * 60)
    print(f"Total: {total_passed} passed, {total_failed} failed")
    return 0 if total_failed == 0 else 1


def capture_fixtures(agent: str, output_dir: pathlib.Path) -> None:
    """
    Run the agent's LLM calls in capture mode and save raw responses
    as JSON fixtures for regression baselines.

    Agents must set FIXTURE_CAPTURE=1 in env to activate capture logging.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FIXTURE_CAPTURE"] = "1"
    logging.basicConfig(level=logging.DEBUG)
    logger.info("Capturing fixtures for %s → %s", agent, output_dir)
    # Actual capture is done via DEBUG logging in lib/llm.py;
    # agents read FIXTURE_CAPTURE and write to fixtures/ themselves.
    print(f"Fixture capture mode enabled. Run agent manually to generate fixtures.")
    print(f"Output dir: {output_dir}")
