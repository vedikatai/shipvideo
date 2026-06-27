from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
_WS_RE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS_RE.sub(" ", (value or "").strip().lower())


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


def _tiebreak(
    candidates: Sequence[AgentBrowserElement],
    *,
    preferred_testids_lower: set[str],
    preferred_texts_lower: set[str],
    preferred_surface_lower: str,
) -> Optional[AgentBrowserElement]:
    """Prefer surface / testid / text hints when the ladder would otherwise give up."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    scored: List[Tuple[int, AgentBrowserElement]] = []
    for element in candidates:
        score = 0
        testid = _norm(str(element.get("testid") or ""))
        if preferred_testids_lower and testid in preferred_testids_lower:
            score += 40
        surface = _norm(str(element.get("surface") or ""))
        if preferred_surface_lower and surface == preferred_surface_lower:
            score += 30
        texts = [_norm(t) for t in _candidate_texts(element)]
        if preferred_texts_lower and any(p in texts or any(p in t for t in texts) for p in preferred_texts_lower):
            score += 15
        # Prefer visible interactive roles that usually own the click.
        role = _norm(str(element.get("role") or ""))
        if role in {"button", "link", "menuitem", "tab"}:
            score += 5
        scored.append((score, element))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored[0][0] <= 0:
        return None
    top = [item for item in scored if item[0] == scored[0][0]]
    if len(top) == 1:
        return top[0][1]
    return None


def _finish(
    *,
    chosen: Optional[AgentBrowserElement],
    reason: str,
    candidates: List[RefCandidate],
    intent: str,
    mode: ExperimentMode,
) -> SelectionResult:
    result = SelectionResult(
        chosen_ref=(chosen or {}).get("ref", "") if chosen else "",
        selection_reason=reason if chosen else ("ambiguous" if candidates else "no_match"),
        candidates=candidates,
        intent=intent,
        mode=mode,
    )
    if not chosen and candidates and reason != "ambiguous":
        result = SelectionResult(
            chosen_ref="",
            selection_reason="ambiguous",
            candidates=candidates,
            intent=intent,
            mode=mode,
        )
    _log_result(result)
    return result


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
    intent_norm = _norm(intent)

    if not intent_norm:
        result = SelectionResult(
            chosen_ref="",
            selection_reason="no_match",
            candidates=[],
            intent=intent,
            mode=mode,
        )
        _log_result(result)
        return result

    pool = _filter_by_role(list(snapshot.get("interactive_elements") or []), role_filter)

    preferred_testids_lower = {
        _norm(str(testid))
        for testid in (preferred_testids or [])
        if str(testid).strip()
    }
    preferred_texts_lower = {
        _norm(str(text))
        for text in (preferred_texts or [])
        if str(text).strip()
    }
    preferred_surface_lower = _norm(preferred_surface)

    def resolve_unique(
        matches: List[AgentBrowserElement],
        match_type: str,
        reason: str,
    ) -> Optional[SelectionResult]:
        if not matches:
            return None
        if len(matches) == 1:
            return _finish(
                chosen=matches[0],
                reason=reason,
                candidates=[_make_candidate(matches[0], match_type)],
                intent=intent,
                mode=mode,
            )
        winner = _tiebreak(
            matches,
            preferred_testids_lower=preferred_testids_lower,
            preferred_texts_lower=preferred_texts_lower,
            preferred_surface_lower=preferred_surface_lower,
        )
        candidates = [_make_candidate(e, match_type) for e in matches]
        if winner is not None:
            return _finish(
                chosen=winner,
                reason=f"{reason}_tiebreak",
                candidates=candidates,
                intent=intent,
                mode=mode,
            )
        return _finish(
            chosen=None,
            reason="ambiguous",
            candidates=candidates,
            intent=intent,
            mode=mode,
        )

    # Priority ladder (stable + preference-aware):
    # 1) preferred testid on element  2) intent==testid  3) aria  4) id
    # 5) name (normalized)  6) partial name  7) multi-field scored match
    if preferred_testids_lower:
        preferred_hits = [
            e for e in pool
            if _norm(str(e.get("testid") or "")) in preferred_testids_lower
        ]
        hit = resolve_unique(preferred_hits, "preferred_testid", "preferred_testid_match")
        if hit is not None:
            return hit

    testid_exact = [
        e for e in pool
        if _norm(str(e.get("testid") or "")) == intent_norm
    ]
    hit = resolve_unique(testid_exact, "testid", "testid_match")
    if hit is not None:
        return hit

    aria_exact = [
        e for e in pool
        if _norm(str(e.get("aria_label") or "")) == intent_norm
    ]
    hit = resolve_unique(aria_exact, "aria", "aria_match")
    if hit is not None:
        return hit

    id_exact = [
        e for e in pool
        if _norm(str(e.get("element_id") or "")) == intent_norm
    ]
    hit = resolve_unique(id_exact, "id", "id_match")
    if hit is not None:
        return hit

    name_exact = [
        e for e in pool
        if _norm(str(e.get("name") or "")) == intent_norm
    ]
    hit = resolve_unique(name_exact, "exact", "exact_match")
    if hit is not None:
        return hit

    partial = [
        e
        for e in pool
        if intent_norm
        and (
            intent_norm in _norm(str(e.get("name") or ""))
            or _norm(str(e.get("name") or "")) in intent_norm
        )
    ]
    hit = resolve_unique(partial, "partial", "partial_match")
    if hit is not None:
        return hit

    scored_candidates: List[Tuple[int, AgentBrowserElement, str]] = []
    for element in pool:
        score = 0
        match_type = ""
        for value in _candidate_texts(element):
            value_norm = _norm(value)
            if not value_norm:
                continue
            if value_norm == intent_norm:
                score = max(score, 120)
                match_type = match_type or "exact"
            elif intent_norm in value_norm or value_norm in intent_norm:
                score = max(score, 75)
                match_type = match_type or "partial"
        if not score:
            continue
        if preferred_testids_lower and _norm(str(element.get("testid") or "")) in preferred_testids_lower:
            score += 25
        if preferred_texts_lower and any(
            preferred in _norm(text) for preferred in preferred_texts_lower for text in _candidate_texts(element)
        ):
            score += 10
        if preferred_surface_lower and preferred_surface_lower == _norm(str(element.get("surface") or "")):
            score += 15
        scored_candidates.append((score, element, match_type or "scored"))

    if scored_candidates:
        scored_candidates.sort(key=lambda item: item[0], reverse=True)
        top_score = scored_candidates[0][0]
        top = [item for item in scored_candidates if item[0] == top_score]
        if len(top) == 1:
            return _finish(
                chosen=top[0][1],
                reason="scored_match",
                candidates=[_make_candidate(top[0][1], top[0][2])],
                intent=intent,
                mode=mode,
            )
        winner = _tiebreak(
            [item[1] for item in top],
            preferred_testids_lower=preferred_testids_lower,
            preferred_texts_lower=preferred_texts_lower,
            preferred_surface_lower=preferred_surface_lower,
        )
        candidates = [_make_candidate(item[1], item[2]) for item in top]
        if winner is not None:
            return _finish(
                chosen=winner,
                reason="scored_match_tiebreak",
                candidates=candidates,
                intent=intent,
                mode=mode,
            )
        return _finish(
            chosen=None,
            reason="ambiguous",
            candidates=candidates,
            intent=intent,
            mode=mode,
        )

    result = SelectionResult(
        chosen_ref="",
        selection_reason="no_match",
        candidates=[],
        intent=intent,
        mode=mode,
    )
    _log_result(result)
    return result
