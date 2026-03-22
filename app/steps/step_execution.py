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
        final_outcome / decision_outcome / promotion_allowed — Phase 5 experiment
                       decision fields. These do not change the default backend.

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
        backend, mode, test_case_id, final_outcome, decision_outcome,
        promotion_allowed, default_backend.
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
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
