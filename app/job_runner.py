import subprocess
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

def run_pipeline():
    """
    Runs capture -> render sequentially.
    Prints logs for every step.
    """
    try:
        print("▶️ Starting video pipeline")

        # 1️⃣ Capture screenshots
        capture_script = APP_DIR / "capture.py"
        subprocess.run(
            ["python3", str(capture_script)],
            cwd=str(APP_DIR),
            check=True
        )
        print("📸 Capture finished")

        # 2️⃣ Render video
        render_script = APP_DIR / "render.py"
        subprocess.run(
            ["python3", str(render_script)],
            cwd=str(APP_DIR),
            check=True
        )
        print("🎬 Video rendering finished")

    except subprocess.CalledProcessError as e:
        print("❌ Pipeline failed:", e)
        raise e
    except Exception as e:
        print("❌ Unexpected error in pipeline:", e)
        raise e

    print("✅ Video pipeline finished")
