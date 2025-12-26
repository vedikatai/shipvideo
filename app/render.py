import os
from pathlib import Path

# Create a video from screenshots only
APP_DIR = Path(__file__).resolve().parent
output_path = APP_DIR / "out.mp4"
shot1_path = APP_DIR / "shot1.png"
shot2_path = APP_DIR / "shot2.png"

os.system(
    f"ffmpeg -y "
    f"-loop 1 -t 3 -i {shot1_path} "
    f"-loop 1 -t 3 -i {shot2_path} "
    f'-filter_complex "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p" '
    f"-r 30 "
    f"-c:v libx264 "
    f"-profile:v baseline "
    f"-level 3.0 "
    f"-pix_fmt yuv420p "
    f"-movflags +faststart "
    f"{output_path}"
)
