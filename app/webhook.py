from fastapi import FastAPI, Header, Request
import hmac, hashlib, json, os, asyncio
from pathlib import Path
from fastapi.responses import StreamingResponse
from threading import Thread
from app.job_runner import run_pipeline
from app.github_comment import comment_on_pr
from app.preview_url_resolver import get_preview_url, wait_for_preview_ready
from app.config import load_config
import time
from app.pr_analyzer import analyze_pr
from app.llm_guards import check_already_ran, record_run, get_budget_status

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
# Budget / spend (Azure or local)
# -------------------------
@app.get("/budget-status")
def budget_status():
    """Return current spend and limit (Azure Cost Management or local). For monitoring."""
    return get_budget_status()


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
    event_type = event.get("action", "unknown")
    repo = event.get("repository", {}).get("full_name", "unknown")
    
    # Check if this is a PR event
    pr_title = None
    if "pull_request" in event:
        pr = event["pull_request"]
        pr_number = pr["number"]
        pr_title = pr.get("title")
        print(f"📥 PR Event: {event_type} - {repo}#{pr_number}", flush=True)
    else:
        print(f"📥 Webhook: {event_type} on {repo} (not a PR event)", flush=True)
        return {"status": "ignored"}
    
    # Trigger on opened PR or redelivery (action might be missing or different)
    action = event.get("action")
    
    # Allow opened, synchronize (updates), reopened, or redelivery (no action or ready_for_review)
    allowed_actions = ["opened", "synchronize", "reopened", "ready_for_review"]
    if action and action not in allowed_actions:
        print(f"ℹ️ Ignoring PR action: {action}")
        return {"status": "ignored"}
    
    # If action is missing (redelivery), allow it
    if not action:
        print("ℹ️ No action specified (likely redelivery), proceeding with pipeline")

    repo_full_name = repo  # Already extracted above
    pr_branch = (pr.get("head") or {}).get("ref") if "pull_request" in event else None
    commit_sha = ((pr.get("head") or {}).get("sha") or "") if "pull_request" in event else ""

    def background_job():
        try:
            print("🚀 Background job started", flush=True)

            if check_already_ran(repo_full_name, pr_number, commit_sha):
                print("[llm-guards] Skipping duplicate run for this PR+commit", flush=True)
                return
            record_run(repo_full_name, pr_number, commit_sha)

            # Wait for deployment to finish (simple fixed delay)
            delay = load_config().get("deployment_delay_seconds", 0)
            if delay > 0:
                print(f"⏳ Waiting {delay}s for deployment to finish...", flush=True)
                time.sleep(delay)
                print("✅ Delay complete, starting pipeline", flush=True)
            
            print("🔍 Resolving preview URL...", flush=True)
            try:
                preview_url = get_preview_url(pr_number=pr_number, branch=pr_branch)
            except ValueError as e:
                print(f"❌ Cannot get preview URL: {e}", flush=True)
                error_message = f"⚠️ **Demo video not generated**\n\n{e}"
                comment_on_pr(repo_full_name, pr_number, None, error_message)
                return
            except Exception as e:
                print(f"❌ Error getting preview URL: {type(e).__name__}: {e}", flush=True)
                raise

            if not wait_for_preview_ready(preview_url):
                error_message = (
                    "⚠️ **Demo video not generated**\n\n"
                    "Preview deployment did not become ready in time. "
                    "Try re-running after your preview (e.g. Vercel) has finished building."
                )
                comment_on_pr(repo_full_name, pr_number, None, error_message)
                return

            flow = asyncio.run(
                analyze_pr(
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    pr_title=pr_title,
                    staging_url=preview_url,
                )
            )
            steps = flow.get("steps") or [{"action": "screenshot"}]
            budget_exceeded = flow.get("budget_exceeded", False)

            video_url = run_pipeline(
                pr_number=pr_number,
                preview_url=preview_url,
                steps=steps,
            )
            print("💬 Posting comment to PR", flush=True)
            extra_note = None
            if budget_exceeded:
                extra_note = "ℹ️ **Monthly budget limit reached.** This demo used fallback steps (no LLM)."
            comment_on_pr(repo_full_name, pr_number, video_url, extra_note=extra_note)
            print("✅ Background job completed successfully", flush=True)
        except Exception as e:
            print(f"❌ Background job failed: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    Thread(target=background_job).start()
    print("⏳ Pipeline job started in background", flush=True)

    return {"status": "accepted"}
