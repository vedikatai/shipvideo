import subprocess
from pathlib import Path
import glob
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Suppress ffmpeg progress/codec spam; only real errors will be shown via -loglevel error
FFMPEG_LOGLEVEL = "-loglevel", "error"


@pipeline_step("render")
def render_video():
    """Create a video from screenshots. Dynamically finds all shot*.png files in order."""
    output_path = SCREENSHOT_DIR / "out.mp4"

    cs = load_capture_settings()
    W = cs.viewport_width
    H = cs.viewport_height

    # Find all screenshot files in order (shot1.png, shot2.png, shot3.png, etc.)
    shot_files = sorted(glob.glob(str(SCREENSHOT_DIR / "shot*.png")))

    if not shot_files:
        raise FileNotFoundError("No screenshot files found (shot*.png)")

    print(f"[render] screenshots={len(shot_files)} viewport={W}x{H}", flush=True)

    # scale+pad template: shrink to fit, then pad with black to exact viewport size.
    # This normalises full-page screenshots (variable height) to a fixed canvas.
    scale_pad = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )

    # Build FFmpeg command dynamically based on number of screenshots
    if len(shot_files) == 1:
        # Single screenshot: normalise then loop
        shot_path = shot_files[0]
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            "-loop", "1", "-t", "3", "-i", str(shot_path),
            "-vf", scale_pad,
            "-r", "30",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        # Multiple screenshots: scale+pad each input, then concat
        input_args = []
        for shot in shot_files:
            input_args.extend(["-loop", "1", "-t", "3", "-i", str(shot)])
        # Build per-input scale+pad filter chains, then concat normalised streams
        filter_chains = "".join(
            f"[{i}:v]{scale_pad}[v{i}];" for i in range(len(shot_files))
        )
        concat_inputs = "".join(f"[v{i}]" for i in range(len(shot_files)))
        concat_filter = f"{filter_chains}{concat_inputs}concat=n={len(shot_files)}:v=1:a=0,format=yuv420p"
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            *input_args,
            "-filter_complex", concat_filter,
            "-r", "30",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and result.stderr:
        print(f"[render] ffmpeg stderr: {result.stderr.strip()}", flush=True)
    result.check_returncode()

if __name__ == "__main__":
    render_video()
