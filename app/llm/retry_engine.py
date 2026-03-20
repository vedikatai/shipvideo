from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page

from app.llm.step_generator import generate_next_steps
from app.policy.selector_validator import validate_step_against_dom


def regenerate_with_feedback(
    *,
    objective: Dict[str, Any],
    dom_context: Dict[str, Any],
    error_context: Dict[str, Any],
    max_attempts: int = 3,
    page: Optional[Page] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Retry loop with strict selector validation on regenerated steps.

    When ``page`` is provided, each regenerated step is validated against the
    live page (existence check) in addition to the static DOM-context check.
    Defaults to ``None`` so callers without a live page are unaffected.
    """
    attempts: List[Dict[str, Any]] = []
    previous_error = error_context

    for i in range(1, max_attempts + 1):
        try:
            steps = generate_next_steps(
                objective=objective,
                dom_context=dom_context,
                previous_error=previous_error,
                max_steps=2,
            )
        except RuntimeError as e:
            attempts.append({"attempt": i, "status": "generation_error", "error": str(e)})
            previous_error = {"error": str(e)}
            continue
        if not steps:
            attempts.append({"attempt": i, "status": "empty_steps"})
            previous_error = {"error": "LLM returned empty steps"}
            continue

        ok_all = True
        reasons: List[str] = []
        for s in steps:
            ok, reason = validate_step_against_dom(s, dom_context, page=page)
            if not ok:
                ok_all = False
                reasons.append(reason)
        attempts.append({"attempt": i, "status": "ok" if ok_all else "rejected", "reasons": reasons})
        if ok_all:
            return steps, attempts
        previous_error = {"error": "; ".join(reasons)}

    return [], attempts

