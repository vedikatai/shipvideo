"""
Step execution: entry point that runs capture steps against a preview URL.

Delegates all step-by-step execution, navigation detection, and retry logic
to app.execution.step_runner.

Backend switch (Phase 3 / Phase 5):
    Default capture backend is Agent Browser CLI (run_ab_stepwise). Set
    BROWSER_BACKEND=playwright to use the legacy Playwright stepwise runner.

    BROWSER_BACKEND=agent_browser_cli  — default when unset
    BROWSER_BACKEND=playwright          — opt-in legacy Playwright stepwise

    Optionally set EXPERIMENT_MODE to control the ref-selection mode:
    EXPERIMENT_MODE=deterministic           — Mode A (default, baseline)
    EXPERIMENT_MODE=deterministic_plus_llm  — Mode B (LLM fallback scaffold)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

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

# ---------------------------------------------------------------------------
# Phase 3 — Backend switch
# ---------------------------------------------------------------------------

#: Valid values for BROWSER_BACKEND.
BrowserBackend = Literal["playwright", "agent_browser_cli"]

#: Active backend for this process. Defaults to Agent Browser CLI; set
#: BROWSER_BACKEND=playwright to use Playwright stepwise instead.
def _resolve_browser_backend() -> BrowserBackend:
    raw = os.getenv("BROWSER_BACKEND", "").strip().lower()
    if raw == "playwright":
        return "playwright"
    return "agent_browser_cli"


BROWSER_BACKEND: BrowserBackend = _resolve_browser_backend()

#: Experiment mode used when BROWSER_BACKEND=agent_browser_cli.
#: Reads EXPERIMENT_MODE env var; defaults to "deterministic" (Mode A).
_EXPERIMENT_MODE: str = os.getenv("EXPERIMENT_MODE", "deterministic").strip() or "deterministic"

#: Declared default for telemetry / UI (Agent Browser is the default capture path).
_DEFAULT_BACKEND: BrowserBackend = "agent_browser_cli"


def _normalize_success_condition(raw: Any) -> Optional[SuccessCondition]:
    """Return a validated SuccessCondition dict, or None when invalid/missing."""
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
    """
    Copy fixed-suite step success conditions onto the runtime steps when possible.

    Small safe assumption:
        - Step-local success_condition always wins.
        - When test_case_id resolves to a fixed test case, index-aligned click
          steps inherit the test case step's success_condition if present.
        - If no step-level condition is present on the last click step, fall back
          to the test case's top-level success_condition.
    """
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
    """
    Return paired benchmark metadata for one test case from experiment_summary.

    This separates:
      - single-run health (`final_outcome`)
      - paired benchmark result for this test case
      - repo-level recommendation
    """
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


@pipeline_step("step_execution")
def run_capture(
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    screenshot_dir: Optional[Path] = None,
    generation_context: Optional[Dict[str, Any]] = None,
    test_case_id: str = "",
) -> Dict[str, Any]:
    """
    Execute capture steps against the preview URL and write screenshots to disk.

    Dispatches to the Playwright runner (default) or the Agent Browser
    experiment runner based on the BROWSER_BACKEND environment variable.

    Phase 4 / Phase 5 additions (backward-compatible):
        test_case_id — optional experiment test case identifier. When non-empty,
                       run artifacts (run_trace.json, run_summary.json) are saved
                       to app/data/experiment_runs/<run_id>/ via ExperimentLogger.
        backend      — added to return dict so callers can identify which backend ran.
        mode         — added to return dict for experiment run traceability.
        test_case_id — echoed in return dict for downstream comparison logic.
        final_outcome — single-run health for this backend invocation.
        benchmark_outcome / benchmark_has_paired_baseline — paired benchmark
                       result for this test case when a Playwright baseline exists.
        repo_decision_outcome / repo_recommendation / promotion_allowed — repo-level
                       Phase 5 decision fields. These do not change the default backend.

    Args:
        preview_url:        Base URL of the preview deployment.
        steps:              List of step dicts (action/url/selector/text).
        screenshot_dir:     Directory for shot*.png; defaults to app/screenshots.
        generation_context: Optional context dict from step generation.
        test_case_id:       Phase 4: experiment test case identifier. Pass one of
                            the FIXED_TEST_SUITE ids (e.g. "tc_01_semantic_button")
                            to trigger artifact persistence.

    Returns:
        Dict with steps_succeeded, steps_failed, failure_reason, success, debug,
        backend, mode, test_case_id, final_outcome, benchmark_outcome,
        benchmark_has_paired_baseline, repo_decision_outcome,
        repo_recommendation, decision_outcome, promotion_allowed, default_backend.
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
    steps = _attach_test_case_success_conditions(steps, test_case_id)
    out_dir = screenshot_dir or SCREENSHOT_DIR
    capture_settings = load_capture_settings()

    _active_mode = (
        _EXPERIMENT_MODE if BROWSER_BACKEND == "agent_browser_cli" else "playwright"
    )

    # ------------------------------------------------------------------
    # Phase 3 / Phase 4 — Agent Browser experiment path
    # ------------------------------------------------------------------
    if BROWSER_BACKEND == "agent_browser_cli":
        _runner_result = run_ab_stepwise(
            preview_url=preview_url,
            initial_steps=steps,
            screenshot_dir=out_dir,
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
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }
        else:
            _result = {
                "steps_succeeded": int(_runner_result.get("steps_succeeded", 0)),
                "steps_failed": 1,
                "failure_reason": _runner_result.get("failure_reason") or "ab_execution_failed",
                "success": False,
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }

    # ------------------------------------------------------------------
    # Default — existing Playwright stepwise path (unchanged)
    # ------------------------------------------------------------------
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
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }
        else:
            _result = {
                "steps_succeeded": 0,
                "steps_failed": 1,
                "failure_reason": _runner_result.get("failure_reason") or "stepwise_execution_failed",
                "success": False,
                "debug": {"engine": _engine, "results": _runner_result.get("results", [])},
            }

    # ------------------------------------------------------------------
    # Phase 4 — Experiment metadata (appended to all return paths)
    # ------------------------------------------------------------------
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

    # Persist experiment artifacts when a test_case_id is provided.
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
