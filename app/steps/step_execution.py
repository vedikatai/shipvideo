"""
Step execution: entry point that runs capture steps against a preview URL.

Delegates all step-by-step execution, navigation detection, and retry logic
to app.execution.step_runner.run_stepwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from observability import pipeline_step
from app.config_types import load_capture_settings
from app.execution.step_runner import run_stepwise

BASE_APP_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_STEPS: List[Dict[str, Any]] = [
    {"action": "screenshot"},
    {"action": "screenshot"},
]

MAX_STEP_RETRIES = 3


@pipeline_step("step_execution")
def run_capture(
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    screenshot_dir: Optional[Path] = None,
    generation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute capture steps against the preview URL and write screenshots to disk.

    Args:
        preview_url: Base URL of the preview deployment.
        steps: List of step dicts (action: goto | click | screenshot, plus url/selector/text).
        screenshot_dir: Directory for shot*.png files; defaults to app/screenshots directory.
        generation_context: Optional context dict from step generation (passed to step_runner).

    Returns:
        Dict with steps_succeeded, steps_failed, failure_reason, success, debug.
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
    out_dir = screenshot_dir or SCREENSHOT_DIR
    capture_settings = load_capture_settings()

    objective = {
        "goal": "Generate reliable demo actions from current DOM only",
        "generation_context": generation_context or {},
    }
    stepwise = run_stepwise(
        preview_url=preview_url,
        initial_steps=steps,
        objective=objective,
        screenshot_dir=out_dir,
        max_retries_per_failure=MAX_STEP_RETRIES,
        capture_settings=capture_settings,
    )
    if stepwise.get("success"):
        return {
            "steps_succeeded": int(stepwise.get("steps_succeeded", 0)),
            "steps_failed": int(stepwise.get("steps_failed", 0)),
            "failure_reason": None,
            "success": True,
            "debug": {"engine": "stepwise", "results": stepwise.get("results", [])},
        }
    return {
        "steps_succeeded": 0,
        "steps_failed": 1,
        "failure_reason": stepwise.get("failure_reason") or "stepwise_execution_failed",
        "success": False,
        "debug": {"engine": "stepwise", "results": stepwise.get("results", [])},
    }


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
