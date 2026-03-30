from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.generator.script_generator import MAX_SCRIPT_RETRIES, generate_playwright_script
from app.recorder.playwright_runner import run_script
from app.recorder.video_processor import convert_webm_to_mp4

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"


class ScriptPipelineError(RuntimeError):
    pass


def _log(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def run_script_pipeline(
    *,
    pr_number: int,
    preview_url: str,
    generation_context: Dict[str, Any],
    screenshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    out_dir = screenshot_dir or SCREENSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    suggested_demo_flow: str = generation_context.get("suggested_demo_flow") or ""
    dom_data: Dict[str, Any] = generation_context.get("dom_data") or {}
    app_hints: str = generation_context.get("app_hints") or ""
    diff_files = generation_context.get("diffs_for_prompt") or []

    if not suggested_demo_flow:
        raise ScriptPipelineError(
            "script_pipeline requires suggested_demo_flow in generation_context; "
            "run generate_steps_from_diff first."
        )

    _log(
        "script_pipeline.start",
        {
            "pr_number": pr_number,
            "preview_url": preview_url,
            "suggested_demo_flow_chars": len(suggested_demo_flow),
        },
    )

    previous_script: Optional[str] = None
    previous_error: Optional[str] = None

    for attempt in range(1, MAX_SCRIPT_RETRIES + 2):                                   
        _log("script_pipeline.attempt", {"attempt": attempt, "max": MAX_SCRIPT_RETRIES + 1})


        try:
            script = generate_playwright_script(
                suggested_demo_flow=suggested_demo_flow,
                dom_data=dom_data,
                base_url=preview_url,
                app_hints=app_hints,
                diff_files=diff_files,
                previous_script=previous_script,
                previous_error=previous_error,
            )
        except Exception as e:
            _log("script_pipeline.generation_failed", {"attempt": attempt, "error": str(e)})
            raise ScriptPipelineError(f"Script generation failed: {e}") from e


        result = run_script(
            script=script,
            base_url=preview_url,
            output_dir=out_dir,
        )

        if result["success"]:
            webm_path = Path(result["webm_path"])
            _log("script_pipeline.execution_success", {"attempt": attempt, "webm": str(webm_path)})


            mp4_path = convert_webm_to_mp4(webm_path, out_dir)
            _log("script_pipeline.video_ready", {"mp4": str(mp4_path)})
            return {
                "success": True,
                "video_path": str(mp4_path),
                "pipeline": "script",
                "attempts": attempt,
                "error": None,
            }


        previous_error = result.get("error") or "execution_failed"
        previous_script = script
        _log(
            "script_pipeline.execution_failed",
            {"attempt": attempt, "error": previous_error, "retrying": attempt <= MAX_SCRIPT_RETRIES},
        )

        if attempt > MAX_SCRIPT_RETRIES:
            break

    raise ScriptPipelineError(
        f"Script pipeline failed after {MAX_SCRIPT_RETRIES + 1} attempts. "
        f"Last error: {previous_error}"
    )
