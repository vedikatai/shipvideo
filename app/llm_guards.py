"""
LLM cost and safety guards. Change limits and behavior here without touching pipeline logic.

- max_tokens: cap Azure response size
- budget: pause LLM spend above a dollar limit (internal tracking)
- dedupe: skip re-running for the same PR+commit (redeliveries)
- skip_llm_for_size: skip LLM when diff payload is too large (use fallback steps)
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# -----------------------------------------------------------------------------
# Config (change these for different environments / requirements)
# -----------------------------------------------------------------------------
MAX_RESPONSE_TOKENS = 500
"""Cap on Azure response tokens so a single response can't be huge."""

BUDGET_LIMIT_USD = 15.0
"""Internal budget: when tracked spend exceeds this, we skip LLM and use fallback steps."""

MAX_DIFF_CHARS_TO_SKIP_LLM = 12_000
"""If diff payload (after our normal truncation) exceeds this, skip LLM and use fallback."""

# Approximate gpt-4o-mini pricing per 1K tokens (adjust if Azure pricing changes)
PRICE_PER_1K_INPUT_USD = 0.000_15
PRICE_PER_1K_OUTPUT_USD = 0.000_6

DATA_DIR = Path(__file__).resolve().parent / "data"
SPEND_FILE = DATA_DIR / "llm_spend.json"
DEDUPE_FILE = DATA_DIR / "llm_dedup.json"
MAX_DEDUPE_ENTRIES = 500

_lock = threading.Lock()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_max_tokens() -> int:
    """Max tokens to allow for Azure chat completion response."""
    return MAX_RESPONSE_TOKENS


def check_budget() -> bool:
    """
    True if under budget (ok to call LLM). False if over budget (use fallback, don't call LLM).
    """
    _ensure_data_dir()
    with _lock:
        if not SPEND_FILE.exists():
            return True
        try:
            with open(SPEND_FILE) as f:
                data = json.load(f)
            total = float(data.get("total_usd", 0))
            if total >= BUDGET_LIMIT_USD:
                print(f"[llm-guards] Budget exceeded (${total:.2f} >= ${BUDGET_LIMIT_USD}), skipping LLM", flush=True)
                return False
            return True
        except Exception as e:
            print(f"[llm-guards] Could not read spend file: {e}", flush=True)
            return True


def record_spend(prompt_tokens: int, completion_tokens: int) -> None:
    """Record estimated cost from a single LLM call (call after successful Azure response)."""
    estimated = (prompt_tokens / 1000.0) * PRICE_PER_1K_INPUT_USD + (completion_tokens / 1000.0) * PRICE_PER_1K_OUTPUT_USD
    _ensure_data_dir()
    with _lock:
        total = 0.0
        if SPEND_FILE.exists():
            try:
                with open(SPEND_FILE) as f:
                    total = float(json.load(f).get("total_usd", 0))
            except Exception:
                pass
        total += estimated
        with open(SPEND_FILE, "w") as f:
            json.dump({"total_usd": round(total, 4), "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"}, f)
        print(f"[llm-guards] Recorded ~${estimated:.4f}; total ~${total:.2f}", flush=True)


def check_already_ran(repo: str, pr_number: int, commit_sha: str) -> bool:
    """True if we already ran for this repo + PR + commit (e.g. redelivery)."""
    if not commit_sha:
        return False
    key = f"{repo}#{pr_number}#{commit_sha}"
    _ensure_data_dir()
    with _lock:
        if not DEDUPE_FILE.exists():
            return False
        try:
            with open(DEDUPE_FILE) as f:
                data = json.load(f)
            if key in data.get("runs", {}):
                print(f"[llm-guards] Dedupe: already ran for {repo}#{pr_number} @ {commit_sha[:7]}", flush=True)
                return True
            return False
        except Exception:
            return False


def record_run(repo: str, pr_number: int, commit_sha: str) -> None:
    """Mark that we ran for this repo + PR + commit (call at start of pipeline for this PR)."""
    if not commit_sha:
        return
    key = f"{repo}#{pr_number}#{commit_sha}"
    _ensure_data_dir()
    with _lock:
        runs = {}
        if DEDUPE_FILE.exists():
            try:
                with open(DEDUPE_FILE) as f:
                    runs = json.load(f).get("runs", {})
            except Exception:
                pass
        runs[key] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
        if len(runs) > MAX_DEDUPE_ENTRIES:
            by_time = sorted(runs.items(), key=lambda x: x[1])
            runs = dict(by_time[-MAX_DEDUPE_ENTRIES:])
        with open(DEDUPE_FILE, "w") as f:
            json.dump({"runs": runs}, f)


def should_skip_llm_for_size(diff_char_count: int) -> bool:
    """True if diff payload is too large; use fallback steps instead of calling LLM."""
    if diff_char_count > MAX_DIFF_CHARS_TO_SKIP_LLM:
        print(f"[llm-guards] Diff size {diff_char_count} > {MAX_DIFF_CHARS_TO_SKIP_LLM}, skipping LLM", flush=True)
        return True
    return False
