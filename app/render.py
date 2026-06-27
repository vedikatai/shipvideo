import subprocess
from pathlib import Path
from typing import Iterable, List, Optional
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"
# Per-frame hold duration. Keep constant so narration timing stays predictable.
FRAME_DURATION_S = "2.5"
OUTPUT_FPS = 30


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

    # Preserve order, drop missing paths, dedupe consecutive identical paths
    # (before/after shots of the same file would otherwise look like a stutter).
    shot_files: List[str] = []
    for frame in approved_frames or []:
        path = Path(frame)
        if not path.exists():
            continue
        resolved = str(path.resolve())
        if shot_files and shot_files[-1] == resolved:
            continue
        shot_files.append(resolved)

    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{approval.get('reasons') or ['unknown']}"
        )

    if not shot_files:
        raise FileNotFoundError("No approved screenshot frames provided for render")

    print(f"[render] screenshots={len(shot_files)} viewport={W}x{H}", flush=True)

    # Force CFR per segment *before* concat. Applying only output -r causes
    # timestamp discontinuities at scene boundaries → dropped/duplicated frames.
    segment_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={OUTPUT_FPS},"
        f"setpts=PTS-STARTPTS,"
        f"format=yuv420p"
    )

    if len(shot_files) == 1:
        shot_path = shot_files[0]
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            "-loop", "1",
            "-framerate", str(OUTPUT_FPS),
            "-t", FRAME_DURATION_S,
            "-i", shot_path,
            "-vf", segment_vf,
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-vsync", "cfr",
            "-r", str(OUTPUT_FPS),
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        input_args: List[str] = []
        for shot in shot_files:
            input_args.extend([
                "-loop", "1",
                "-framerate", str(OUTPUT_FPS),
                "-t", FRAME_DURATION_S,
                "-i", shot,
            ])

        filter_chains = "".join(
            f"[{i}:v]{segment_vf}[v{i}];" for i in range(len(shot_files))
        )
        concat_inputs = "".join(f"[v{i}]" for i in range(len(shot_files)))
        # n= clips must share timebase/fps (enforced above). Reset PTS so
        # encoder does not retime across boundaries.
        concat_filter = (
            f"{filter_chains}{concat_inputs}"
            f"concat=n={len(shot_files)}:v=1:a=0,"
            f"fps={OUTPUT_FPS},setpts=PTS-STARTPTS,format=yuv420p[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            *input_args,
            "-filter_complex", concat_filter,
            "-map", "[outv]",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-vsync", "cfr",
            "-r", str(OUTPUT_FPS),
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
