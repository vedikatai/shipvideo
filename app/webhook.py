from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse
from pathlib import Path
import hmac, hashlib, json, os

app = FastAPI()

GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "secret")

# Path to the generated video
BASE_DIR = Path(__file__).resolve().parent
VIDEO_PATH = BASE_DIR / "out.mp4"

@app.get("/out.mp4")
def get_video():
    if not VIDEO_PATH.exists():
        return {"error": "Video not generated yet"}
    return FileResponse(VIDEO_PATH, media_type="video/mp4")


def verify_signature(signature, payload):
    mac = hmac.new(GITHUB_SECRET.encode(), payload, hashlib.sha256)
    return hmac.compare_digest(f"sha256={mac.hexdigest()}", signature)

@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(...)):
    body = await request.body()
    if not verify_signature(x_hub_signature_256, body):
        return {"status": "invalid signature"}
    event = json.loads(body)
    print("PR Event:", event)
    # TODO: trigger capture + render + comment
    return {"status": "ok"}
