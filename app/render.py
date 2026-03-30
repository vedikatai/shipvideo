import subprocess
from pathlib import Path
import glob
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"


@pipeline_step("render")
def render_video():
    """Create a video from screenshots. Dynamically finds all shot*.png files in order."""
    output_path = SCREENSHOT_DIR / "out.mp4"

    cs = load_capture_settings()
    W = cs.viewport_width
    H = cs.viewport_height


    shot_files = sorted(glob.glob(str(SCREENSHOT_DIR / "shot*.png")))

    if not shot_files:
        raise FileNotFoundError("No screenshot files found (shot*.png)")

    print(f"[render] screenshots={len(shot_files)} viewport={W}x{H}", flush=True)



    scale_pad = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )


    if len(shot_files) == 1:

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

        input_args = []
        for shot in shot_files:
            input_args.extend(["-loop", "1", "-t", "3", "-i", str(shot)])

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
