import os
from pathlib import Path
import glob

APP_DIR = Path(__file__).resolve().parent

def render_video():
    """Create a video from screenshots. Dynamically finds all shot*.png files in order."""
    output_path = APP_DIR / "out.mp4"
    
    # Find all screenshot files in order (shot1.png, shot2.png, shot3.png, etc.)
    shot_files = sorted(glob.glob(str(APP_DIR / "shot*.png")))
    
    if not shot_files:
        raise FileNotFoundError("No screenshot files found (shot*.png)")
    
    print(f"🎬 Found {len(shot_files)} screenshots to render", flush=True)
    for i, shot in enumerate(shot_files, 1):
        print(f"   {i}. {Path(shot).name}", flush=True)
    
    # Build FFmpeg command dynamically based on number of screenshots
    if len(shot_files) == 1:
        # Single screenshot: just loop it
        shot_path = shot_files[0]
        os.system(
            f"ffmpeg -y "
            f"-loop 1 -t 3 -i {shot_path} "
            f"-r 30 "
            f"-c:v libx264 "
            f"-profile:v baseline "
            f"-level 3.0 "
            f"-pix_fmt yuv420p "
            f"-movflags +faststart "
            f"{output_path}"
        )
    else:
        # Multiple screenshots: build concat filter
        inputs = " ".join([f"-loop 1 -t 3 -i {shot}" for shot in shot_files])
        concat_inputs = "".join([f"[{i}:v]" for i in range(len(shot_files))])
        concat_filter = f'"{concat_inputs}concat=n={len(shot_files)}:v=1:a=0,format=yuv420p"'
        
        os.system(
            f"ffmpeg -y "
            f"{inputs} "
            f"-filter_complex {concat_filter} "
            f"-r 30 "
            f"-c:v libx264 "
            f"-profile:v baseline "
            f"-level 3.0 "
            f"-pix_fmt yuv420p "
            f"-movflags +faststart "
            f"{output_path}"
        )

if __name__ == "__main__":
    render_video()
