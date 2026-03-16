"""
Pipeline: orchestrates PR analysis (extraction + step generation) and video pipeline
(capture → render → upload).

This module wires together:
  - pr_extraction: fetch PR diff from GitHub
  - step_generation: LLM steps from diff + DOM
  - step_execution: run steps and write screenshots
  - render: build video from screenshots
  - storage: upload video to R2

Call analyze_pr() for steps + narration; call run_pipeline() for full capture → render → upload.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.steps.pr_extraction import fetch_pr_diff
from app.render import render_video
from app.steps.step_execution import run_capture
from app.steps.step_generation import generate_steps_from_diff
from app.storage import upload_video
from observability import pipeline_step

BASE_APP_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@pipeline_step("analyze_pr")
async def analyze_pr(
    repo_full_name: str,
    pr_number: int,
    pr_title: Optional[str],
    staging_url: str,
) -> Dict[str, Any]:
    """
    Fetches PR diff, generates grounded steps and narration via LLM + DOM crawl.

    Returns:
        Dict with keys: steps, narration, llm_cost_usd; optionally budget_exceeded.
    """
    try:
        print(
            f"[pipeline] analyzing PR repo={repo_full_name} pr={pr_number}",
            flush=True,
        )
        diff_files = fetch_pr_diff(repo_full_name, pr_number)

        if not diff_files:
            print("[pipeline] no diff files; using default screenshot", flush=True)
            return {
                "steps": [{"action": "screenshot"}],
                "narration": "Demo screenshot for this pull request.",
                "llm_cost_usd": 0.0,
            }

        print(f"[pipeline] files_changed={len(diff_files)}", flush=True)
        flow = await generate_steps_from_diff(
            diff_files, pr_title, staging_url
        )
        steps = flow.get("steps") or [{"action": "screenshot"}]
        narration = flow.get("narration") or "Demo screenshot for this pull request."
        budget_exceeded = flow.get("budget_exceeded", False)
        llm_cost_usd = flow.get("llm_cost_usd", 0.0)
        return {
            "steps": steps,
            "narration": narration,
            "budget_exceeded": budget_exceeded,
            "llm_cost_usd": llm_cost_usd,
        }
    except Exception as e:
        print(
            f"[pipeline] analyze_pr failed: {type(e).__name__}: {e}",
            flush=True,
        )
        import traceback
        traceback.print_exc()
        return {
            "steps": [{"action": "screenshot"}],
            "narration": "Demo screenshot for this pull request (fallback).",
            "llm_cost_usd": 0.0,
        }


@pipeline_step("video_pipeline")
def run_pipeline(
    pr_number: int,
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
) -> tuple:
    """
    Runs capture → render → upload sequentially.

    Returns:
        tuple: (video_url: str, capture_summary: dict with steps_succeeded, steps_failed, failure_reason)
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")

    try:
        capture_summary = run_capture(
            preview_url=preview_url,
            steps=steps,
            screenshot_dir=SCREENSHOT_DIR,
        )
        render_video()
        video_path = SCREENSHOT_DIR / "out.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        video_url = upload_video(video_path, pr_number=pr_number)
    except subprocess.CalledProcessError as e:
        print(
            f"[pipeline] subprocess failed returncode={e.returncode}",
            flush=True,
        )
        if e.stdout:
            print(f"[pipeline] stdout: {e.stdout[:500]}", flush=True)
        if e.stderr:
            print(f"[pipeline] stderr: {e.stderr[:500]}", flush=True)
        raise
    except Exception as e:
        print(
            f"[pipeline] error: {type(e).__name__}: {e}",
            flush=True,
        )
        import traceback
        traceback.print_exc()
        raise
    return video_url, capture_summary
