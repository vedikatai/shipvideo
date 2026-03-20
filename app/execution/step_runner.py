from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from app.context.dom_extractor import extract_dom_context
from app.execution.navigation_detector import capture_state, detect_major_change, wait_stable_after_navigation
from app.llm.retry_engine import regenerate_with_feedback
from app.policy.selector_validator import validate_step_against_dom


def _log(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def _resolve_url(base: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def _execute_one(page: Page, base_url: str, step: Dict[str, Any], out_dir: Path, shot_idx: int) -> tuple[bool, int, str | None]:
    action = step.get("action")
    if action == "goto":
        page.goto(_resolve_url(base_url, step.get("url") or "/"), wait_until="domcontentloaded", timeout=15000)
        return True, shot_idx, None
    if action == "click":
        selector = (step.get("selector") or "").strip()
        text = (step.get("text") or "").strip()
        if selector:
            page.locator(selector).first.click(timeout=8000)
            return True, shot_idx, None
        if text:
            page.get_by_text(text, exact=True).first.click(timeout=8000)
            return True, shot_idx, None
        return False, shot_idx, "missing_click_target"
    if action == "screenshot":
        path = out_dir / f"shot{shot_idx}.png"
        page.screenshot(path=str(path), full_page=False)
        return True, shot_idx + 1, None
    return False, shot_idx, f"unknown_action:{action}"


def run_stepwise(
    *,
    preview_url: str,
    initial_steps: List[Dict[str, Any]],
    objective: Dict[str, Any],
    screenshot_dir: Path,
    max_retries_per_failure: int = 3,
) -> Dict[str, Any]:
    """
    Step-by-step execution model:
      execute step -> detect navigation/major change -> re-anchor -> regenerate next steps from fresh DOM.
    """
    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = list(initial_steps)
    shot_idx = 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.goto(preview_url, wait_until="domcontentloaded", timeout=15000)
        wait_stable_after_navigation(page)
        dom_ctx = extract_dom_context(page)
        state_before = capture_state(page)

        i = 0
        while i < len(queue):
            step = queue[i]

            ok, reason = validate_step_against_dom(step, dom_ctx, page=page)
            if not ok:
                # regenerate immediately using fresh DOM
                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": reason, "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                _log("step.regenerated_on_validation_failure", {"index": i, "reason": reason, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {"success": False, "failure_reason": f"validation_failed:{reason}", "results": results}
                queue[i : i + 1] = regenerated
                step = queue[i]

            prev = capture_state(page)
            ok_exec, shot_idx, err = _execute_one(page, preview_url, step, screenshot_dir, shot_idx)
            if not ok_exec:
                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": err or "execution_failed", "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                _log("step.regenerated_on_execution_failure", {"index": i, "error": err, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {"success": False, "failure_reason": err or "execution_failed", "results": results}
                queue[i : i + 1] = regenerated
                continue

            results.append({"index": i, "step": step, "status": "ok"})
            now = capture_state(page)
            nav_changed = detect_major_change(prev, now)
            if nav_changed:
                wait_stable_after_navigation(page)
                dom_ctx = extract_dom_context(page)
                # Mandatory re-anchoring: regenerate remaining steps from fresh DOM only.
                remaining_objective = {**objective, "remaining_from_index": i + 1}
                regenerated, attempts = regenerate_with_feedback(
                    objective=remaining_objective,
                    dom_context=dom_ctx,
                    error_context={"event": "navigation_boundary", "at_index": i},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                _log("navigation.reanchored", {"index": i, "attempts": attempts})
                if regenerated:
                    queue = queue[: i + 1] + regenerated
            i += 1

        browser.close()

    return {"success": True, "steps_succeeded": len(results), "steps_failed": 0, "results": results}

