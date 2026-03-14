import subprocess
import os
import sys
from pathlib import Path
from app.storage import upload_video
from app.capture import capture_demo
from app.render import render_video
from observability import pipeline_step

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent


@pipeline_step("video_pipeline")
def run_pipeline(pr_number: int, preview_url: str, steps=None):
    """
    Runs capture -> render -> upload sequentially.

    Returns:
        tuple: (video_url: str, capture_summary: dict with steps_succeeded, steps_failed, failure_reason)
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")

    try:
        capture_summary = capture_demo(preview_url=preview_url, steps=steps)
        render_video()
        video_path = APP_DIR / "out.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        video_url = upload_video(video_path, pr_number=pr_number)
    except subprocess.CalledProcessError as e:
        print(f"[video_pipeline] subprocess failed returncode={e.returncode}", flush=True)
        if e.stdout:
            print(f"[video_pipeline] stdout: {e.stdout[:500]}", flush=True)
        if e.stderr:
            print(f"[video_pipeline] stderr: {e.stderr[:500]}", flush=True)
        raise e
    except Exception as e:
        print(f"[video_pipeline] error: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise e
    return video_url, capture_summary
