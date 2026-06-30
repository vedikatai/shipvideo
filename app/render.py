from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_LOGLEVEL: tuple[str, str] = ("-loglevel", "error")
FFMPEG_TIMEOUT_SECONDS: float = 120.0
FFMPEG_MAX_ATTEMPTS: int = 3


def _build_ffmpeg_cmd(
    shot_files: Sequence[str],
    *,
    output_path: Path,
    width: int,
    height: int,
) -> List[str]:
    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    if len(shot_files) == 1:
        return [
            "ffmpeg", "-y", *FFMPEG_LOGLEVEL,
            "-loop", "1", "-t", "3", "-i", shot_files[0],
            "-vf", scale_pad, "-r", "30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path),
        ]
    input_args: List[str] = []
    for shot in shot_files:
        input_args.extend(["-loop", "1", "-t", "3", "-i", shot])
    filter_chains = "".join(
        f"[{i}:v]{scale_pad}[v{i}];" for i in range(len(shot_files))
    )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(shot_files)))
    concat_filter = (
        f"{filter_chains}{concat_inputs}"
        f"concat=n={len(shot_files)}:v=1:a=0,format=yuv420p"
    )
    return [
        "ffmpeg", "-y", *FFMPEG_LOGLEVEL, *input_args,
        "-filter_complex", concat_filter, "-r", "30",
        "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path),
    ]


def run_ffmpeg_with_retry(
    cmd: Sequence[str],
    *,
    timeout: float = FFMPEG_TIMEOUT_SECONDS,
    max_attempts: int = FFMPEG_MAX_ATTEMPTS,
) -> subprocess.CompletedProcess[str]:
    last_err: Optional[BaseException] = None
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            print(f"[render] ffmpeg invoke timeout_s={timeout} attempt={attempt}", flush=True)
            result = subprocess.run(
                list(cmd), capture_output=True, text=True, timeout=timeout,
            )
            last_result = result
            if result.returncode == 0:
                return result
            print(
                f"[render] ffmpeg non-zero code={result.returncode} "
                f"stderr={(result.stderr or '')[:500]}",
                flush=True,
            )
            last_err = subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=result.stderr
            )
        except subprocess.TimeoutExpired as exc:
            last_err = exc
            print(f"[render] ffmpeg timeout attempt={attempt}", flush=True)
        except OSError as exc:
            last_err = exc
            print(f"[render] ffmpeg OSError: {exc}", flush=True)
        if attempt < max_attempts:
            time.sleep(min(0.5 * attempt, 2.0))
    if last_result is not None and last_result.returncode != 0:
        last_result.check_returncode()
    if last_err:
        raise last_err
    raise RuntimeError("ffmpeg failed")


@pipeline_step("render")
def render_video(
    approved_frames: Optional[Iterable[Union[str, Path]]] = None,
    *,
    render_approval: Optional[dict] = None,
) -> Path:
    output_path = SCREENSHOT_DIR / "out.mp4"
    cs = load_capture_settings()
    width, height = int(cs.viewport_width), int(cs.viewport_height)
    shot_paths = sorted(
        (Path(f) for f in (approved_frames or []) if Path(f).exists()),
        key=lambda p: p.name,
    )
    shot_files = [str(p) for p in shot_paths]
    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{approval.get('reasons') or ['unknown']}"
        )
    if not shot_files:
        raise FileNotFoundError("No approved screenshot frames provided for render")
    print(f"[render] screenshots={len(shot_files)} viewport={width}x{height}", flush=True)
    cmd = _build_ffmpeg_cmd(
        shot_files, output_path=output_path, width=width, height=height,
    )
    run_ffmpeg_with_retry(cmd)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise FileNotFoundError(f"ffmpeg produced no output: {output_path}")
    return output_path


if __name__ == "__main__":
    render_video()
