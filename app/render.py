import subprocess
from pathlib import Path
from typing import Iterable, List, Optional
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"
FRAME_DURATION_S = 3.0
OUTPUT_FPS = 30
# Demos longer than this use preset=fast to cut encode time ~in half with
# negligible quality loss for still-frame demo content.
LONG_DEMO_SECONDS = 30.0
PRESET_DEFAULT = "medium"
PRESET_LONG = "fast"


def _estimate_duration_s(frame_count: int, frame_duration_s: float = FRAME_DURATION_S) -> float:
    return max(frame_count, 0) * float(frame_duration_s)


def _x264_preset_for_duration(duration_s: float) -> str:
    if duration_s > LONG_DEMO_SECONDS:
        return PRESET_LONG
    return PRESET_DEFAULT


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
    prev = ""
    for frame in approved_frames or []:
        path = Path(frame)
        if not path.exists():
            continue
        resolved = str(path.resolve())
        if resolved == prev:
            continue
        shot_files.append(resolved)
        prev = resolved

    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{approval.get('reasons') or ['unknown']}"
        )

    if not shot_files:
        raise FileNotFoundError("No approved screenshot frames provided for render")

    duration_s = _estimate_duration_s(len(shot_files))
    preset = _x264_preset_for_duration(duration_s)
    print(
        f"[render] screenshots={len(shot_files)} viewport={W}x{H} "
        f"duration_est={duration_s:.1f}s preset={preset}",
        flush=True,
    )

    scale_pad = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )
    encode_args = [
        "-c:v", "libx264",
        "-preset", preset,
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    if len(shot_files) == 1:
        shot_path = shot_files[0]
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            "-loop", "1", "-t", str(FRAME_DURATION_S), "-i", str(shot_path),
            "-vf", scale_pad,
            "-r", str(OUTPUT_FPS),
            *encode_args,
            str(output_path),
        ]
    else:
        input_args: List[str] = []
        for shot in shot_files:
            input_args.extend(["-loop", "1", "-t", str(FRAME_DURATION_S), "-i", str(shot)])

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
            "-r", str(OUTPUT_FPS),
            *encode_args,
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
