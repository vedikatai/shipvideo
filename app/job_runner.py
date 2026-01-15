import subprocess
import os
import sys
from pathlib import Path
from app.storage import upload_video
from app.capture import capture_demo
from app.render import render_video

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

def run_pipeline(pr_number=None):
    """
    Runs capture -> render -> upload sequentially.
    
    Args:
        pr_number: PR number to pass to capture for preview URL resolution.
    
    Returns:
        str: Public URL of the uploaded video
    """
    try:
        print("▶️ Starting video pipeline", flush=True)
        if pr_number:
            print(f"🔢 PR Number: {pr_number}", flush=True)

        # 1️⃣ Capture screenshots
        print("📸 Running capture module", flush=True)
        capture_demo(pr_number=pr_number)
        print("📸 Capture finished", flush=True)

        # 2️⃣ Render video
        print("🎬 Running render module", flush=True)
        render_video()
        print("🎬 Video rendering finished", flush=True)

        # 3️⃣ Upload video to R2
        video_path = APP_DIR / "out.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        print("☁️ Uploading video to R2...", flush=True)
        video_url = upload_video(video_path, pr_number=pr_number)
        print(f"✅ Video uploaded to R2: {video_url}", flush=True)

    except subprocess.CalledProcessError as e:
        print(f"❌ Pipeline failed with return code {e.returncode}:", flush=True)
        if e.stdout:
            print("STDOUT:", e.stdout, flush=True)
        if e.stderr:
            print("STDERR:", e.stderr, flush=True)
        raise e
    except Exception as e:
        print(f"❌ Unexpected error in pipeline: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise e

    print("✅ Video pipeline finished", flush=True)
    return video_url
