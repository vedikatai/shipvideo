import subprocess
import os
import sys
from pathlib import Path
from app.storage import upload_video

APP_DIR = Path(__file__).resolve().parent

def run_pipeline():
    """
    Runs capture -> render -> upload sequentially.
    Prints logs for every step.
    
    Returns:
        str: Public URL of the uploaded video
    """
    try:
        print("▶️ Starting video pipeline", flush=True)

        # 1️⃣ Capture screenshots
        capture_script = APP_DIR / "capture.py"
        print(f"📸 Running capture script: {capture_script}", flush=True)
        result = subprocess.run(
            ["python3", str(capture_script)],
            cwd=str(APP_DIR),
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print("Capture stdout:", result.stdout, flush=True)
        if result.stderr:
            print("Capture stderr:", result.stderr, flush=True)
        print("📸 Capture finished", flush=True)

        # 2️⃣ Render video
        render_script = APP_DIR / "render.py"
        print(f"🎬 Running render script: {render_script}", flush=True)
        result = subprocess.run(
            ["python3", str(render_script)],
            cwd=str(APP_DIR),
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print("Render stdout:", result.stdout, flush=True)
        if result.stderr:
            print("Render stderr:", result.stderr, flush=True)
        print("🎬 Video rendering finished", flush=True)

        # 3️⃣ Upload video to R2
        video_path = APP_DIR / "out.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        print("☁️ Uploading video to R2...", flush=True)
        video_url = upload_video(video_path)
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
