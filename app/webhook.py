from fastapi import FastAPI, Header, Request
import hmac, hashlib, json
import os

app = FastAPI()
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "secret")

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
    # TODO: trigger job
    return {"status": "ok"}
