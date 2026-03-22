"""
Deterministic ref-selection policy — Phase 2 / Phase 3.

Implements the decision layer for the Agent Browser accuracy experiment.
Given a natural-language intent (e.g. "Generate API Key") and a normalized
AgentBrowserSnapshot from Phase 1, this module returns a structured
SelectionResult identifying which ref (@e1, @e2, …) should be clicked and why.

Public API:
    select_ref(intent, snapshot, *, mode, role_filter) -> SelectionResult

Selection waterfall (deterministic, Mode A):
    1. Exact name match            — element.name == intent  (case-sensitive)
    2. Case-insensitive exact match — element.name.lower() == intent.lower()
    3. Partial match               — intent ⊆ element.name or
                                     element.name ⊆ intent  (case-insensitive)
    At every level:
        - Exactly 1 match  → return that ref with the corresponding reason.
        - 2+ matches       → return "ambiguous" immediately; do not guess.
        - 0 matches        → fall through to the next level.
    If all levels are exhausted with no candidate → return "no_match".

Experiment modes:
    Mode A — "deterministic"
        Runs only the deterministic waterfall above.
        REQUIRED for all baseline comparison runs.

    Mode B — "deterministic_plus_llm"
        Intended to run the deterministic waterfall first, then fall back to
        an LLM call when no deterministic match is found.
        LLM fallback is NOT YET WIRED in Phase 2 (app/llm/step_generator.py
        is left unchanged). Calling select_ref in Mode B returns the
        deterministic result and logs a clear warning so that Mode B results
        are never silently mixed with Mode A baseline data.

Design constraints (Phase 2):
    - No writes to any execution file (step_runner, step_execution unchanged).
    - No modifications to the LLM planner (step_generator unchanged).
    - No production code path is altered.

Phase 3 addition:
    derive_intent(step) — converts a standard step dict (the planner output
    format) into a string intent for select_ref(). This is the explicit
    "connect planning to execution" bridge required by the Phase 3 spec.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import RefCandidate, SelectionResult
from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, ExperimentMode


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_candidate(element: AgentBrowserElement, match_type: str) -> RefCandidate:
    """Build a RefCandidate from a normalized AgentBrowserElement."""
    return RefCandidate(
        ref=element["ref"],
        role=element["role"],
        name=element["name"],
        match_type=match_type,
    )


def _filter_by_role(
    elements: List[AgentBrowserElement],
    role_filter: Optional[List[str]],
) -> List[AgentBrowserElement]:
    """
    Return only elements whose role is in role_filter (case-insensitive).
    If role_filter is None or empty, return all elements unchanged.
    """
    if not role_filter:
        return elements
    allowed = {r.lower() for r in role_filter}
    return [e for e in elements if e["role"].lower() in allowed]


def _log_result(result: SelectionResult) -> None:
    """Emit a structured log line for every selection outcome."""
    print(
        f"[ref_selector] "
        f"intent={result['intent']!r} "
        f"reason={result['selection_reason']} "
        f"chosen_ref={result['chosen_ref']!r} "
        f"candidates={len(result['candidates'])} "
        f"mode={result['mode']}",
        flush=True,
    )


def _apply_mode(result: SelectionResult, mode: ExperimentMode) -> SelectionResult:
    """
    Apply mode-specific behavior after the deterministic waterfall completes.

    Mode A ("deterministic"):
        Returns the result unchanged. This is the baseline path.

    Mode B ("deterministic_plus_llm"):
        LLM fallback is not yet wired in Phase 2. The deterministic result is
        returned unchanged AND a clear warning is printed so that any run in
        Mode B is immediately visible in logs and can never be silently treated
        as a Mode A baseline data point.

        Phase 3 will wire the LLM fallback here when no_match or ambiguous is
        returned in Mode B.
    """
    if mode == "deterministic_plus_llm":
        print(
            "[ref_selector] WARNING mode=deterministic_plus_llm — "
            "LLM fallback NOT YET WIRED (Phase 2 only implements deterministic). "
            "Returning deterministic result. "
            "This run MUST NOT be compared against Mode A baseline results.",
            flush=True,
        )
    return result


# ---------------------------------------------------------------------------
# Phase 3 — Planning-to-execution bridge
# ---------------------------------------------------------------------------

# Regex for extracting testid value from CSS selector strings like
# [data-testid='generate-api-key'] or [data-testid="submit-btn"].
_TESTID_RE = re.compile(r"""\[data-testid=['"]([^'"]+)['"]\]""")


def derive_intent(step: Dict[str, Any]) -> str:
    """
    Extract a ref-selection intent string from a standard step dict.

    This is the explicit bridge between the planner output format and the
    Agent Browser execution loop (Phase 3 spec: "connects planning to execution").

    Decision priority:
        1. step["text"]     — visible text written by the planner; ideal for
                               agent-browser name matching.
        2. data-testid      — extracted from step["selector"] and converted to
                               human-readable form ("generate-api-key" →
                               "generate api key").
        3. ""               — intent cannot be derived; caller should treat
                               this as a fatal step failure.

    Args:
        step — standard step dict from the planner:
               {"action": "click", "text": "...", "selector": "...", ...}

    Returns:
        A non-empty intent string on success, or "" when no intent can be
        derived from the step data.
    """
    text = (step.get("text") or "").strip()
    if text:
        return text

    selector = (step.get("selector") or "").strip()
    if selector:
        m = _TESTID_RE.search(selector)
        if m:
            # "generate-api-key" → "generate api key" for fuzzy name matching.
            return m.group(1).replace("-", " ").replace("_", " ")

    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_ref(
    intent: str,
    snapshot: AgentBrowserSnapshot,
    *,
    mode: ExperimentMode = "deterministic",
    role_filter: Optional[List[str]] = None,
) -> SelectionResult:
    """
    Select the best ref from snapshot for the given intent string.

    Runs the deterministic waterfall (exact → case-insensitive → partial),
    declaring ambiguous at any level where multiple candidates match.

    Args:
        intent      — natural-language target description, e.g. "Generate API
                      Key". Whitespace is stripped before comparison. An empty
                      string after stripping returns "no_match" immediately.
        snapshot    — normalized AgentBrowserSnapshot (from Phase 1 wrapper).
        mode        — experiment mode. Always use "deterministic" for baseline
                      comparison runs (Mode A). Pass "deterministic_plus_llm"
                      only for Mode B experimental runs.
        role_filter — optional ARIA role allowlist, e.g. ["button", "link"].
                      When provided, only elements with a matching role are
                      considered. None (default) searches all roles.

    Returns:
        SelectionResult with:
            chosen_ref       — "@eN" on success; "" on ambiguous or no_match.
            selection_reason — one of: "exact_match", "case_insensitive_match",
                               "partial_match", "ambiguous", "no_match".
            candidates       — elements considered at the deciding level.
            intent           — the (stripped) intent string.
            mode             — the experiment mode that produced this result.

    Success criteria (from plan):
        - Single exact match         → returns exact_match deterministically.
        - Multiple partial matches   → returns ambiguous (never guesses).
        - Zero matches at all levels → returns no_match cleanly.
    """
    intent = (intent or "").strip()

    # Guard: empty intent — cannot select anything.
    if not intent:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="no_match",
            candidates=[],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result

    # Apply optional role filter to restrict the search pool.
    pool = _filter_by_role(snapshot["interactive_elements"], role_filter)

    # ------------------------------------------------------------------
    # Level 1 — Exact case-sensitive name match
    # ------------------------------------------------------------------
    exact: List[AgentBrowserElement] = [e for e in pool if e["name"] == intent]

    if len(exact) == 1:
        result = SelectionResult(
            chosen_ref=exact[0]["ref"],
            selection_reason="exact_match",
            candidates=[_make_candidate(exact[0], "exact")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    if len(exact) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "exact") for e in exact],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    # ------------------------------------------------------------------
    # Level 2 — Case-insensitive exact name match
    # ------------------------------------------------------------------
    intent_lower = intent.lower()
    ci: List[AgentBrowserElement] = [
        e for e in pool if e["name"].lower() == intent_lower
    ]

    if len(ci) == 1:
        result = SelectionResult(
            chosen_ref=ci[0]["ref"],
            selection_reason="case_insensitive_match",
            candidates=[_make_candidate(ci[0], "case_insensitive")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    if len(ci) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "case_insensitive") for e in ci],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    # ------------------------------------------------------------------
    # Level 3 — Partial match (substring in either direction, case-insensitive)
    # ------------------------------------------------------------------
    partial: List[AgentBrowserElement] = [
        e
        for e in pool
        if intent_lower in e["name"].lower() or e["name"].lower() in intent_lower
    ]

    if len(partial) == 1:
        result = SelectionResult(
            chosen_ref=partial[0]["ref"],
            selection_reason="partial_match",
            candidates=[_make_candidate(partial[0], "partial")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    if len(partial) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "partial") for e in partial],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return _apply_mode(result, mode)

    # ------------------------------------------------------------------
    # No match at any level
    # ------------------------------------------------------------------
    result = SelectionResult(
        chosen_ref="",
        selection_reason="no_match",
        candidates=[],
        intent=intent,
        mode=mode,
    )
    _log_result(result)
    return result
