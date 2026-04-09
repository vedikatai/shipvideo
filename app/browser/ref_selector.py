from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import RefCandidate, SelectionResult
from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, ExperimentMode






def _make_candidate(element: AgentBrowserElement, match_type: str) -> RefCandidate:
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
    if not role_filter:
        return elements
    allowed = {r.lower() for r in role_filter}
    return [e for e in elements if e["role"].lower() in allowed]


def _log_result(result: SelectionResult) -> None:
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
    return slug.replace("-", " ").replace("_", " ").strip()


def _candidate_texts(element: AgentBrowserElement) -> List[str]:
    values = [
        str(element.get("name") or "").strip(),
        str(element.get("testid") or "").strip(),
        str(element.get("aria_label") or "").strip(),
        str(element.get("element_id") or "").strip(),
        str(element.get("nearby_text") or "").strip(),
    ]
    return [value for value in values if value]


def derive_intent(step: Dict[str, Any]) -> str:
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
    preferred_testids: Optional[List[str]] = None,
    preferred_surface: str = "",
    preferred_texts: Optional[List[str]] = None,
) -> SelectionResult:
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




    preferred_testids_lower = {
        str(testid).strip().lower()
        for testid in (preferred_testids or [])
        if str(testid).strip()
    }
    preferred_texts_lower = {
        str(text).strip().lower()
        for text in (preferred_texts or [])
        if str(text).strip()
    }
    preferred_surface_lower = preferred_surface.strip().lower()

    testid_exact: List[AgentBrowserElement] = [
        e for e in pool
        if str(e.get("testid") or "").strip().lower() == intent.lower()
    ]
    if len(testid_exact) == 1:
        result = SelectionResult(
            chosen_ref=testid_exact[0]["ref"],
            selection_reason="testid_match",
            candidates=[_make_candidate(testid_exact[0], "testid")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result
    if len(testid_exact) > 1:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(e, "testid") for e in testid_exact],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result

    aria_exact: List[AgentBrowserElement] = [
        e for e in pool
        if str(e.get("aria_label") or "").strip().lower() == intent.lower()
    ]
    if len(aria_exact) == 1:
        result = SelectionResult(
            chosen_ref=aria_exact[0]["ref"],
            selection_reason="aria_match",
            candidates=[_make_candidate(aria_exact[0], "aria")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result

    id_exact: List[AgentBrowserElement] = [
        e for e in pool
        if str(e.get("element_id") or "").strip().lower() == intent.lower()
    ]
    if len(id_exact) == 1:
        result = SelectionResult(
            chosen_ref=id_exact[0]["ref"],
            selection_reason="id_match",
            candidates=[_make_candidate(id_exact[0], "id")],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result

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




    scored_candidates: List[tuple[int, AgentBrowserElement, str]] = []
    for element in pool:
        score = 0
        match_type = ""
        for value in _candidate_texts(element):
            value_lower = value.lower()
            if value_lower == intent_lower:
                score = max(score, 120)
                match_type = match_type or "exact"
            elif intent_lower and (intent_lower in value_lower or value_lower in intent_lower):
                score = max(score, 75)
                match_type = match_type or "partial"
        if not score:
            continue
        if preferred_testids_lower and str(element.get("testid") or "").strip().lower() in preferred_testids_lower:
            score += 25
        if preferred_texts_lower and any(
            preferred in text.lower() for preferred in preferred_texts_lower for text in _candidate_texts(element)
        ):
            score += 10
        if preferred_surface_lower and preferred_surface_lower == str(element.get("surface") or "").strip().lower():
            score += 15
        scored_candidates.append((score, element, match_type or "scored"))

    if scored_candidates:
        scored_candidates.sort(key=lambda item: item[0], reverse=True)
        top_score = scored_candidates[0][0]
        top = [item for item in scored_candidates if item[0] == top_score]
        if len(top) == 1:
            result = SelectionResult(
                chosen_ref=top[0][1]["ref"],
                selection_reason="scored_match",
                candidates=[_make_candidate(top[0][1], top[0][2])],
                intent=intent,
                mode=mode,
            )
            _log_result(result)
            return result
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=[_make_candidate(item[1], item[2]) for item in top],
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
