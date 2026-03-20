"""
Pipeline: orchestrates PR analysis (extraction + step generation) and video pipeline.

Execution strategy (dual-pipeline):
  1. Script-first (preferred): LLM generates a full Playwright script → continuous webm → mp4.
     Produces smooth, real screen-recording quality video.
  2. Stepwise (fallback): step-by-step execution with self-healing retries → screenshots → mp4.
     More resilient when the script-first pipeline cannot recover from LLM errors.

Call analyze_pr() for steps + narration; call run_pipeline() for full capture → render → upload.
"""
from __future__ import annotations

import subprocess
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.steps.pr_extraction import fetch_pr_diff
from app.render import render_video
from app.steps.step_execution import run_capture
from app.steps.step_generation import generate_steps_from_diff
from app.storage import upload_video
from app.script_pipeline import ScriptPipelineError, run_script_pipeline
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
    *,
    diff_files: Optional[List[Dict[str, str]]] = None,
    start_route: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches PR diff, generates grounded steps and narration via LLM + DOM crawl.

    Returns:
        Dict with keys: steps, narration, llm_cost_usd; optionally budget_exceeded.
    """
    try:
        print("\n[steps.pipeline] === ANALYZE PR (diff → steps) ===", flush=True)
        print(
            f"[steps.pipeline/analyze_pr] repo={repo_full_name} pr={pr_number}",
            flush=True,
        )
        diff_files = diff_files if diff_files is not None else fetch_pr_diff(repo_full_name, pr_number)

        if not diff_files:
            print("[steps.pipeline/analyze_pr] no diff files; using default screenshot", flush=True)
            return {
                "steps": [{"action": "screenshot"}],
                "narration": "Demo screenshot for this pull request.",
                "llm_cost_usd": 0.0,
            }

        print(f"[steps.pipeline/analyze_pr] files_changed={len(diff_files)}", flush=True)
        flow = await generate_steps_from_diff(
            diff_files,
            pr_title,
            staging_url,
            start_route=start_route,
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
            "suggested_demo_flow": flow.get("suggested_demo_flow", ""),
            "generation_context": flow.get("generation_context"),
        }
    except Exception as e:
        print(
            f"[steps.pipeline/analyze_pr] failed: {type(e).__name__}: {e}",
            flush=True,
        )
        import traceback
        traceback.print_exc()
        return {
            "steps": [{"action": "screenshot"}],
            "narration": "Demo screenshot for this pull request (fallback).",
            "llm_cost_usd": 0.0,
            "generation_context": None,
        }


@pipeline_step("video_pipeline")
def run_pipeline(
    pr_number: int,
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    generation_context: Optional[Dict[str, Any]] = None,
    upload: bool = True,
) -> tuple:
    """
    Dual-pipeline runner: tries script-first for smooth continuous video,
    falls back to stepwise (screenshot-stitch) on failure.

    Pipeline selection logic:
      - script-first: requires suggested_demo_flow in generation_context.
      - stepwise: always available as fallback.

    Returns:
        tuple: (video_url: str, capture_summary: dict)
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")

    import traceback as _tb

    print("\n[steps.pipeline] === VIDEO PIPELINE (script-first → stepwise fallback) ===", flush=True)

    video_path: Optional[Path] = None
    capture_summary: Dict[str, Any] = {}
    pipeline_used = "unknown"

    # ── Script-first attempt ────────────────────────────────────────────────
    has_demo_flow = bool(
        generation_context
        and (generation_context.get("suggested_demo_flow") or "").strip()
    )

    if has_demo_flow:
        print("[steps.pipeline] trying script-first pipeline", flush=True)
        try:
            result = run_script_pipeline(
                pr_number=pr_number,
                preview_url=preview_url,
                generation_context=generation_context,
                screenshot_dir=SCREENSHOT_DIR,
            )
            video_path = Path(result["video_path"])
            pipeline_used = "script"
            capture_summary = {
                "pipeline": "script",
                "attempts": result["attempts"],
                "steps_succeeded": 1,
                "steps_failed": 0,
                "failure_reason": None,
                "success": True,
            }
            print(
                f"[steps.pipeline] script-first succeeded attempts={result['attempts']}",
                flush=True,
            )
        except ScriptPipelineError as e:
            print(
                f"[steps.pipeline] script-first failed ({e}); falling back to stepwise",
                flush=True,
            )
        except Exception as e:
            print(
                f"[steps.pipeline] script-first unexpected error ({type(e).__name__}: {e}); "
                "falling back to stepwise",
                flush=True,
            )
            _tb.print_exc()
    else:
        print(
            "[steps.pipeline] no suggested_demo_flow — skipping script-first, using stepwise directly",
            flush=True,
        )

    # ── Stepwise fallback ────────────────────────────────────────────────────
    if video_path is None:
        print("[steps.pipeline] running stepwise pipeline", flush=True)
        try:
            capture_summary = run_capture(
                preview_url=preview_url,
                steps=steps,
                screenshot_dir=SCREENSHOT_DIR,
                generation_context=generation_context,
            )
            if not capture_summary.get("success", False):
                debug = capture_summary.get("debug") or {}
                raise RuntimeError(
                    "Stepwise capture failed and pipeline aborted. "
                    f"steps_failed={capture_summary.get('steps_failed')} "
                    f"failure_reason={capture_summary.get('failure_reason')}. "
                    f"debug_preview={json.dumps(debug, ensure_ascii=False, default=str)[:4000]}"
                )
            render_video()
            video_path = SCREENSHOT_DIR / "out.mp4"
            pipeline_used = "stepwise"
            capture_summary["pipeline"] = "stepwise"
        except subprocess.CalledProcessError as e:
            print(
                f"[steps.pipeline/stepwise] subprocess failed returncode={e.returncode}",
                flush=True,
            )
            if e.stdout:
                print(f"[steps.pipeline/stepwise] stdout: {e.stdout[:500]}", flush=True)
            if e.stderr:
                print(f"[steps.pipeline/stepwise] stderr: {e.stderr[:500]}", flush=True)
            raise
        except Exception as e:
            print(
                f"[steps.pipeline/stepwise] error: {type(e).__name__}: {e}",
                flush=True,
            )
            _tb.print_exc()
            raise

    # ── Final validation + upload ────────────────────────────────────────────
    if not video_path or not video_path.exists():
        raise FileNotFoundError(f"Video file not found after {pipeline_used} pipeline: {video_path}")

    print(f"[steps.pipeline] pipeline_used={pipeline_used} video={video_path}", flush=True)

    if upload:
        video_url = upload_video(video_path, pr_number=pr_number)
    else:
        video_url = str(video_path)

    return video_url, capture_summary
