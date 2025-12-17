from fastapi import FastAPI, Header, Request
import hmac, hashlib, json, os
from pathlib import Path
from fastapi.responses import FileResponse, StreamingResponse
from threading import Thread
from app.job_runner import run_pipeline
from app.github_comment import comment_on_pr

app = FastAPI()

# CORS for frontend
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

# Video path
BASE_DIR = Path(__file__).resolve().parent
VIDEO_PATH = BASE_DIR / "out.mp4"

# -------------------------
# Serve video
# -------------------------
@app.get("/out.mp4")
def get_video(request: Request):
    if not VIDEO_PATH.exists():
        return {"error": "Video not generated yet"}

    file_size = VIDEO_PATH.stat().st_size
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1

    if range_header:
        bytes_range = range_header.replace("bytes=", "").split("-")
        if bytes_range[0]:
            start = int(bytes_range[0])
        if len(bytes_range) > 1 and bytes_range[1]:
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
    return StreamingResponse(iterfile(VIDEO_PATH, start, length), status_code=206, headers=headers)

# -------------------------
# GitHub webhook
# -------------------------
def verify_signature(signature, payload):
    mac = hmac.new(GITHUB_SECRET.encode(), payload, hashlib.sha256)
    return hmac.compare_digest(f"sha256={mac.hexdigest()}", signature)

@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(...)):
    body = await request.body()

    if not verify_signature(x_hub_signature_256, body):
        return {"status": "invalid signature"}

    event = json.loads(body)
    print("📥 PR Event received:", json.dumps(event, indent=2))

    # Only trigger on opened PR
    action = event.get("action")
    if action != "opened":
        print(f"ℹ️ Ignoring PR action: {action}")
        return {"status": "ignored"}

    pr_number = event["pull_request"]["number"]
    repo_full_name = event["repository"]["full_name"]

    def background_job():
        try:
            print("🚀 Background job started", flush=True)
            run_pipeline()
            print("💬 Posting comment to PR", flush=True)
            comment_on_pr(repo_full_name, pr_number)
            print("✅ Background job completed successfully", flush=True)
        except Exception as e:
            print(f"❌ Background job failed: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    Thread(target=background_job).start()
    print("⏳ Pipeline job started in background", flush=True)

    return {"status": "accepted"}
