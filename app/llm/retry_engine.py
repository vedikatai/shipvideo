from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page

from app.llm.step_generator import generate_next_steps, generate_single_step_toward_testid
from app.policy.selector_validator import validate_step_against_dom


def regenerate_with_feedback(
    *,
    objective: Dict[str, Any],
    dom_context: Dict[str, Any],
    error_context: Dict[str, Any],
    max_attempts: int = 3,
    page: Optional[Page] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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


def regenerate_single_step_toward_testid(
    *,
    objective: Dict[str, Any],
    target_testid: str,
    snapshot: Dict[str, Any],
    dom_context: Dict[str, Any],
    max_attempts: int = 2,
    page: Optional[Page] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    previous_error: Dict[str, Any] = {}

    for i in range(1, max_attempts + 1):
        try:
            step = generate_single_step_toward_testid(
                target_testid=target_testid,
                snapshot=snapshot,
                objective=objective,
                previous_error=previous_error,
            )
        except RuntimeError as e:
            attempts.append({"attempt": i, "status": "generation_error", "error": str(e)})
            previous_error = {"error": str(e)}
            continue
        ok, reason = validate_step_against_dom(step, dom_context, page=page)
        attempts.append({"attempt": i, "status": "ok" if ok else "rejected", "reason": reason})
        if ok:
            return step, attempts
        previous_error = {"error": reason}

    return None, attempts
