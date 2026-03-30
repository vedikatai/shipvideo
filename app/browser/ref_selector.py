from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import RefCandidate, SelectionResult
from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, ExperimentMode






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








_TESTID_RE = re.compile(r"""\[data-testid=['"]([^'"]+)['"]\]""")

_ID_FRAGMENT_RE = re.compile(r"#([\w-]+)")


def _slug_to_intent(slug: str) -> str:
    """Turn a testid or DOM id slug into words for fuzzy a11y name matching."""
    return slug.replace("-", " ").replace("_", " ").strip()


def derive_intent(step: Dict[str, Any]) -> str:
    """
    Extract a ref-selection intent string from a standard step dict.

    This is the explicit bridge between the planner output format and the
    Agent Browser execution loop (Phase 3 spec: "connects planning to execution").

    Decision priority:
        1. step["label"]    — visible text written by the planner; ideal for
                               agent-browser name matching.
        2. step["text"]     — legacy visible text field; treated the same way.
                               agent-browser name matching.
        3. data-testid      — extracted from step["selector"] and converted to
                               human-readable form ("generate-api-key" →
                               "generate api key").
        4. #id selector     — first `#foo-bar` fragment → "foo bar" for partial
                               matching against accessible names.
        5. ""               — intent cannot be derived; caller should treat
                               this as a fatal step failure.

    Args:
        step — standard step dict from the planner:
               {"action": "click", "text": "...", "selector": "...", ...}

    Returns:
        A non-empty intent string on success, or "" when no intent can be
        derived from the step data.
    """
    label = (step.get("label") or "").strip()
    if label:
        return label

    text = (step.get("text") or "").strip()
    if text:
        return text

    selector = (step.get("selector") or "").strip()
    if selector:
        m = _TESTID_RE.search(selector)
        if m:
            return _slug_to_intent(m.group(1))
        m_id = _ID_FRAGMENT_RE.search(selector)
        if m_id:

            return _slug_to_intent(m_id.group(1))

    return ""






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


    pool = _filter_by_role(list(snapshot["interactive_elements"]), role_filter)




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
        return result

    if len(exact) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "exact") for e in exact],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result




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
        return result

    if len(ci) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "case_insensitive") for e in ci],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result




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
        return result

    if len(partial) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "partial") for e in partial],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result




    result = SelectionResult(
        chosen_ref="",
        selection_reason="no_match",
        candidates=[],
        intent=intent,
        mode=mode,
    )
    _log_result(result)
    return result
