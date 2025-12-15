import os
os.system(
    "ffmpeg -loop 1 -t 3 -i shot1.png -loop 1 -t 3 -i shot2.png "
    "-i narration.mp3 -filter_complex \"[0:v][1:v]concat=n=2:v=1:a=0[v]\" "
    "-map \"[v]\" -map 2:a out.mp4"
)
