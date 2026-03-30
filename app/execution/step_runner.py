from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from app.browser.agent_browser_types import (
    ABActionabilityResult,
    ABPageSettleResult,
    ABTargetResolution,
    StepValidationResult,
    ValidationCondition,
)
from app.config_types import CaptureSettings
from app.context.dom_extractor import extract_dom_context
from app.dom_schema import ExperimentMode
from app.execution.navigation_detector import capture_state, detect_major_change, wait_stable_after_navigation
from app.llm.retry_engine import regenerate_with_feedback
from app.policy.selector_validator import validate_step_against_dom
from observability import record_agent_browser_diagnostics






MAX_STEPS_PER_RUN: int = 10


MAX_RETRIES_PER_STEP: int = 2
MAX_AB_REPLANS_PER_RUN: int = 1



AB_DOMCONTENTLOADED_TIMEOUT_S: int = 15
AB_NETWORKIDLE_TIMEOUT_S: int = 8
AB_VALIDATION_WAIT_TIMEOUT_S: int = 8
AB_SCROLL_RETRY_COUNT: int = 3
AB_SCROLL_RETRY_PX: int = 400
AB_SCROLL_SETTLE_TIMEOUT_S: int = 1


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
    unvalidated_count = sum(
        1 for r in results if r.get("outcome") == "unvalidated"
    )
    failure_counts: Dict[str, int] = {}
    for r in results:
        outcome = (r.get("outcome") or "").strip()
        if outcome and outcome not in ("success", "pending", "ok", "unvalidated"):
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
        "steps_unvalidated": unvalidated_count,
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
        "stale_ref",
        "stale_ref_unrecovered",
    )
    if reason.startswith(regression_prefixes):
        return "regressed"

    return "inconclusive"


def _resolve_url(base: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def _normalize_validation_condition(raw: Any) -> Optional[ValidationCondition]:
    """Return a validated ValidationCondition dict, or None when invalid/missing."""
    if not isinstance(raw, dict):
        return None
    cond_type = str(raw.get("type") or "").strip()
    cond_value = str(raw.get("value") or "").strip()
    if cond_type not in {"url_match", "text_present", "element_present"}:
        return None
    if not cond_value:
        return None
    return ValidationCondition(type=cond_type, value=cond_value)


def _extract_validation_condition(step: Dict[str, Any]) -> Optional[ValidationCondition]:
    """Read structured click validation metadata from a step dict."""
    return _normalize_validation_condition(
        step.get("success_condition") or step.get("validation_condition")
    )


def _configure_ab_session(cli: Any, capture_settings: CaptureSettings) -> Dict[str, Any]:
    """Apply deterministic Agent Browser session settings before capturing UI."""
    cli.set_viewport(
        capture_settings.viewport_width,
        capture_settings.viewport_height,
    )
    return {
        "viewport_width": int(capture_settings.viewport_width),
        "viewport_height": int(capture_settings.viewport_height),
    }


def _settle_ab_page(
    cli: Any,
    *,
    validation_condition: Optional[ValidationCondition] = None,
) -> ABPageSettleResult:
    """
    Prefer deterministic page readiness checks over blind sleeps.

    `networkidle` is the preferred gate. `domcontentloaded` and validation waits
    are best-effort fallback signals because modern apps may keep background
    activity alive. This function never raises.
    """
    settle: ABPageSettleResult = {
        "domcontentloaded": False,
        "networkidle": False,
        "validation_wait": "",
        "fallback_wait_used": False,
    }

    try:
        cli.wait_for_load_state(
            "networkidle",
            timeout=AB_NETWORKIDLE_TIMEOUT_S,
        )
        settle["networkidle"] = True
    except Exception:
        pass

    try:
        cli.wait_for_load_state(
            "domcontentloaded",
            timeout=AB_DOMCONTENTLOADED_TIMEOUT_S,
        )
        settle["domcontentloaded"] = True
    except Exception:
        pass

    if validation_condition is not None:
        cond_type = validation_condition["type"]
        cond_value = validation_condition["value"]
        try:
            if cond_type == "text_present":
                cli.wait_for_text(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
                settle["validation_wait"] = "text_present"
            elif cond_type == "url_match":
                cli.wait_for_url(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
                settle["validation_wait"] = "url_match"
        except Exception:
            pass

    settle["fallback_wait_used"] = not settle["networkidle"]

    return settle


def _resolve_ab_ref_with_commands(
    cli: Any,
    *,
    intent: str,
    selector: str = "",
) -> str:
    selector_norm = (selector or "").strip()
    testid_match = re.search(r"""\[data-testid=['"]([^'"]+)['"]\]""", selector_norm)
    if testid_match:
        found_ref = cli.find_testid_ref(testid_match.group(1))
        if found_ref:
            return found_ref

    if not intent:
        return ""

    for role in ("button", "link"):
        found_ref = cli.find_role_ref(role, intent)
        if found_ref:
            return found_ref

    found_ref = cli.find_label_ref(intent)
    if found_ref:
        return found_ref

    return cli.find_ref(intent)


def _scroll_to_find(
    cli: Any,
    *,
    intent: str,
    selector: str = "",
) -> str:
    for _ in range(AB_SCROLL_RETRY_COUNT):
        found_ref = _resolve_ab_ref_with_commands(
            cli,
            intent=intent,
            selector=selector,
        )
        if found_ref:
            try:
                cli.scroll_into_view(found_ref)
            except Exception:
                pass
            return found_ref
        try:
            cli.scroll("down", AB_SCROLL_RETRY_PX)
        except Exception:
            break
        try:
            cli.wait_for_load_state(
                "networkidle",
                timeout=AB_SCROLL_SETTLE_TIMEOUT_S,
            )
        except Exception:
            try:
                cli.wait_for_load_state(
                    "domcontentloaded",
                    timeout=AB_SCROLL_SETTLE_TIMEOUT_S,
                )
            except Exception:
                pass
    return ""


def _resolve_ab_click_target(
    cli: Any,
    *,
    intent: str,
    snapshot: Dict[str, Any],
    mode: str,
    allow_scroll_retry: bool,
    selector: str = "",
) -> ABTargetResolution:
    """
    Resolve a click target using command-first lookup with deterministic
    snapshot selection as a supporting path.
    """
    from app.browser.ref_selector import select_ref

    resolved: ABTargetResolution = {
        "chosen_ref": "",
        "selection_reason": "no_match",
        "selection_source": "deterministic",
        "scroll_retry_used": False,
        "should_retry": False,
    }

    selector_norm = (selector or "").strip()
    testid_match = re.search(r"""\[data-testid=['"]([^'"]+)['"]\]""", selector_norm)
    if testid_match:
        found_ref = cli.find_testid_ref(testid_match.group(1))
        if found_ref:
            resolved.update({
                "chosen_ref": found_ref,
                "selection_reason": "ab_find_testid",
                "selection_source": "semantic_testid",
            })
            return resolved

    if intent:
        for role in ("button", "link"):
            found_ref = cli.find_role_ref(role, intent)
            if found_ref:
                resolved.update({
                    "chosen_ref": found_ref,
                    "selection_reason": f"ab_find_role_{role}",
                    "selection_source": "semantic_role",
                })
                return resolved

    sel = select_ref(intent, snapshot, mode=mode)                          
    resolved.update({
        "chosen_ref": sel["chosen_ref"],
        "selection_reason": sel["selection_reason"],
        "selection_source": "deterministic",
    })
    if sel["chosen_ref"]:
        return resolved

    if str(sel.get("selection_reason") or "") == "ambiguous":
        resolved["selection_source"] = "ambiguous"
        return resolved

    found_ref = cli.find_label_ref(intent)
    if found_ref:
        resolved.update({
            "chosen_ref": found_ref,
            "selection_reason": "ab_find_label",
            "selection_source": "semantic_label",
        })
        return resolved

    found_ref = cli.find_ref(intent)
    if found_ref:
        resolved.update({
            "chosen_ref": found_ref,
            "selection_reason": "ab_find",
            "selection_source": "semantic_find",
        })
        return resolved

    if allow_scroll_retry:
        resolved["scroll_retry_used"] = True
        resolved["should_retry"] = True
    return resolved


def _ensure_ab_target_actionable(cli: Any, click_target: str) -> ABActionabilityResult:
    """
    Bring the target into view, then verify it is both visible and enabled
    before clicking.
    """
    try:
        cli.scroll_into_view(click_target)
    except Exception:
        pass
    _settle_ab_page(cli)
    visible = cli.is_visible(click_target)
    enabled = cli.is_enabled(click_target)
    return {
        "target_visible": visible,
        "target_enabled": enabled,
    }


def _capture_ab_screenshot(
    cli: Any,
    *,
    screenshot_dir: Path,
    shot_idx: int,
    step_result: Dict[str, Any],
    step_result_key: str,
    attempt_screenshots: List[Path],
) -> int:
    path = screenshot_dir / f"shot{shot_idx}.png"
    try:
        cli.screenshot(path)
        attempt_screenshots.append(path)
        step_result[step_result_key] = str(path)
        return shot_idx + 1
    except Exception:
        return shot_idx


def _run_ab_click_attempt(
    *,
    cli: Any,
    step: Dict[str, Any],
    step_result: Dict[str, Any],
    screenshot_dir: Path,
    shot_idx: int,
    attempt: int,
    click_attempt_limit: int,
    mode: str,
    extract_snapshot: Any,
) -> Dict[str, Any]:
    attempt_screenshots: List[Path] = []
    result: Dict[str, Any] = {
        "attempt_screenshots": attempt_screenshots,
        "retry": False,
        "retry_reason": "",
        "outcome": "click_failed",
        "error": "",
        "stale_ref_error": False,
        "snap_before": None,
        "snap_after": None,
        "validation": None,
        "action_key": "",
        "state_changed": False,
        "click_target": "",
        "shot_idx": shot_idx,
    }

    step_result["pre_snapshot_settle"] = _settle_ab_page(cli)
    if step_result["pre_snapshot_settle"].get("fallback_wait_used"):
        _log(
            "ab_runner.page_settle_fallback",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "phase": "pre_snapshot",
                "intent": str(step_result.get("intent") or ""),
            },
        )
    snap = extract_snapshot(save_raw=(attempt == 1))
    result["snap_before"] = snap
    step_result["raw_snapshot_path"] = snap.get("raw_snapshot_path", "")

    intent = str(step_result.get("intent") or "")
    resolution = _resolve_ab_click_target(
        cli,
        intent=intent,
        snapshot=snap,
        mode=mode,
        allow_scroll_retry=(attempt < click_attempt_limit),
        selector=str(step.get("selector") or ""),
    )
    step_result.update({
        "chosen_ref": resolution["chosen_ref"],
        "selection_reason": resolution["selection_reason"],
        "selection_source": resolution["selection_source"],
        "scroll_retry_used": resolution["scroll_retry_used"],
    })

    if resolution["selection_source"] == "semantic_find":
        _log(
            "ab_runner.ab_find_recovered",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "intent": intent,
                "ref": resolution["chosen_ref"],
            },
        )

    if resolution["should_retry"]:
        result["retry"] = True
        result["retry_reason"] = "scroll_retry"
        return result

    if not resolution["chosen_ref"]:
        result["error"] = f"selection_failed:{resolution['selection_reason']}"
        return result

    click_target = resolution["chosen_ref"]
    result["click_target"] = click_target
    result["action_key"] = f"{snap['current_url']}:{click_target}"

    actionability = _ensure_ab_target_actionable(cli, click_target)
    step_result.update(actionability)
    if not actionability["target_visible"] or not actionability["target_enabled"]:
        result["error"] = (
            "target_not_actionable:"
            f"visible={actionability['target_visible']}:"
            f"enabled={actionability['target_enabled']}"
        )
        _log("ab_runner.target_not_actionable", {
            "index": step_result["index"],
            "attempt": attempt,
            "ref": click_target,
            "visible": actionability["target_visible"],
            "enabled": actionability["target_enabled"],
        })
        return result

    shot_idx = _capture_ab_screenshot(
        cli,
        screenshot_dir=screenshot_dir,
        shot_idx=shot_idx,
        step_result=step_result,
        step_result_key="before_screenshot",
        attempt_screenshots=attempt_screenshots,
    )
    result["shot_idx"] = shot_idx

    try:
        cli.click(click_target)
    except Exception as exc:
        error_message = str(exc)
        result["error"] = error_message
        result["stale_ref_error"] = _is_stale_ref_error(error_message, click_target)
        return result

    validation_condition = _extract_validation_condition(step)
    step_result["post_click_settle"] = _settle_ab_page(
        cli,
        validation_condition=validation_condition,
    )
    if step_result["post_click_settle"].get("fallback_wait_used"):
        _log(
            "ab_runner.page_settle_fallback",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "phase": "post_click",
                "intent": str(step_result.get("intent") or ""),
            },
        )

    shot_idx = _capture_ab_screenshot(
        cli,
        screenshot_dir=screenshot_dir,
        shot_idx=shot_idx,
        step_result=step_result,
        step_result_key="after_screenshot",
        attempt_screenshots=attempt_screenshots,
    )
    result["shot_idx"] = shot_idx

    snap_after = extract_snapshot(save_raw=False)
    result["snap_after"] = snap_after
    url_before = snap["current_url"]
    url_after = snap_after["current_url"]
    state_changed = _detect_state_change(
        url_before,
        url_after,
        snap["snapshot_text"],
        snap_after["snapshot_text"],
    )
    result["state_changed"] = state_changed
    step_result.update({
        "url_before": url_before,
        "url_after": url_after,
        "state_changed": state_changed,
    })

    validation = _evaluate_click_validation(
        step=step,
        snap_before=snap,
        snap_after=snap_after,
    )
    result["validation"] = validation
    ui_diff = cli.compare_snapshots(snap, snap_after)
    condition = validation["condition"]
    step_result.update({
        "validation_result": validation,
        "validation_type": condition["type"] if condition else "",
        "validation_value": condition["value"] if condition else "",
        "validation_source": validation["source"],
        "validation_passed": validation["passed"],
        "validation_actual": validation["actual"],
        "ui_diff": ui_diff,
        "ui_change_summary": ui_diff.get("summary", ""),
    })

    if condition is None:
        result["outcome"] = "unvalidated"
        return result
    if validation["passed"]:
        result["outcome"] = "success"
        return result

    result["outcome"] = "wrong_click"
    step_result["validation_failure_reason"] = validation["failure_reason"]
    return result


def _ab_snapshot_to_dom_context(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    current_url = str(snapshot.get("current_url") or "")
    current_path = urlparse(current_url).path or "/"
    buttons: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

    for element in snapshot.get("interactive_elements") or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "").strip().lower()
        name = str(element.get("name") or "").strip()
        if not name:
            continue
        if role == "link":
            links.append({
                "text": name,
                "href": "",
                "testid": "",
                "aria": "",
                "id": "",
            })
        else:
            buttons.append({
                "text": name,
                "testid": "",
                "aria": "",
                "title": "",
                "id": "",
                "selector": "",
            })

    return {
        "current_path": current_path,
        "routes": [current_path, "/"],
        "buttons": buttons,
        "links": links,
        "inputs": [],
        "data_testids": [],
    }


def _next_click_intent(steps: List[Dict[str, Any]], start_index: int) -> str:
    from app.browser.ref_selector import derive_intent

    for next_step in steps[start_index + 1:]:
        if str(next_step.get("action") or "").strip() != "click":
            continue
        intent = derive_intent(next_step)
        if intent:
            return intent
    return ""


def _snapshot_has_intent(
    snapshot: Dict[str, Any],
    *,
    intent: str,
    mode: str,
) -> bool:
    if not intent:
        return False
    from app.browser.ref_selector import select_ref

    selected = select_ref(intent, snapshot, mode=mode)                          
    return bool(selected.get("chosen_ref"))


def _recover_ab_prerequisite_steps(
    *,
    objective: Optional[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    step_index: int,
    current_step: Dict[str, Any],
    current_intent: str,
    snap_after: Dict[str, Any],
    mode: str,
    trigger_reason: str = "state_unchanged",
    current_step_completed_unvalidated: bool = False,
    state_changed: Optional[bool] = None,
) -> Dict[str, Any]:
    if not objective or current_step.get("_ab_recovery_attempted"):
        return {"recovered": False, "attempts_used": 0}

    next_intent = _next_click_intent(steps, step_index)
    blocked_intent = (
        next_intent or current_intent
        if trigger_reason == "state_unchanged"
        else current_intent
    )
    if not blocked_intent:
        return {"recovered": False, "attempts_used": 0}
    if _snapshot_has_intent(snap_after, intent=blocked_intent, mode=mode):
        return {
            "recovered": False,
            "attempts_used": 0,
            "next_intent": next_intent,
            "blocked_intent": blocked_intent,
            "blocked_target_present": True,
        }

    regenerated, attempts = regenerate_with_feedback(
        objective=objective,
        dom_context=_ab_snapshot_to_dom_context(snap_after),
        error_context={
            "error": "prerequisite_failure",
            "trigger_reason": trigger_reason,
            "failed_step": current_step,
            "current_intent": current_intent,
            "blocked_intent": blocked_intent,
            "next_intent": next_intent,
            "current_step_completed_unvalidated": current_step_completed_unvalidated,
            "state_changed": state_changed,
            "current_url": str(snap_after.get("current_url") or ""),
        },
        max_attempts=1,
        page=None,
    )
    if not regenerated:
        return {
            "recovered": False,
            "attempts_used": len(attempts),
            "next_intent": next_intent,
            "blocked_intent": blocked_intent,
            "blocked_target_present": False,
        }

    retried_step = dict(current_step)
    retried_step["_ab_recovery_attempted"] = True
    return {
        "recovered": True,
        "attempts_used": len(attempts),
        "next_intent": next_intent,
        "blocked_intent": blocked_intent,
        "blocked_target_present": False,
        "replacement_steps": regenerated + [retried_step],
    }


def _collect_ab_failure_diagnostics(cli: Any) -> Dict[str, Any]:
    console_messages = cli.console_messages()
    page_errors = cli.page_errors()
    network_requests = cli.network_requests()
    network_error_count = 0
    for request in network_requests:
        status = request.get("status")
        error_text = str(request.get("error") or "").strip()
        if isinstance(status, int) and status >= 400:
            network_error_count += 1
        elif error_text:
            network_error_count += 1
    diagnostics = {
        "console_messages": console_messages[:20],
        "page_errors": page_errors[:20],
        "network_request_count": len(network_requests),
        "network_error_count": network_error_count,
        "network_requests_preview": network_requests[:20],
    }
    record_agent_browser_diagnostics(
        console_count=len(console_messages),
        page_error_count=len(page_errors),
        network_request_count=len(network_requests),
        network_error_count=network_error_count,
    )
    return diagnostics


def _attach_ab_failure_diagnostics(cli: Any, step_result: Dict[str, Any]) -> None:
    step_result["diagnostics"] = _collect_ab_failure_diagnostics(cli)


def _contains_ci(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def _matches_validation_condition(
    condition: ValidationCondition,
    *,
    current_url: str,
    snapshot_text: str,
    element_names: List[str],
) -> bool:
    """Evaluate one explicit success condition against one page state."""
    expected = condition["value"]
    cond_type = condition["type"]
    if cond_type == "url_match":
        return _contains_ci(current_url, expected)
    if cond_type == "text_present":
        return _contains_ci(snapshot_text, expected)
    if cond_type == "element_present":
        return any(_contains_ci(name, expected) for name in element_names)
    return False


def _element_names(snapshot: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if isinstance(element, dict):
                name = str(element.get("name") or "").strip()
                if name:
                    names.append(name)
    return names


def _describe_validation_actual(
    condition: Optional[ValidationCondition],
    snap_after: Dict[str, Any],
) -> str:
    if condition is None:
        return "no_validation_condition"

    if condition["type"] == "url_match":
        return str(snap_after.get("current_url") or "")
    if condition["type"] == "text_present":
        return str(snap_after.get("snapshot_text") or "")

    matched_names = [
        name for name in _element_names(snap_after)
        if _contains_ci(name, condition["value"])
    ]
    if matched_names:
        return ", ".join(matched_names[:5])
    all_names = _element_names(snap_after)
    return ", ".join(all_names[:5]) if all_names else "no_matching_element"


def _evaluate_click_validation(
    *,
    step: Dict[str, Any],
    snap_before: Dict[str, Any],
    snap_after: Dict[str, Any],
) -> StepValidationResult:
    """Validate the post-click page state for one click step."""
    condition = _extract_validation_condition(step)
    if condition is None:
        return StepValidationResult(
            passed=False,
            condition=None,
            actual="no_validation_condition",
            source="",
            failure_reason="",
        )

    source = str(step.get("validation_source") or "step")
    before_matches = _matches_validation_condition(
        condition,
        current_url=str(snap_before.get("current_url") or ""),
        snapshot_text=str(snap_before.get("snapshot_text") or ""),
        element_names=_element_names(snap_before),
    )
    after_matches = _matches_validation_condition(
        condition,
        current_url=str(snap_after.get("current_url") or ""),
        snapshot_text=str(snap_after.get("snapshot_text") or ""),
        element_names=_element_names(snap_after),
    )
    passed = after_matches and not before_matches
    return StepValidationResult(
        passed=passed,
        condition=condition,
        actual=_describe_validation_actual(condition, snap_after),
        source=source if source in {"step", "test_case"} else "step",
        failure_reason="" if passed else f"validation_failed:{condition['type']}:{condition['value']}",
    )


def _is_stale_ref_error(error_message: str, click_target: str) -> bool:
    """
    Best-effort stale-ref classification for Agent Browser click failures.

    Only ref-based clicks (@e1 style) can become stale in the snapshot sense.
    """
    if not click_target.startswith("@"):
        return False
    lowered = error_message.lower()
    stale_markers = (
        "stale",
        "unknown ref",
        "invalid ref",
        "could not find element",
        "element not found",
        "no such element",
    )
    return any(marker in lowered for marker in stale_markers)


def _discard_screenshots(paths: List[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _step_screenshot_paths(step_result: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    for key in ("before_screenshot", "after_screenshot"):
        raw = str(step_result.get(key) or "").strip()
        if raw:
            paths.append(Path(raw))
    return paths


def _discard_step_screenshots(step_result: Dict[str, Any]) -> None:
    _discard_screenshots(_step_screenshot_paths(step_result))
    step_result["before_screenshot"] = ""
    step_result["after_screenshot"] = ""


def _should_keep_click_screenshots(step_result: Dict[str, Any]) -> bool:
    outcome = str(step_result.get("outcome") or "")
    if outcome == "success":
        return True
    if outcome == "unvalidated" and bool(step_result.get("state_changed")):
        return True
    return False


def _terminal_match_in_snapshot(snapshot: Dict[str, Any], expected: str) -> bool:
    needle = (expected or "").strip().lower()
    if not needle:
        return False
    interactive_elements = snapshot.get("interactive_elements") or []
    context_elements = snapshot.get("context_elements") or []
    snapshot_text = str(snapshot.get("snapshot_text") or "")
    print(
        f"[terminal_check] looking for '{expected}' "
        f"in {len(interactive_elements)} interactive, "
        f"{len(context_elements)} context elements, "
        f"snapshot_text_length={len(snapshot_text)}",
        flush=True,
    )
    print(
        f"[terminal_check] snapshot_text excerpt: "
        f"{snapshot_text[:500]}",
        flush=True,
    )
    for element in interactive_elements:
        if not isinstance(element, dict):
            continue
        name = str(element.get("name") or "").strip().lower()
        if needle and needle in name:
            return True
    for element in context_elements:
        if not isinstance(element, dict):
            continue
        name = str(element.get("name") or "").strip().lower()
        if needle and needle in name:
            return True
    snap_text = snapshot_text.lower()
    return bool(needle and needle in snap_text)


def _assert_ab_terminal_condition(
    cli: Any,
    *,
    condition: Dict[str, Any],
    expected_element: str,
    extract_snapshot: Any,
) -> Dict[str, Any]:
    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    expected = expected_element or cond_value
    if not cond_type and expected_element:
        cond_type = "element_present"
        cond_value = expected_element
    result: Dict[str, Any] = {
        "found": True,
        "source": "none",
        "actual": "",
    }

    if cond_type == "text_present" and cond_value:
        try:
            cli.wait_for_text(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
            result["source"] = "wait_for_text"
            result["actual"] = cond_value
            return result
        except Exception:
            pass

    if cond_type == "url_match" and cond_value:
        try:
            cli.wait_for_url(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
            result["source"] = "wait_for_url"
            result["actual"] = cli.get_url()
            return result
        except Exception:
            pass

    if cond_type == "element_present" and expected:
        testid_ref = cli.find_testid_ref(expected)
        if testid_ref:
            result["source"] = "find_testid"
            result["actual"] = testid_ref
            return result

        for selector in (f"[data-testid='{expected}']", f"#{expected}"):
            count = cli.get_count(selector)
            if count > 0:
                result["source"] = "get_count"
                result["actual"] = selector
                return result

        semantic_ref = cli.find_ref(expected)
        if semantic_ref:
            result["source"] = "semantic_find"
            result["actual"] = semantic_ref
            return result

    if expected:
        print(
            f"[terminal_check] browser-native check failed for "
            f"type={cond_type or 'unknown'} value={cond_value or expected!r} "
            f"— falling back to snapshot text search",
            flush=True,
        )
        terminal_snapshot = extract_snapshot(save_raw=False)
        found = _terminal_match_in_snapshot(terminal_snapshot, expected)
        result["found"] = found
        result["source"] = "snapshot_fallback"
        result["actual"] = expected if found else ""
        if found:
            print(
                "[terminal_check] matched via snapshot text fallback — "
                "consider adding explicit testid to this terminal condition",
                flush=True,
            )
        return result

    return result


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
        text = (step.get("label") or step.get("text") or "").strip()
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
    total_retries = 0                                                  

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": cs.viewport_width, "height": cs.viewport_height})
        page.goto(preview_url, wait_until="domcontentloaded", timeout=15000)
        wait_stable_after_navigation(page)
        dom_ctx = extract_dom_context(page)

        i = 0
        while i < len(queue):
            step = queue[i]
            _step_t0 = time.monotonic()                                  

            ok, reason = validate_step_against_dom(step, dom_ctx, page=page)
            if not ok:

                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": reason, "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                total_retries += attempts           
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
                total_retries += attempts           
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

            _step_latency_ms = int((time.monotonic() - _step_t0) * 1000)           
            results.append({"index": i, "step": step, "status": "ok", "step_latency_ms": _step_latency_ms})

            now = capture_state(page)
            nav_changed = detect_major_change(prev, now)
            if nav_changed:
                wait_stable_after_navigation(page)
                dom_ctx = extract_dom_context(page)

                remaining_objective = {**objective, "remaining_from_index": i + 1}
                regenerated, attempts = regenerate_with_feedback(
                    objective=remaining_objective,
                    dom_context=dom_ctx,
                    error_context={"event": "navigation_boundary", "at_index": i},
                    max_attempts=max_retries_per_failure,
                    page=page,
                )
                total_retries += attempts           
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
    objective: Optional[Dict[str, Any]] = None,
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
                → click(ref) → settle via browser load checks
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

    Validation outcomes:
        success         — explicit post-click validation passed.
        wrong_click     — click ran without error, but the explicit post-click
                          validation condition was not satisfied after retries.
                          This is a step failure.

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
        objective          — bounded runtime recovery context for conditional UI.
        max_steps_per_run  — hard upper bound on steps processed.
        max_retries_per_step — retry attempts per click step.
        mode               — ExperimentMode: "deterministic" (Mode A, default)
                             or "deterministic_plus_llm" (Mode B).
        capture_settings   — CaptureSettings for viewport etc.; uses defaults
                             when not provided.
        session            — agent-browser session name; use a unique value
                             per concurrent job for isolation.
    """


    from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError
    from app.browser.ref_selector import derive_intent
    from app.context.dom_extractor import extract_ab_context

    cs = capture_settings or CaptureSettings()

    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = list(initial_steps[:max_steps_per_run])
    steps_succeeded = 0
    shot_idx = 1

    last_action_key: Optional[str] = None
    _total_retries = 0                                                             
    _replans_used = 0

    _log("ab_runner.start", {
        "preview_url": preview_url,
        "total_steps": len(initial_steps),
        "mode": mode,
        "session": session,
    })

    cli = AgentBrowserCLI(session=session)

    try:
        cli.open(preview_url)
        session_config = _configure_ab_session(cli, cs)
        _settle_ab_page(cli)

        step_idx = 0
        while step_idx < len(queue) and step_idx < max_steps_per_run:
            step = queue[step_idx]
            action = step.get("action")
            _step_t0 = time.monotonic()                                  
            step_result: Dict[str, Any] = {
                "index": step_idx,
                "step": step,
                "status": "failed",
                "outcome": "pending",
                "backend": "agent_browser_cli",
                "mode": mode,
            }




            if action == "goto":
                url = step.get("url") or "/"
                full_url = _resolve_url(preview_url, url)
                try:
                    cli.open(full_url)
                    step_result["page_settle"] = _settle_ab_page(cli)
                    step_result["session_viewport"] = session_config
                    step_result.update({"status": "ok", "outcome": "success"})
                    steps_succeeded += 1
                    last_action_key = None                                      
                except AgentBrowserError as exc:
                    step_result.update({"outcome": "click_failed", "error": str(exc)})
                    _attach_ab_failure_diagnostics(cli, step_result)
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
                step_idx += 1
                continue




            if action == "screenshot":
                path = screenshot_dir / f"shot{shot_idx}.png"
                try:
                    cli.screenshot(path)
                    shot_idx += 1
                except AgentBrowserError:
                    pass                                                           
                step_result.update({"status": "ok", "outcome": "success"})
                steps_succeeded += 1
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                results.append(step_result)
                step_idx += 1
                continue




            if action == "assert_terminal":
                condition = step.get("condition") or {}
                expected_element = (
                    step.get("expected_element")
                    or (condition.get("value") if isinstance(condition, dict) else "")
                    or ""
                )
                found = True
                terminal_source = ""
                terminal_actual = ""
                if expected_element:
                    try:
                        terminal_result = _assert_ab_terminal_condition(
                            cli,
                            condition=condition if isinstance(condition, dict) else {},
                            expected_element=str(expected_element),
                            extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
                        )
                        found = bool(terminal_result.get("found"))
                        terminal_source = str(terminal_result.get("source") or "")
                        terminal_actual = str(terminal_result.get("actual") or "")
                    except AgentBrowserError as exc:
                        step_result.update(
                            {
                                "status": "failed",
                                "outcome": "click_failed",
                                "error": f"snapshot_failed:{exc}",
                            }
                        )
                        _attach_ab_failure_diagnostics(cli, step_result)
                        step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                        results.append(step_result)
                        return {
                            "success": False,
                            "final_outcome": _classify_final_outcome(
                                success=False,
                                failure_reason=f"snapshot_failed:{exc}",
                            ),
                            "steps_succeeded": steps_succeeded,
                            "steps_failed": 1,
                            "failure_reason": f"snapshot_failed:{exc}",
                            "results": results,
                            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                        }

                step_result["terminal_condition_reached"] = found
                step_result["terminal_validation_source"] = terminal_source
                step_result["terminal_validation_actual"] = terminal_actual
                step_result["outcome"] = "success" if found else "terminal_not_reached"
                if not found:
                    print(
                        f"[step_runner] terminal condition not reached: "
                        f"expected={expected_element!r}",
                        flush=True,
                    )
                step_result["status"] = "ok" if found else "failed"
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                if found:
                    steps_succeeded += 1
                results.append(step_result)
                if not found:
                    if results[:-1]:
                        previous_result = results[-2]
                        if (
                            str(previous_result.get("step", {}).get("action") or "") == "click"
                            and not _should_keep_click_screenshots(previous_result)
                        ):
                            _discard_step_screenshots(previous_result)
                    _attach_ab_failure_diagnostics(cli, step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="terminal_not_reached",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": "terminal_not_reached",
                        "results": results,
                            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                        }
                step_idx += 1
                continue




            if action == "click":
                intent = derive_intent(step)
                if not intent:
                    step_result.update({
                        "outcome": "click_failed",
                        "status": "failed",
                        "error": "no_intent",
                    })
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    _log("ab_runner.no_intent", {"index": step_idx, "step": step})
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="click_failed:no_intent",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": "click_failed:no_intent",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                step_result["intent"] = intent
                validation_condition = _extract_validation_condition(step)
                if validation_condition is not None:
                    step_result["validation_condition"] = validation_condition

                outcome = "click_failed"
                attempts_used = 0
                stale_ref_retry_used = False
                click_attempt_limit = min(max_retries_per_step, MAX_RETRIES_PER_STEP)
                step_result["stale_ref_count"] = 0

                runtime_recovered = False
                previous_step_unvalidated = bool(
                    results and str(results[-1].get("outcome") or "") == "unvalidated"
                )

                for attempt in range(1, click_attempt_limit + 1):
                    attempts_used = attempt
                    _log("ab_runner.click_attempt", {
                        "index": step_idx, "attempt": attempt, "intent": intent,
                    })

                    try:
                        attempt_result = _run_ab_click_attempt(
                            cli=cli,
                            step=step,
                            step_result=step_result,
                            screenshot_dir=screenshot_dir,
                            shot_idx=shot_idx,
                            attempt=attempt,
                            click_attempt_limit=click_attempt_limit,
                            mode=mode,
                            extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
                        )
                    except AgentBrowserError as exc:
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = f"snapshot_failed:{exc}"
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.snapshot_failed", {"index": step_idx, "attempt": attempt})
                        break

                    attempt_screenshots = list(attempt_result["attempt_screenshots"])
                    shot_idx = int(attempt_result["shot_idx"])

                    if attempt_result["retry"]:
                        found_ref = _scroll_to_find(
                            cli,
                            intent=intent,
                            selector=str(step.get("selector") or ""),
                        )
                        if found_ref:
                            _log(
                                "ab_runner.no_match_scroll_retry",
                                {
                                    "index": step_idx,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "ref": found_ref,
                                },
                            )
                            continue
                        attempt_result["error"] = "selection_failed:no_match"
                        step_result["error"] = "selection_failed:no_match"
                        outcome = "click_failed"

                    if attempt_result["stale_ref_error"]:
                        click_target = str(attempt_result["click_target"] or "")
                        error_message = str(attempt_result["error"])
                        step_result["error"] = error_message
                        step_result["stale_ref_count"] = int(step_result.get("stale_ref_count", 0)) + 1
                        _discard_screenshots(attempt_screenshots)
                        _log("ab_runner.stale_ref", {
                            "index": step_idx,
                            "attempt": attempt,
                            "ref": click_target,
                            "error": error_message,
                            "stale_ref_count": step_result["stale_ref_count"],
                        })
                        if stale_ref_retry_used or attempt >= click_attempt_limit:
                            _attach_ab_failure_diagnostics(cli, step_result)
                            outcome = "stale_ref_unrecovered"
                            break
                        stale_ref_retry_used = True
                        outcome = "stale_ref"
                        continue

                    if attempt_result["error"]:
                        error_message = str(attempt_result["error"])
                        if (
                            error_message == "selection_failed:no_match"
                            and _replans_used < MAX_AB_REPLANS_PER_RUN
                        ):
                            recovery = _recover_ab_prerequisite_steps(
                                objective=objective,
                                steps=queue,
                                step_index=step_idx,
                                current_step=step,
                                current_intent=intent,
                                snap_after=attempt_result["snap_before"] or {},
                                mode=mode,
                                trigger_reason=(
                                    "selection_failed_after_unvalidated"
                                    if previous_step_unvalidated
                                    else "selection_failed_current_step"
                                ),
                                current_step_completed_unvalidated=previous_step_unvalidated,
                                state_changed=None,
                            )
                            _total_retries += int(recovery.get("attempts_used", 0))
                            if recovery.get("recovered"):
                                _replans_used += 1
                                _discard_screenshots(attempt_screenshots)
                                step_result["runtime_recovery"] = {
                                    "triggered": True,
                                    "trigger_reason": (
                                        "selection_failed_after_unvalidated"
                                        if previous_step_unvalidated
                                        else "selection_failed_current_step"
                                    ),
                                    "blocked_intent": recovery.get("blocked_intent", ""),
                                    "next_intent": recovery.get("next_intent", ""),
                                }
                                queue[step_idx:step_idx + 1] = list(
                                    recovery.get("replacement_steps") or []
                                )
                                runtime_recovered = True
                                _log("ab_runner.prerequisite_recovered", {
                                    "index": step_idx,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "trigger_reason": step_result["runtime_recovery"]["trigger_reason"],
                                    "blocked_intent": recovery.get("blocked_intent", ""),
                                    "next_intent": recovery.get("next_intent", ""),
                                })
                                break
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = error_message
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.selection_failed", {
                            "index": step_idx,
                            "attempt": attempt,
                            "reason": step_result["error"],
                            "intent": intent,
                        })
                        break

                    click_target = str(attempt_result["click_target"] or "")
                    action_key = str(attempt_result["action_key"] or "")
                    if action_key == last_action_key:
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = "repeated_action"
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.repeated_action", {
                            "index": step_idx,
                            "ref": click_target,
                            "url": str(step_result.get("url_before") or ""),
                        })
                        break

                    snap_after = attempt_result["snap_after"]
                    state_changed = bool(attempt_result["state_changed"])
                    validation = attempt_result["validation"]
                    condition = validation["condition"] if validation else None

                    if (
                        not state_changed
                        and _replans_used < MAX_AB_REPLANS_PER_RUN
                        and (
                            validation_condition is not None
                            or bool(_next_click_intent(queue, step_idx))
                        )
                    ):
                        recovery = _recover_ab_prerequisite_steps(
                            objective=objective,
                            steps=queue,
                            step_index=step_idx,
                            current_step=step,
                            current_intent=intent,
                            snap_after=snap_after,
                            mode=mode,
                            trigger_reason="state_unchanged",
                            current_step_completed_unvalidated=False,
                            state_changed=state_changed,
                        )
                        _total_retries += int(recovery.get("attempts_used", 0))
                        if recovery.get("recovered"):
                            _replans_used += 1
                            _discard_screenshots(attempt_screenshots)
                            step_result["runtime_recovery"] = {
                                "triggered": True,
                                "trigger_reason": "state_unchanged",
                                "blocked_intent": recovery.get("blocked_intent", ""),
                                "next_intent": recovery.get("next_intent", ""),
                            }
                            queue[step_idx:step_idx + 1] = list(
                                recovery.get("replacement_steps") or []
                            )
                            runtime_recovered = True
                            _log("ab_runner.prerequisite_recovered", {
                                "index": step_idx,
                                "attempt": attempt,
                                "intent": intent,
                                "next_intent": recovery.get("next_intent", ""),
                            })
                            break

                    if condition is None:
                        outcome = str(attempt_result["outcome"])
                        last_action_key = action_key
                        _log("ab_runner.unvalidated", {
                            "index": step_idx,
                            "attempt": attempt,
                            "ref": click_target,
                        })
                        break

                    if validation and validation["passed"]:
                        outcome = str(attempt_result["outcome"])
                        last_action_key = action_key
                        _log("ab_runner.validation_passed", {
                            "index": step_idx, "attempt": attempt,
                            "url_before": step_result.get("url_before", ""),
                            "url_after": step_result.get("url_after", ""),
                            "validation_type": condition["type"],
                            "validation_source": validation["source"],
                        })
                        break
                    outcome = str(attempt_result["outcome"])
                    _discard_screenshots(attempt_screenshots)
                    _log("ab_runner.wrong_click", {
                        "index": step_idx, "attempt": attempt,
                        "ref": click_target,
                        "intent": intent,
                        "validation_type": condition["type"],
                        "validation_failure_reason": validation["failure_reason"] if validation else "",
                    })
                    break


                if runtime_recovered:
                    continue
                _total_retries += max(attempts_used - 1, 0)
                step_result["outcome"] = outcome
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)


                _FATAL_OUTCOMES = frozenset({
                    "click_failed",
                    "stale_ref",
                    "stale_ref_unrecovered",
                    "wrong_click",
                })
                if outcome in _FATAL_OUTCOMES:
                    step_result["status"] = "failed"
                    if not _should_keep_click_screenshots(step_result):
                        _discard_step_screenshots(step_result)
                    if "diagnostics" not in step_result:
                        _attach_ab_failure_diagnostics(cli, step_result)
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

                step_result["status"] = "ok"
                if not _should_keep_click_screenshots(step_result):
                    _discard_step_screenshots(step_result)
                steps_succeeded += 1
                results.append(step_result)
                step_idx += 1
                continue




            step_result.update({"outcome": "click_failed", "status": "failed", "error": f"unknown_action:{action}"})
            _attach_ab_failure_diagnostics(cli, step_result)
            step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
            _log("ab_runner.unknown_action", {"index": step_idx, "action": action})
            results.append(step_result)
            return {
                "success": False,
                "final_outcome": _classify_final_outcome(
                    success=False,
                    failure_reason=f"click_failed:unknown_action:{action}",
                ),
                "steps_succeeded": steps_succeeded,
                "steps_failed": 1,
                "failure_reason": f"click_failed:unknown_action:{action}",
                "results": results,
                "metrics": _build_metrics(results, len(initial_steps), _total_retries),
            }


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
