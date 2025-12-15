import os

# Create a video from screenshots only
os.system(
    "ffmpeg -y -loop 1 -t 3 -i shot1.png -loop 1 -t 3 -i shot2.png "
    "-filter_complex \"[0:v][1:v]concat=n=2:v=1:a=0[v]\" "
    "-map \"[v]\" out.mp4"
)
