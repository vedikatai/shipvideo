import subprocess
import os

APP_DIR = os.path.dirname(__file__)

def run_pipeline():
    """
    Runs capture -> render.
    Assumes frontend is already running.
    """
    print("▶️ Starting video pipeline")

    # 1. Capture screenshots
    subprocess.run(
        ["python", "capture.py"],
        cwd=APP_DIR,
        check=True
    )

    # 2. Render video
    subprocess.run(
        ["python", "render.py"],
        cwd=APP_DIR,
        check=True
    )

    print("✅ Video pipeline finished")
