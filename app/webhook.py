from fastapi import FastAPI, Header, Request
import hmac, hashlib, json, os
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import StreamingResponse
import math


app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# GitHub webhook secret
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "secret")

# Path to video
BASE_DIR = Path(__file__).resolve().parent
VIDEO_PATH = BASE_DIR / "out.mp4"

# Serve video

@app.get("/out.mp4")
def get_video(request: Request):
    file_path = VIDEO_PATH
    if not file_path.exists():
        return {"error": "Video not generated yet"}

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1

    if range_header:
        bytes_range = range_header.replace("bytes=", "").split("-")
        if bytes_range[0]:
            start = int(bytes_range[0])
        if bytes_range[1]:
            end = int(bytes_range[1])
    length = end - start + 1

    def iterfile(path, start, length):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk_size = min(1024*1024, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": "video/mp4",
    }
    return StreamingResponse(iterfile(file_path, start, length), status_code=206, headers=headers)

# Verify GitHub signature
def verify_signature(signature, payload):
    mac = hmac.new(GITHUB_SECRET.encode(), payload, hashlib.sha256)
    return hmac.compare_digest(f"sha256={mac.hexdigest()}", signature)

# Webhook endpoint
@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(...)):
    body = await request.body()
    if not verify_signature(x_hub_signature_256, body):
        return {"status": "invalid signature"}
    event = json.loads(body)
    print("PR Event:", event)
    # TODO: trigger capture/render job here
    return {"status": "ok"}
