"""
Video processor: converts a .webm recorded by Playwright into a web-compatible
.mp4 using FFmpeg.

Encoding settings match the existing render.py pipeline for consistency:
- libx264, baseline profile, yuv420p, +faststart
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def convert_webm_to_mp4(webm_path: Path, output_dir: Path) -> Path:
    """
    Convert a Playwright-recorded .webm to .mp4.

    Args:
        webm_path: Path to the source .webm file.
        output_dir: Directory where out.mp4 will be written.

    Returns:
        Path to the produced .mp4 file.

    Raises:
        FileNotFoundError: webm_path does not exist.
        subprocess.CalledProcessError: FFmpeg conversion failed.
    """
    if not webm_path.exists():
        raise FileNotFoundError(f"webm not found: {webm_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = output_dir / "out.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-i", str(webm_path),
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",

        "-an",
        str(mp4_path),
    ]

    print(f"[video_processor] converting webm→mp4 src={webm_path.name}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[video_processor] ffmpeg stderr: {result.stderr.strip()}", flush=True)
    result.check_returncode()

    print(f"[video_processor] mp4 ready path={mp4_path}", flush=True)
    return mp4_path
