import os
from pathlib import Path

# Create a video from screenshots only
APP_DIR = Path(__file__).resolve().parent
output_path = APP_DIR / "/out.mp4"

os.system(
    f"ffmpeg -y -loop 1 -t 3 -i shot1.png -loop 1 -t 3 -i shot2.png "
    f"-filter_complex \"[0:v][1:v]concat=n=2:v=1:a=0[v]\" "
    f"-map \"[v]\" {output_path}"
)
