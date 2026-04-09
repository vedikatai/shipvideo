from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from observability import pipeline_step
from app.config_types import load_capture_settings
from app.dom_schema import SuccessCondition
from app.execution.step_runner import run_ab_stepwise, run_stepwise

BASE_APP_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_STEPS: List[Dict[str, Any]] = [
    {"action": "screenshot"},
    {"action": "screenshot"},
]

MAX_STEP_RETRIES = 3






BrowserBackend = Literal["playwright", "agent_browser_cli"]



def _resolve_browser_backend() -> BrowserBackend:
    raw = os.getenv("BROWSER_BACKEND", "").strip().lower()
    if raw == "playwright":
        return "playwright"
    return "agent_browser_cli"


BROWSER_BACKEND: BrowserBackend = _resolve_browser_backend()



_EXPERIMENT_MODE: str = os.getenv("EXPERIMENT_MODE", "deterministic").strip() or "deterministic"


_DEFAULT_BACKEND: BrowserBackend = "agent_browser_cli"


def _normalize_success_condition(raw: Any) -> Optional[SuccessCondition]:
    if not isinstance(raw, dict):
        return None
    cond_type = str(raw.get("type") or "").strip()
    cond_value = str(raw.get("value") or "").strip()
    if cond_type not in {"url_match", "text_present", "element_present"}:
        return None
    if not cond_value:
        return None
    return SuccessCondition(type=cond_type, value=cond_value)


def _attach_test_case_success_conditions(
    steps: List[Dict[str, Any]],
    test_case_id: str,
) -> List[Dict[str, Any]]:
    cloned_steps = [dict(step) for step in steps]
    for step in cloned_steps:
        explicit = (
            _normalize_success_condition(step.get("validation_condition"))
            or _normalize_success_condition(step.get("success_condition"))
        )
        if explicit is not None:
            step["validation_condition"] = explicit
            step.setdefault("success_condition", explicit)
    if not test_case_id:
        return cloned_steps

    try:
        from app.browser.experiment_logger import load_test_suite

        test_case = next(
            (tc for tc in load_test_suite() if tc.get("id") == test_case_id),
            None,
        )
    except Exception:
        test_case = None

    if not test_case:
        return cloned_steps

    test_case_steps = list(test_case.get("steps") or [])
    fallback_condition = _normalize_success_condition(test_case.get("success_condition"))
    click_indexes = [
        idx for idx, step in enumerate(cloned_steps)
        if (step.get("action") or "").strip() == "click"
    ]
    last_click_index = click_indexes[-1] if click_indexes else -1

    for idx, step in enumerate(cloned_steps):
        if (step.get("action") or "").strip() != "click":
            continue
        if _normalize_success_condition(step.get("success_condition")):
            continue

        inherited = None
        if idx < len(test_case_steps):
            inherited = _normalize_success_condition(test_case_steps[idx].get("success_condition"))
        if inherited is None and idx == last_click_index:
            inherited = fallback_condition
        if inherited is not None:
            step["validation_condition"] = inherited
            step["success_condition"] = inherited
            step.setdefault("validation_source", "test_case")

    return cloned_steps


def _lookup_benchmark_result(
    experiment_summary: Dict[str, Any],
    *,
    mode: str,
    test_case_id: str,
) -> Dict[str, Any]:
    for mode_summary in experiment_summary.get("mode_summaries") or []:
        if str(mode_summary.get("mode") or "") != mode:
            continue
        for tc in mode_summary.get("test_case_results") or []:
            if str(tc.get("test_case_id") or "") == test_case_id:
                return {
                    "benchmark_outcome": tc.get("decision_outcome", "inconclusive"),
                    "benchmark_has_paired_baseline": bool(tc.get("has_paired_baseline", False)),
                }
    return {
        "benchmark_outcome": "inconclusive",
        "benchmark_has_paired_baseline": False,
    }


def _contains_ci(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def _collect_target_markers(generation_context: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(generation_context, dict):
        return []
    markers: List[str] = []
    seen: set[str] = set()

    for raw in generation_context.get("changed_testids") or []:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            markers.append(value)

    contract = generation_context.get("contract")
    if contract is not None:
        for target in getattr(contract, "targets", []) or []:
            value = str(getattr(target, "label", "") or "").strip()
            if value and value not in seen:
                seen.add(value)
                markers.append(value)
        terminal = getattr(contract, "terminal", None)
        terminal_value = str(getattr(terminal, "value", "") or "").strip() if terminal is not None else ""
        if terminal_value and terminal_value not in seen:
            seen.add(terminal_value)
            markers.append(terminal_value)

    extraction = generation_context.get("extraction") or {}
    if isinstance(extraction, dict):
        for raw in extraction.get("click_labels") or []:
            value = str(raw or "").strip()
            if value and value not in seen:
                seen.add(value)
                markers.append(value)
        terminal_value = str(extraction.get("terminal_testid") or "").strip()
        if terminal_value and terminal_value not in seen:
            seen.add(terminal_value)
            markers.append(terminal_value)

    return markers


def _result_path(result: Dict[str, Any]) -> str:
    url_after = str(result.get("url_after") or "").strip()
    if url_after:
        return urlparse(url_after).path or "/"
    step = result.get("step") or {}
    if str(step.get("action") or "") == "goto":
        return str(step.get("url") or "").strip() or "/"
    return ""


def _result_mentions_marker(result: Dict[str, Any], marker: str) -> bool:
    if not marker:
        return False
    values = [
        str(result.get("terminal_validation_actual") or ""),
        str(result.get("validation_actual") or ""),
        str(result.get("search_target_testid") or ""),
        str((result.get("step") or {}).get("label") or ""),
        str((result.get("step") or {}).get("expected_element") or ""),
        str((result.get("step") or {}).get("selector") or ""),
        str((result.get("step") or {}).get("url") or ""),
    ]
    return any(_contains_ci(value, marker) for value in values if value)


def _build_render_approval(
    *,
    generation_context: Optional[Dict[str, Any]],
    results: List[Dict[str, Any]],
    approved_frames: List[str],
) -> Dict[str, Any]:
    reasons: List[str] = []
    start_route = ""
    if isinstance(generation_context, dict):
        start_route = str(generation_context.get("start_route") or "").strip()
        if not start_route:
            extraction = generation_context.get("extraction") or {}
            if isinstance(extraction, dict):
                start_route = str(extraction.get("start_route") or "").strip()

    target_markers = _collect_target_markers(generation_context)
    wrong_click_detected = any(
        str(result.get("outcome") or "") == "wrong_click"
        for result in results
    )
    target_route_reached = (
        True if not start_route else any(_result_path(result) == start_route for result in results)
    )
    expected_proof_satisfied = any(
        bool(result.get("terminal_condition_reached"))
        or (
            str((result.get("step") or {}).get("action") or "") == "click"
            and bool(result.get("validation_passed"))
        )
        for result in results
    )
    expected_changed_target_shown = (
        True
        if not target_markers
        else any(
            _result_mentions_marker(result, marker)
            for marker in target_markers
            for result in results
        )
    )

    if not approved_frames:
        reasons.append("no_approved_frames")
    if wrong_click_detected:
        reasons.append("wrong_click_detected")
    if not target_route_reached:
        reasons.append("target_route_not_reached")
    if not expected_changed_target_shown:
        reasons.append("expected_changed_target_not_shown")
    if not expected_proof_satisfied:
        reasons.append("expected_proof_not_satisfied")

    return {
        "is_sendable": len(reasons) == 0,
        "reasons": reasons,
        "target_route_reached": target_route_reached,
        "expected_changed_target_shown": expected_changed_target_shown,
        "expected_proof_satisfied": expected_proof_satisfied,
        "wrong_click_detected": wrong_click_detected,
        "approved_frame_count": len(approved_frames),
        "target_markers": target_markers,
        "start_route": start_route,
    }


@pipeline_step("step_execution")
def run_capture(
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    screenshot_dir: Optional[Path] = None,
    generation_context: Optional[Dict[str, Any]] = None,
    test_case_id: str = "",
) -> Dict[str, Any]:
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
    steps = _attach_test_case_success_conditions(steps, test_case_id)
    out_dir = screenshot_dir or SCREENSHOT_DIR
    capture_settings = load_capture_settings()

    _active_mode = (
        _EXPERIMENT_MODE if BROWSER_BACKEND == "agent_browser_cli" else "playwright"
    )




    if BROWSER_BACKEND == "agent_browser_cli":
        _objective = {
            "goal": "Recover missing prerequisite interactions from current DOM",
            "generation_context": generation_context or {},
        }
        _runner_result = run_ab_stepwise(
            preview_url=preview_url,
            initial_steps=steps,
            screenshot_dir=out_dir,
            objective=_objective,
            capture_settings=capture_settings,
            mode=_EXPERIMENT_MODE,
        )
        _engine = f"agent_browser_cli:{_EXPERIMENT_MODE}"
        if _runner_result.get("success"):
            _result: Dict[str, Any] = {
                "steps_succeeded": int(_runner_result.get("steps_succeeded", 0)),
                "steps_failed": int(_runner_result.get("steps_failed", 0)),
                "failure_reason": None,
                "success": True,
                "approved_frames": _runner_result.get("approved_frames", []),
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }
        else:
            _result = {
                "steps_succeeded": int(_runner_result.get("steps_succeeded", 0)),
                "steps_failed": 1,
                "failure_reason": _runner_result.get("failure_reason") or "ab_execution_failed",
                "success": False,
                "approved_frames": _runner_result.get("approved_frames", []),
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }




    else:
        _objective = {
            "goal": "Generate reliable demo actions from current DOM only",
            "generation_context": generation_context or {},
        }
        _runner_result = run_stepwise(
            preview_url=preview_url,
            initial_steps=steps,
            objective=_objective,
            screenshot_dir=out_dir,
            max_retries_per_failure=MAX_STEP_RETRIES,
            capture_settings=capture_settings,
        )
        _engine = "stepwise"
        if _runner_result.get("success"):
            _result = {
                "steps_succeeded": int(_runner_result.get("steps_succeeded", 0)),
                "steps_failed": int(_runner_result.get("steps_failed", 0)),
                "failure_reason": None,
                "success": True,
                "approved_frames": _runner_result.get("approved_frames", []),
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }
        else:
            _result = {
                "steps_succeeded": 0,
                "steps_failed": 1,
                "failure_reason": _runner_result.get("failure_reason") or "stepwise_execution_failed",
                "success": False,
                "approved_frames": _runner_result.get("approved_frames", []),
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }

    approved_frames = list(_runner_result.get("approved_frames") or [])
    render_approval = _build_render_approval(
        generation_context=generation_context,
        results=list(_runner_result.get("results") or []),
        approved_frames=approved_frames,
    )
    if _result.get("success") and not approved_frames:
        _result["success"] = False
        _result["steps_failed"] = max(int(_result.get("steps_failed", 0)), 1)
        _result["failure_reason"] = "no_validated_frames"
    if _result.get("success") and not render_approval.get("is_sendable", False):
        _result["success"] = False
        _result["steps_failed"] = max(int(_result.get("steps_failed", 0)), 1)
        _result["failure_reason"] = ",".join(render_approval.get("reasons") or ["render_not_sendable"])
    _result["approved_frames"] = approved_frames
    _result["render_approval"] = render_approval




    _result["backend"] = BROWSER_BACKEND
    _result["mode"] = _active_mode
    _result["test_case_id"] = test_case_id
    _result["final_outcome"] = _runner_result.get("final_outcome", "inconclusive")
    _result["benchmark_outcome"] = "inconclusive"
    _result["benchmark_has_paired_baseline"] = False
    _result["repo_decision_outcome"] = "inconclusive"
    _result["repo_recommendation"] = "inconclusive"
    _result["decision_outcome"] = "inconclusive"
    _result["promotion_allowed"] = False
    _result["default_backend"] = _DEFAULT_BACKEND


    if test_case_id:
        from app.browser.experiment_logger import ExperimentLogger, summarize_artifacts
        _logger = ExperimentLogger(
            backend=BROWSER_BACKEND,
            mode=_active_mode,
            test_case_id=test_case_id,
        )
        _logger.finish_from_runner_result(_runner_result)
        _summary = summarize_artifacts()
        _benchmark = _lookup_benchmark_result(
            _summary,
            mode=_active_mode,
            test_case_id=test_case_id,
        )
        _result["benchmark_outcome"] = _benchmark["benchmark_outcome"]
        _result["benchmark_has_paired_baseline"] = _benchmark["benchmark_has_paired_baseline"]
        _result["repo_decision_outcome"] = _summary["decision"]["outcome"]
        _result["repo_recommendation"] = _summary["decision"]["recommendation"]
        _result["decision_outcome"] = _summary["decision"]["outcome"]
        _result["promotion_allowed"] = bool(_summary["decision"]["promotion_allowed"])

    return _result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError(
                "PREVIEW_URL environment variable or command line argument required"
            )
    run_capture(preview_url=preview_url)
