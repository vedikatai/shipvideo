from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from app.config_types import CaptureSettings
from app.context.dom_extractor import extract_dom_context
from app.dom_schema import ExperimentMode
from app.execution.navigation_detector import capture_state, detect_major_change, wait_stable_after_navigation
from app.llm.retry_engine import regenerate_with_feedback
from app.policy.selector_validator import validate_step_against_dom

# ---------------------------------------------------------------------------
# Phase 3 — AB runner loop controls (hard limits, never configurable below these)
# ---------------------------------------------------------------------------

#: Maximum steps processed by run_ab_stepwise in a single invocation.
MAX_STEPS_PER_RUN: int = 10

#: Maximum per-step retry attempts for stale-ref recovery and wrong-click retry.
MAX_RETRIES_PER_STEP: int = 2

#: Minimum wait in milliseconds after any click, before re-snapshot.
#: Gives the page time to settle (animation, navigation, async state).
WAIT_AFTER_CLICK_MS: int = 1500


def _log(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def _build_metrics(
    results: List[Dict[str, Any]],
    total_initial_steps: int,
    total_retries: int,
) -> Dict[str, Any]:
    """
    Compute Phase 4 comparison metrics from a runner's per-step results list.

    Produces a metrics dict with the same keys for both run_stepwise and
    run_ab_stepwise so ExperimentLogger can consume them uniformly.

    Args:
        results             — list of per-step result dicts from the runner.
        total_initial_steps — denominator for success_rate.
        total_retries       — total retry/regeneration count for the run.
    """
    succeeded = sum(1 for r in results if r.get("status") == "ok")
    wrong_click_count = sum(
        1 for r in results if r.get("outcome") == "wrong_click"
    )
    failure_counts: Dict[str, int] = {}
    for r in results:
        outcome = (r.get("outcome") or "").strip()
        if outcome and outcome not in ("success", "pending", "ok"):
            failure_counts[outcome] = failure_counts.get(outcome, 0) + 1

    latencies = [
        r.get("step_latency_ms", 0)
        for r in results
        if r.get("step_latency_ms", 0) > 0
    ]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    return {
        "success_rate": succeeded / max(total_initial_steps, 1),
        "retries_per_run": float(total_retries),
        "failure_type_counts": failure_counts,
        "wrong_click_count": wrong_click_count,
        "avg_step_latency_ms": round(avg_latency, 1),
    }


def _classify_final_outcome(*, success: bool, failure_reason: str = "") -> str:
    """
    Convert runner success/failure state into the Phase 5 machine-readable
    decision categories used by experiment reporting.

    Categories:
        passed       — run completed successfully.
        ambiguous    — target ambiguity blocked a safe action.
        regressed    — concrete target/execution failure occurred.
        inconclusive — outcome does not clearly fit the above categories.
    """
    if success:
        return "passed"

    reason = (failure_reason or "").strip().lower()
    if "ambiguous" in reason:
        return "ambiguous"

    regression_prefixes = (
        "validation_failed",
        "execution_failed",
        "missing_click_target",
        "unknown_action",
        "goto_failed",
        "click_failed",
        "snapshot_failed",
        "agent_browser_error",
        "wrong_click",
        "no_match",
        "no_intent",
        "repeated_action",
    )
    if reason.startswith(regression_prefixes):
        return "regressed"

    return "inconclusive"


def _resolve_url(base: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def _execute_one(
    page: Page,
    base_url: str,
    step: Dict[str, Any],
    out_dir: Path,
    shot_idx: int,
    *,
    full_page: bool = False,
) -> tuple[bool, int, str | None]:
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
        page.screenshot(path=str(path), full_page=full_page)
        return True, shot_idx + 1, None
    return False, shot_idx, f"unknown_action:{action}"


def run_stepwise(
    *,
    preview_url: str,
    initial_steps: List[Dict[str, Any]],
    objective: Dict[str, Any],
    screenshot_dir: Path,
    max_retries_per_failure: int = 3,
    capture_settings: Optional[CaptureSettings] = None,
) -> Dict[str, Any]:
    """
    Step-by-step execution model:
      execute step -> detect navigation/major change -> re-anchor -> regenerate next steps from fresh DOM.

    Phase 4 additions (backward-compatible):
        - step_latency_ms  added to every step result entry.
        - total_retries    accumulated across all regenerate_with_feedback calls.
        - metrics          dict appended to all return paths for ExperimentLogger.
        - steps_succeeded / steps_failed added to failure return paths.
    """
    cs = capture_settings or CaptureSettings()

    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = list(initial_steps)
    shot_idx = 1
    total_retries = 0  # Phase 4: accumulates all regeneration attempts

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": cs.viewport_width, "height": cs.viewport_height})
        page.goto(preview_url, wait_until="domcontentloaded", timeout=15000)
        wait_stable_after_navigation(page)
        dom_ctx = extract_dom_context(page)

        i = 0
        while i < len(queue):
            step = queue[i]
            _step_t0 = time.monotonic()  # Phase 4: per-step timing start

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
                total_retries += attempts  # Phase 4
                _log("step.regenerated_on_validation_failure", {"index": i, "reason": reason, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"validation_failed:{reason}",
                        ),
                        "steps_succeeded": len(results),
                        "steps_failed": 1,
                        "failure_reason": f"validation_failed:{reason}",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), total_retries),
                    }
                queue[i : i + 1] = regenerated
                step = queue[i]

            prev = capture_state(page)
            ok_exec, shot_idx, err = _execute_one(page, preview_url, step, screenshot_dir, shot_idx, full_page=cs.full_page_screenshots)
            if not ok_exec:
                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": err or "execution_failed", "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                total_retries += attempts  # Phase 4
                _log("step.regenerated_on_execution_failure", {"index": i, "error": err, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=err or "execution_failed",
                        ),
                        "steps_succeeded": len(results),
                        "steps_failed": 1,
                        "failure_reason": err or "execution_failed",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), total_retries),
                    }
                queue[i : i + 1] = regenerated
                continue

            _step_latency_ms = int((time.monotonic() - _step_t0) * 1000)  # Phase 4
            results.append({"index": i, "step": step, "status": "ok", "step_latency_ms": _step_latency_ms})

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
                total_retries += attempts  # Phase 4
                _log("navigation.reanchored", {"index": i, "attempts": attempts})
                if regenerated:
                    queue = queue[: i + 1] + regenerated
            i += 1

        browser.close()

    return {
        "success": True,
        "final_outcome": _classify_final_outcome(success=True),
        "steps_succeeded": len(results),
        "steps_failed": 0,
        "results": results,
        "metrics": _build_metrics(results, len(initial_steps), total_retries),
    }


# ---------------------------------------------------------------------------
# Phase 3 — Agent Browser CLI execution path
# ---------------------------------------------------------------------------

def _detect_state_change(
    url_before: str,
    url_after: str,
    snap_text_before: str,
    snap_text_after: str,
) -> bool:
    """
    Return True when a meaningful page state change is detected after a click.

    Two signals:
        1. URL changed  — navigated to a different page.
        2. Snapshot diff — accessibility tree text changed, indicating DOM
                           updates (modal opened, form submitted, content
                           replaced, etc.).

    Both signals use the normalised AgentBrowserSnapshot fields so the check
    is self-consistent with the data the ref-selector already consumed.
    """
    if url_before != url_after:
        return True
    if snap_text_before != snap_text_after:
        return True
    return False


def run_ab_stepwise(
    *,
    preview_url: str,
    initial_steps: List[Dict[str, Any]],
    screenshot_dir: Path,
    max_steps_per_run: int = MAX_STEPS_PER_RUN,
    max_retries_per_step: int = MAX_RETRIES_PER_STEP,
    mode: str = "deterministic",
    capture_settings: Optional[CaptureSettings] = None,
    session: str = "ab_exp",
) -> Dict[str, Any]:
    """
    Agent Browser CLI execution loop — experimental side-path parallel to run_stepwise.

    Implements the Phase 3 execution loop exactly as specified:
        open preview URL
        → for each step (up to max_steps_per_run):
            goto   — cli.open(full_url)
            screenshot — cli.screenshot(path)
            click:
                snapshot → select_ref → before-screenshot
                → click(ref) → wait(WAIT_AFTER_CLICK_MS)
                → after-screenshot → re-snapshot → state-change check
                → retry up to max_retries_per_step on stale-ref / wrong-click

    Loop controls (hard, non-negotiable):
        max_steps_per_run   = 10  — prevents runaway loops.
        max_retries_per_step = 2  — stale-ref recovery + wrong-click retry.

    Fatal outcomes (loop stops immediately):
        no_match        — no ref found for intent at any waterfall level.
        ambiguous       — multiple refs matched; cannot safely select.
        repeated_action — same ref chosen on same URL without state change.
        click_failed    — CLI click command raised AgentBrowserError after
                          all retries (may be a persistent stale ref).
        snapshot_failed — cannot read current page state.
        no_intent       — step has no text and no parseable selector.

    Non-fatal outcomes (recorded, loop continues):
        wrong_click     — click ran without error but no state change
                          detected after all retries. Recorded for accuracy
                          analysis in Phase 4. Loop continues to next step.

    Return shape is identical to run_stepwise so step_execution.py can
    process both paths with the same logic.

    NOTE: The existing Playwright run_stepwise path is completely unchanged.
    This function is an additive side-path only.

    Args:
        preview_url        — base URL of the preview deployment.
        initial_steps      — step list from the planner (same format as
                             run_stepwise). Processed in order up to
                             max_steps_per_run.
        screenshot_dir     — directory for shot*.png files.
        max_steps_per_run  — hard upper bound on steps processed.
        max_retries_per_step — retry attempts per click step.
        mode               — ExperimentMode: "deterministic" (Mode A, default)
                             or "deterministic_plus_llm" (Mode B).
        capture_settings   — CaptureSettings for viewport etc.; uses defaults
                             when not provided.
        session            — agent-browser session name; use a unique value
                             per concurrent job for isolation.
    """
    # Local imports: avoid pulling in the browser sub-package for callers that
    # only use run_stepwise (Playwright path).
    from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError
    from app.browser.ref_selector import derive_intent, select_ref
    from app.context.dom_extractor import extract_ab_context

    cs = capture_settings or CaptureSettings()

    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    steps_succeeded = 0
    shot_idx = 1
    # Tracks the last (url, chosen_ref) pair to detect stuck loops.
    last_action_key: Optional[str] = None
    _total_retries = 0  # Phase 4: total click-step retry attempts across all steps

    _log("ab_runner.start", {
        "preview_url": preview_url,
        "total_steps": len(initial_steps),
        "mode": mode,
        "session": session,
    })

    cli = AgentBrowserCLI(session=session)

    try:
        cli.open(preview_url)

        for step_idx, step in enumerate(initial_steps[:max_steps_per_run]):
            action = step.get("action")
            _step_t0 = time.monotonic()  # Phase 4: per-step timing start
            step_result: Dict[str, Any] = {
                "index": step_idx,
                "step": step,
                "status": "failed",
                "outcome": "pending",
                "backend": "agent_browser_cli",
                "mode": mode,
            }

            # ------------------------------------------------------------------
            # goto — navigate to a new URL within the session
            # ------------------------------------------------------------------
            if action == "goto":
                url = step.get("url") or "/"
                full_url = _resolve_url(preview_url, url)
                try:
                    cli.open(full_url)
                    step_result.update({"status": "ok", "outcome": "success"})
                    steps_succeeded += 1
                    last_action_key = None  # navigation resets repeat detection
                except AgentBrowserError as exc:
                    step_result.update({"outcome": "click_failed", "error": str(exc)})
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    _log("ab_runner.goto_failed", {"index": step_idx, "url": full_url, "error": str(exc)})
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"goto_failed:{full_url}",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": f"goto_failed:{full_url}",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                results.append(step_result)
                continue

            # ------------------------------------------------------------------
            # screenshot — capture current page to disk
            # ------------------------------------------------------------------
            if action == "screenshot":
                path = screenshot_dir / f"shot{shot_idx}.png"
                try:
                    cli.screenshot(path)
                    shot_idx += 1
                except AgentBrowserError:
                    pass  # screenshot failure is non-fatal; log implicitly via cli
                step_result.update({"status": "ok", "outcome": "success"})
                steps_succeeded += 1
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                results.append(step_result)
                continue

            # ------------------------------------------------------------------
            # click — core AB execution loop
            # ------------------------------------------------------------------
            if action == "click":
                intent = derive_intent(step)
                if not intent:
                    step_result.update({"outcome": "no_intent", "status": "failed"})
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    _log("ab_runner.no_intent", {"index": step_idx, "step": step})
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="no_intent",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": "no_intent",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                step_result["intent"] = intent
                outcome = "pending"

                for attempt in range(1, max_retries_per_step + 1):
                    _log("ab_runner.click_attempt", {
                        "index": step_idx, "attempt": attempt, "intent": intent,
                    })

                    # Snapshot current page state.
                    # Save raw JSON on first attempt only; post-click re-snapshots
                    # use save_raw=False to keep disk usage bounded.
                    try:
                        snap = extract_ab_context(cli, save_raw=(attempt == 1))
                    except AgentBrowserError as exc:
                        outcome = "snapshot_failed"
                        step_result["error"] = str(exc)
                        _log("ab_runner.snapshot_failed", {"index": step_idx, "attempt": attempt})
                        break  # not retriable

                    # Deterministic ref selection.
                    sel = select_ref(intent, snap, mode=mode)  # type: ignore[arg-type]
                    step_result.update({
                        "chosen_ref": sel["chosen_ref"],
                        "selection_reason": sel["selection_reason"],
                    })

                    # If a11y name matching fails but the planner sent a CSS/XPath selector,
                    # fall back to agent-browser click(<selector>) — the stock CLI accepts
                    # refs (@e1), CSS, or XPath (see `agent-browser click --help`).
                    raw_selector = (step.get("selector") or "").strip()
                    if sel["chosen_ref"]:
                        click_target = sel["chosen_ref"]
                    elif sel["selection_reason"] == "no_match" and raw_selector:
                        click_target = raw_selector
                        step_result.update({
                            "chosen_ref": raw_selector,
                            "selection_reason": "css_selector_fallback",
                        })
                        _log("ab_runner.css_selector_fallback", {
                            "index": step_idx, "selector": raw_selector, "intent": intent,
                        })
                    else:
                        # ambiguous (or no_match with no selector) — cannot safely click.
                        outcome = sel["selection_reason"]
                        _log("ab_runner.selection_failed", {
                            "index": step_idx, "attempt": attempt,
                            "reason": outcome, "intent": intent,
                        })
                        break

                    # Repeat-action guard: same target on same URL without prior
                    # state change means we are stuck in a loop.
                    action_key = f"{snap['current_url']}:{click_target}"
                    if action_key == last_action_key:
                        outcome = "repeated_action"
                        _log("ab_runner.repeated_action", {
                            "index": step_idx,
                            "ref": click_target,
                            "url": snap["current_url"],
                        })
                        break

                    # Before-click screenshot.
                    before_path = screenshot_dir / f"shot{shot_idx}.png"
                    try:
                        cli.screenshot(before_path)
                        shot_idx += 1
                        step_result["before_screenshot"] = str(before_path)
                    except AgentBrowserError:
                        pass  # non-fatal

                    # Execute the click (ref @e1 or CSS/XPath from fallback).
                    try:
                        cli.click(click_target)
                    except AgentBrowserError as exc:
                        # Possible stale ref — re-snapshot and retry selection.
                        outcome = "click_failed"
                        step_result["error"] = str(exc)
                        _log("ab_runner.click_failed", {
                            "index": step_idx, "attempt": attempt,
                            "ref": click_target, "error": str(exc),
                        })
                        continue  # retry: re-snapshot will get fresh refs

                    # Post-click wait: minimum 1.5 s floor to let the page settle
                    # before state-change detection and after-screenshot.
                    try:
                        cli.wait(WAIT_AFTER_CLICK_MS)
                    except AgentBrowserError:
                        pass  # wait failure does not abort the step

                    # After-click screenshot.
                    after_path = screenshot_dir / f"shot{shot_idx}.png"
                    try:
                        cli.screenshot(after_path)
                        shot_idx += 1
                        step_result["after_screenshot"] = str(after_path)
                    except AgentBrowserError:
                        pass  # non-fatal

                    # Re-snapshot for state-change detection.
                    try:
                        snap_after = extract_ab_context(cli, save_raw=False)
                    except AgentBrowserError as exc:
                        outcome = "snapshot_failed"
                        step_result["error"] = str(exc)
                        break

                    url_before = snap["current_url"]
                    url_after = snap_after["current_url"]
                    state_changed = _detect_state_change(
                        url_before, url_after,
                        snap["snapshot_text"], snap_after["snapshot_text"],
                    )

                    step_result.update({
                        "url_before": url_before,
                        "url_after": url_after,
                        "state_changed": state_changed,
                    })
                    last_action_key = action_key

                    if state_changed:
                        # Success: observable UI change confirmed.
                        outcome = "success"
                        _log("ab_runner.state_changed", {
                            "index": step_idx, "attempt": attempt,
                            "url_before": url_before, "url_after": url_after,
                        })
                        break
                    else:
                        # Wrong click: technically succeeded, wrong result.
                        # Defined per plan: click ran without error, but expected
                        # success condition (state change) was not reached.
                        outcome = "wrong_click"
                        _log("ab_runner.wrong_click", {
                            "index": step_idx, "attempt": attempt,
                            "ref": click_target, "intent": intent,
                        })
                        # Retry: re-snapshot may reveal updated element tree.

                # End of retry loop.
                _total_retries += attempt - 1  # Phase 4: retries = attempts − 1
                step_result["outcome"] = outcome
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)

                # Fatal outcomes: stop the run immediately.
                _FATAL_OUTCOMES = frozenset({
                    "no_match", "ambiguous", "repeated_action",
                    "click_failed", "snapshot_failed", "no_intent",
                })
                if outcome in _FATAL_OUTCOMES:
                    step_result["status"] = "failed"
                    results.append(step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=outcome,
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": outcome,
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                # wrong_click after retry exhaustion is non-fatal: the step ran,
                # it just did not produce an expected state change. Recorded for
                # Phase 4 accuracy analysis; execution continues.
                step_result["status"] = "ok"
                steps_succeeded += 1
                results.append(step_result)
                continue

            # ------------------------------------------------------------------
            # Unknown action — skip with warning; never fatal
            # ------------------------------------------------------------------
            step_result.update({"outcome": f"unknown_action:{action}", "status": "ok"})
            step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
            _log("ab_runner.unknown_action", {"index": step_idx, "action": action})
            results.append(step_result)

    except AgentBrowserError as exc:
        _log("ab_runner.fatal_error", {"error": str(exc)})
        return {
            "success": False,
            "final_outcome": _classify_final_outcome(
                success=False,
                failure_reason=f"agent_browser_error:{exc}",
            ),
            "steps_succeeded": steps_succeeded,
            "steps_failed": 1,
            "failure_reason": f"agent_browser_error:{exc}",
            "results": results,
            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
        }
    finally:
        # Always close the browser session — runs even on early returns.
        try:
            cli.close()
        except Exception:
            pass

    _log("ab_runner.complete", {
        "steps_succeeded": steps_succeeded, "total_results": len(results),
    })
    return {
        "success": True,
        "final_outcome": _classify_final_outcome(success=True),
        "steps_succeeded": steps_succeeded,
        "steps_failed": 0,
        "results": results,
        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
    }

