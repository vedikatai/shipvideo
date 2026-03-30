"""
Pipeline: orchestrates PR analysis (extraction + step generation) and video pipeline.

Execution strategy:
  - Default: **stepwise only** — `run_capture` (Agent Browser CLI by default, see
    `app.steps.step_execution`). Screenshots → mp4.
  - Optional script-first: set env `VIDEO_PIPELINE=script_first` to try the legacy
    Playwright script path first (smooth continuous recording), then fall back to
    stepwise on failure.

Call analyze_pr() for steps + narration; call run_pipeline() for full capture → render → upload.
"""
from __future__ import annotations
from app.steps.errors import ContractIntegrityError
import os
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.steps.pr_extraction import fetch_pr_diff
from app.render import render_video
from app.steps.step_execution import run_capture
from app.steps.step_generation import generate_steps_from_diff
from app.storage import upload_video
from app.script_pipeline import ScriptPipelineError, run_script_pipeline
from app.trigger import evaluate_trigger
from app.steps.metrics import new_run_metrics, write_run_metrics
from app.config import load_config
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

        from app.steps.contract_extraction import extract_contract_static
        contract = extract_contract_static(diff_files)
        print(
            f"[steps.pipeline/analyze_pr] contract_id={contract.contract_id} "
            f"confidence={contract.confidence} "
            f"targets={len(contract.targets)} "
            f"start_route={contract.start_route!r}",
            flush=True,
        )

        config = load_config()
        decision = evaluate_trigger(diff_files, config)
        print(
            f"[steps.pipeline/analyze_pr] trigger should_run={decision.should_run} "
            f"reason={decision.reason!r}",
            flush=True,
        )
        if not decision.should_run:
            return {
                "skipped": True,
                "reason": decision.reason,
                "steps": [{"action": "screenshot"}],
                "narration": "Demo generation skipped for this pull request.",
                "llm_cost_usd": 0.0,
                "generation_context": None,
            }

        flow = await generate_steps_from_diff(
            diff_files,
            pr_title,
            staging_url,
            start_route=start_route,
            general_demo=decision.general_demo,
            contract=contract,
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
    except ContractIntegrityError:
        raise
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
    Video capture runner. By default (**VIDEO_PIPELINE** unset or `stepwise`) only
    the stepwise path runs (Agent Browser unless `BROWSER_BACKEND=playwright`).

    Set **VIDEO_PIPELINE=script_first** to attempt script-first first when
    `suggested_demo_flow` is present, then fall back to stepwise.

    Returns:
        tuple: (video_url: str, capture_summary: dict)
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")

    import traceback as _tb

    _video_pipeline = os.getenv("VIDEO_PIPELINE", "stepwise").strip().lower()
    use_script_first = _video_pipeline == "script_first"

    print(
        "\n[steps.pipeline] === VIDEO PIPELINE "
        f"(mode={_video_pipeline!r}; default=stepwise / Agent Browser) ===",
        flush=True,
    )

    video_path: Optional[Path] = None
    capture_summary: Dict[str, Any] = {}
    pipeline_used = "unknown"
    run_metrics = new_run_metrics(pr_number)
    run_metrics.preflight_passed = bool(generation_context)

    def _apply_capture_metrics() -> None:
        debug = capture_summary.get("debug") or {}
        results = debug.get("results") or []
        if not isinstance(results, list):
            results = []
        unvalidated = 0
        validated = 0
        wrong_clicks = 0
        terminal_reached = False
        console_count = 0
        page_error_count = 0
        network_request_count = 0
        network_error_count = 0
        for r in results:
            if not isinstance(r, dict):
                continue
            outcome = str(r.get("outcome") or "")
            if outcome == "unvalidated":
                unvalidated += 1
            if outcome == "wrong_click":
                wrong_clicks += 1
            if outcome == "success":
                validated += 1
            if r.get("terminal_condition_reached") is True:
                terminal_reached = True
            diagnostics = r.get("diagnostics") or {}
            if isinstance(diagnostics, dict):
                console_count += len(diagnostics.get("console_messages") or [])
                page_error_count += len(diagnostics.get("page_errors") or [])
                network_request_count += int(diagnostics.get("network_request_count") or 0)
                network_error_count += int(diagnostics.get("network_error_count") or 0)
        run_metrics.steps_unvalidated = unvalidated
        run_metrics.steps_validated = validated
        run_metrics.wrong_clicks = wrong_clicks
        run_metrics.terminal_condition_reached = terminal_reached
        run_metrics.extra.update({
            "console_count": console_count,
            "page_error_count": page_error_count,
            "network_request_count": network_request_count,
            "network_error_count": network_error_count,
        })

    def _finalize_run_metrics(*, success: bool, error: Optional[Exception] = None) -> None:
        run_metrics.pipeline = str(capture_summary.get("pipeline_branch", pipeline_used) or pipeline_used)
        run_metrics.capture_browser = str(capture_summary.get("capture_browser") or "unknown")
        _apply_capture_metrics()
        run_metrics.success = success
        run_metrics.video_usable = bool(video_path and video_path.exists() and video_path.stat().st_size > 0)
        if error is not None:
            run_metrics.error_type = type(error).__name__
            run_metrics.error_message = str(error)
        run_metrics.finished_at = datetime.now(timezone.utc).isoformat()
        metrics_path = write_run_metrics(run_metrics)
        print(f"[steps.pipeline] run_metrics file={metrics_path.name}", flush=True)


    has_demo_flow = bool(
        generation_context
        and (generation_context.get("suggested_demo_flow") or "").strip()
    )

    if use_script_first and has_demo_flow:
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
                "pipeline_branch": "script_first",

                "capture_browser": "playwright",
                "capture_path": "script_first_playwright",
                "agent_browser_used": False,
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
    elif use_script_first and not has_demo_flow:
        print(
            "[steps.pipeline] VIDEO_PIPELINE=script_first but no suggested_demo_flow "
            "— using stepwise directly",
            flush=True,
        )
    else:
        print(
            "[steps.pipeline] VIDEO_PIPELINE=stepwise — skipping script-first, using stepwise only",
            flush=True,
        )


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
                try:
                    print(
                        "[steps.pipeline/stepwise] debug_preview="
                        f"{json.dumps(debug, ensure_ascii=False, default=str)[:4000]}",
                        flush=True,
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    "Stepwise capture failed and pipeline aborted. "
                    f"steps_failed={capture_summary.get('steps_failed')} "
                    f"failure_reason={capture_summary.get('failure_reason')}."
                )
            render_video()
            video_path = SCREENSHOT_DIR / "out.mp4"
            pipeline_used = "stepwise"
            capture_summary["pipeline"] = "stepwise"
            capture_summary["pipeline_branch"] = "stepwise"
            capture_summary["capture_path"] = "stepwise"

            capture_summary["capture_browser"] = capture_summary.get("backend") or "playwright"
            capture_summary["agent_browser_used"] = (
                capture_summary.get("backend") == "agent_browser_cli"
            )
        except subprocess.CalledProcessError as e:
            print(
                f"[steps.pipeline/stepwise] subprocess failed returncode={e.returncode}",
                flush=True,
            )
            if e.stdout:
                print(f"[steps.pipeline/stepwise] stdout: {e.stdout[:500]}", flush=True)
            if e.stderr:
                print(f"[steps.pipeline/stepwise] stderr: {e.stderr[:500]}", flush=True)
            _finalize_run_metrics(success=False, error=e)
            raise
        except Exception as e:
            print(
                f"[steps.pipeline/stepwise] error: {type(e).__name__}: {e}",
                flush=True,
            )
            _tb.print_exc()
            _finalize_run_metrics(success=False, error=e)
            raise


    if not video_path or not video_path.exists():
        err = FileNotFoundError(f"Video file not found after {pipeline_used} pipeline: {video_path}")
        _finalize_run_metrics(success=False, error=err)
        raise err

    print(f"[steps.pipeline] pipeline_used={pipeline_used} video={video_path}", flush=True)
    _vp = capture_summary.get("pipeline_branch", pipeline_used)
    _cb = capture_summary.get("capture_browser", "unknown")
    _ab = capture_summary.get("agent_browser_used", False)
    print(
        f"[steps.pipeline] capture_summary: pipeline_branch={_vp!r} "
        f"capture_browser={_cb!r} agent_browser={_ab}",
        flush=True,
    )

    try:
        if upload:
            video_url = upload_video(video_path, pr_number=pr_number)
        else:
            video_url = str(video_path)
    except Exception as e:
        _finalize_run_metrics(success=False, error=e)
        raise

    _finalize_run_metrics(success=True)
    return video_url, capture_summary
