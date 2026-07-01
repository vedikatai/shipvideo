import subprocess
from pathlib import Path
from typing import Iterable, List, Optional
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"

# Manifest-first hold: each approved still is shown for FRAME_DURATION_S.
# Last frame gets +1/OUTPUT_FPS so the encoder never trims the closing milestone
# when vsync/concat rounds duration down (intermittent missing tail).
FRAME_DURATION_S = 3.0
OUTPUT_FPS = 30
LAST_FRAME_PAD_S = 1.0 / OUTPUT_FPS


def _frame_hold_seconds(index: int, total: int) -> float:
    if total <= 0:
        return FRAME_DURATION_S
    if index == total - 1:
        return FRAME_DURATION_S + LAST_FRAME_PAD_S
    return FRAME_DURATION_S


@pipeline_step("render")
def render_video(
    approved_frames: Optional[Iterable[str | Path]] = None,
    *,
    render_approval: Optional[dict] = None,
):
    output_path = SCREENSHOT_DIR / "out.mp4"

    cs = load_capture_settings()
    W = cs.viewport_width
    H = cs.viewport_height

    shot_files: List[str] = []
    for frame in approved_frames or []:
        path = Path(frame)
        if path.exists():
            shot_files.append(str(path))

    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{approval.get('reasons') or ['unknown']}"
        )

    if not shot_files:
        raise FileNotFoundError("No approved screenshot frames provided for render")

    n = len(shot_files)
    print(
        f"[render] screenshots={n} viewport={W}x{H} "
        f"frame_hold={FRAME_DURATION_S}s last_pad={LAST_FRAME_PAD_S:.4f}s",
        flush=True,
    )

    scale_pad = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )

    if n == 1:
        hold = _frame_hold_seconds(0, 1)
        shot_path = shot_files[0]
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            "-loop", "1", "-t", f"{hold:.4f}", "-i", str(shot_path),
            "-vf", scale_pad,
            "-r", str(OUTPUT_FPS),
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        input_args: List[str] = []
        for i, shot in enumerate(shot_files):
            hold = _frame_hold_seconds(i, n)
            input_args.extend(["-loop", "1", "-t", f"{hold:.4f}", "-i", str(shot)])

        filter_chains = "".join(
            f"[{i}:v]{scale_pad}[v{i}];" for i in range(n)
        )
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        # fps= after concat stabilizes timestamps so the last hold is not cut.
        concat_filter = (
            f"{filter_chains}{concat_inputs}"
            f"concat=n={n}:v=1:a=0,fps={OUTPUT_FPS},format=yuv420p"
        )
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            *input_args,
            "-filter_complex", concat_filter,
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
