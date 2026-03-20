from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.llm.step_generator import generate_next_steps
from app.policy.selector_validator import validate_step_against_dom


def regenerate_with_feedback(
    *,
    objective: Dict[str, Any],
    dom_context: Dict[str, Any],
    error_context: Dict[str, Any],
    max_attempts: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Retry loop with strict selector validation on regenerated steps.
    """
    attempts: List[Dict[str, Any]] = []
    previous_error = error_context

    for i in range(1, max_attempts + 1):
        steps = generate_next_steps(
            objective=objective,
            dom_context=dom_context,
            previous_error=previous_error,
            max_steps=2,
        )
        if not steps:
            attempts.append({"attempt": i, "status": "empty_steps"})
            previous_error = {"error": "LLM returned empty steps"}
            continue

        ok_all = True
        reasons: List[str] = []
        for s in steps:
            ok, reason = validate_step_against_dom(s, dom_context)
            if not ok:
                ok_all = False
                reasons.append(reason)
        attempts.append({"attempt": i, "status": "ok" if ok_all else "rejected", "reasons": reasons})
        if ok_all:
            return steps, attempts
        previous_error = {"error": "; ".join(reasons)}

    return [], attempts

