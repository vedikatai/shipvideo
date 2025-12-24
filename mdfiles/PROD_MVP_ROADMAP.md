# Production MVP Roadmap - Step by Step

## 🎯 Goal
Transform the current proof-of-concept into a **testable MVP** that real users can use with GitHub PRs.

## 📋 Strategy: Backend First, UI Later
**Answer: Don't build a UI yet.** Focus on making the backend production-ready first. Users can test via GitHub PRs directly. Add a UI later if needed for monitoring/debugging.

---

## Phase 1: Make It Actually Work for Real Users (Week 1-2)

### Step 1: Fix Video Storage & Access ✅ **START HERE**
**Problem:** Videos are only accessible on localhost. Users can't see them.

**What to do:**
1. Set up cloud storage (AWS S3, Google Cloud Storage, or Cloudflare R2)
2. Upload videos to cloud storage after rendering
3. Update `github_comment.py` to use cloud URL instead of localhost
4. Make videos publicly accessible (or use signed URLs)

**Files to modify:**
- `app/github_comment.py` - Change VIDEO_URL to cloud URL
- `app/render.py` - Add upload function after video creation
- Create `app/storage.py` - Handle cloud storage uploads

**Test:** Create a PR, verify video URL works from anywhere

---

### Step 2: Add Environment Configuration ✅
**Problem:** Everything is hardcoded (URLs, secrets, etc.)

**What to do:**
1. Create `.env.example` with all required variables
2. Use `python-dotenv` to load environment variables
3. Replace all hardcoded values with env vars:
   - `STAGING_URL` (instead of hardcoded localhost:3000)
   - `GITHUB_TOKEN`
   - `GITHUB_WEBHOOK_SECRET`
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET` (or equivalent)
   - `BASE_URL` (for video URLs)

**Files to modify:**
- `app/webhook.py` - Load env vars
- `app/capture.py` - Use STAGING_URL from env
- `app/github_comment.py` - Use BASE_URL from env
- Create `.env.example` template

**Test:** Run locally with different .env files

---

### Step 3: Add Basic Job Status Tracking ✅
**Problem:** No way to know if a job is running, failed, or completed.

**What to do:**
1. Add simple in-memory job status (dict with job_id → status)
2. Create job_id from PR number + timestamp
3. Update status: `pending` → `processing` → `completed` / `failed`
4. Add endpoint: `GET /job/{job_id}/status`
5. Post initial comment: "🎬 Generating demo video... (Job ID: {job_id})"
6. Update comment when done with video URL

**Files to modify:**
- `app/webhook.py` - Generate job_id, store status
- `app/job_runner.py` - Update status during pipeline
- `app/github_comment.py` - Post initial comment, update with video
- Add `GET /job/{job_id}/status` endpoint

**Test:** Check job status via API, see comments update in PR

---

### Step 4: Improve Error Handling & Reporting ✅
**Problem:** Errors are silent. Users don't know what went wrong.

**What to do:**
1. Catch errors at each stage (capture, render, upload, comment)
2. Post error comment to PR if job fails
3. Add structured logging (use Python `logging` module)
4. Log errors with context (PR number, job_id, error type)

**Files to modify:**
- `app/job_runner.py` - Better error handling
- `app/webhook.py` - Catch background job errors
- `app/github_comment.py` - Add `post_error_comment()` function
- Replace `print()` with `logging.info()`, `logging.error()`

**Test:** Intentionally break something, verify error comment appears

---

## Phase 2: Make It Reliable (Week 2-3)

### Step 5: Add Proper Job Queue ✅
**Problem:** Using simple Thread. Jobs lost on restart, no retries.

**What to do:**
1. Choose a simple queue: **Redis + RQ** (easiest) or **Celery** (more features)
2. Install: `pip install redis rq`
3. Replace Thread with RQ job queue
4. Add job retry logic (retry 2-3 times on failure)
5. Store job results in Redis

**Files to modify:**
- `app/webhook.py` - Use RQ instead of Thread
- `app/job_runner.py` - Make it a proper RQ job function
- Create `app/queue.py` - Queue setup
- Update requirements.txt

**Test:** Restart server, verify jobs persist

---

### Step 6: Add Health Checks & Timeouts ✅
**Problem:** No way to know if staging is ready, jobs can hang forever.

**What to do:**
1. Add staging URL health check before capture
2. Wait up to 5 minutes for staging to be ready (poll every 30s)
3. Add timeout to capture (max 5 minutes)
4. Add timeout to render (max 2 minutes)
5. Fail gracefully if timeouts hit

**Files to modify:**
- `app/capture.py` - Add health check function, timeout handling
- `app/job_runner.py` - Check staging before capture

**Test:** Test with staging down, verify timeout error

---

### Step 7: Improve Video Quality ✅
**Problem:** Videos are just static screenshots, no transitions, no audio.

**What to do:**
1. Add smooth transitions between screenshots (fade, slide)
2. Add basic TTS narration (use `gTTS` or `pyttsx3` for free, or `elevenlabs` for better quality)
3. Generate simple script: "This PR adds a new feature. Let's see it in action."
4. Sync audio with video duration
5. Improve video resolution (1080p instead of default)

**Files to modify:**
- `app/render.py` - Add transitions, audio mixing
- `app/tts.py` - Implement TTS (was empty)
- `app/job_runner.py` - Call TTS before render

**Test:** Generate video, verify it has audio and smooth transitions

---

## Phase 3: Make It Smarter (Week 3-4)

### Step 8: Basic PR Analysis ✅
**Problem:** Doesn't understand what changed in PR. Always records same flow.

**What to do:**
1. Fetch PR diff using GitHub API
2. Analyze changed files (frontend files? backend? config?)
3. Extract basic info: "This PR modifies `Button.tsx` and `HomePage.tsx`"
4. Use simple heuristics:
   - If frontend files changed → record UI
   - If specific component changed → try to find that component
5. Generate basic narration: "This PR updates the button component..."

**Files to modify:**
- Create `app/pr_analyzer.py` - Analyze PR diff
- `app/job_runner.py` - Call analyzer, pass context to capture
- `app/capture.py` - Use PR context to determine what to record

**Test:** Create PRs with different changes, verify different flows

---

### Step 9: Dynamic Flow Detection ✅
**Problem:** Hardcoded flow in config. Can't adapt to PR changes.

**What to do:**
1. Read `shipvideo.config.json` as base flow registry
2. Map PR changes to flow steps:
   - If `Button.tsx` changed → look for button-related steps
   - If `HomePage.tsx` changed → look for homepage steps
3. Build flow dynamically based on PR analysis
4. Fallback to default flow if no match

**Files to modify:**
- `app/capture.py` - Accept dynamic flow from job_runner
- `app/job_runner.py` - Build flow from PR analysis
- Keep `shipvideo.config.json` as fallback/default

**Test:** Different PRs trigger different recording flows

---

## Phase 4: Polish & Deploy (Week 4-5)

### Step 10: Add Monitoring & Logging ✅
**Problem:** No visibility into what's happening.

**What to do:**
1. Set up structured logging (JSON format)
2. Add basic metrics:
   - Jobs started/completed/failed
   - Average job duration
   - Video generation success rate
3. Use logging service: **Sentry** (free tier) for errors, or just log to file
4. Add simple dashboard endpoint: `GET /health` with stats

**Files to modify:**
- All files - Replace print() with structured logging
- `app/webhook.py` - Add `/health` endpoint
- Create `app/metrics.py` - Track metrics

**Test:** Check logs, verify metrics endpoint

---

### Step 11: Dockerize & Deploy ✅
**Problem:** Not containerized, hard to deploy.

**What to do:**
1. Create `Dockerfile`
2. Create `docker-compose.yml` (app + Redis)
3. Deploy to:
   - **Railway** (easiest, free tier)
   - **Render** (free tier)
   - **Fly.io** (free tier)
   - Or AWS/GCP if you prefer
4. Set up environment variables in deployment platform
5. Configure GitHub webhook to point to deployed URL

**Files to create:**
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

**Test:** Deploy, create PR, verify it works in production

---

### Step 12: Add Simple Status Page (Optional) ✅
**Problem:** No way to see job status without checking PR.

**What to do:**
1. Create simple HTML page: `GET /status`
2. Show recent jobs, their status, PR links
3. No fancy UI needed - just a table
4. This is optional - PR comments are enough for MVP

**Files to create:**
- `app/status_page.py` - Simple HTML template
- `app/webhook.py` - Add `/status` endpoint

**Test:** Visit status page, see job history

---

## 🎯 MVP Definition (What "Done" Looks Like)

Your MVP is ready when:

✅ **Users can:**
- Open a PR → automatically get a demo video comment
- Click video URL → watch video from anywhere (cloud storage)
- See job status in PR comments
- Get error messages if something fails

✅ **System can:**
- Handle multiple PRs concurrently
- Retry failed jobs
- Store videos in cloud (accessible anywhere)
- Work with real staging URLs
- Generate videos with audio and transitions
- Analyze PRs to determine what to record

✅ **You can:**
- Deploy to production
- Monitor job status
- Debug issues via logs
- Scale if needed

---

## 🚫 What NOT to Build Yet

**Skip for now:**
- ❌ Full web UI/dashboard (PR comments are enough)
- ❌ User authentication
- ❌ Multiple project support
- ❌ Advanced AI/LLM features
- ❌ Video editing UI
- ❌ Analytics dashboard

**Build these later** (after MVP is proven):
- Web UI for monitoring (if needed)
- Advanced PR analysis with LLM
- Custom flow builder UI
- User management

---

## 📊 Priority Order

**Must Have (Week 1-2):**
1. Cloud storage (Step 1)
2. Environment config (Step 2)
3. Job status (Step 3)
4. Error handling (Step 4)

**Should Have (Week 2-3):**
5. Job queue (Step 5)
6. Health checks (Step 6)
7. Better videos (Step 7)

**Nice to Have (Week 3-4):**
8. PR analysis (Step 8)
9. Dynamic flows (Step 9)

**Polish (Week 4-5):**
10. Monitoring (Step 10)
11. Deploy (Step 11)
12. Status page (Step 12)

---

## 🚀 Quick Start: Your First Task

**Start with Step 1: Cloud Storage**

1. Sign up for AWS S3 (or Cloudflare R2 - free tier)
2. Create a bucket
3. Install: `pip install boto3` (for S3) or `pip install boto3` with R2
4. Create `app/storage.py`:
   ```python
   import boto3
   from pathlib import Path
   
   def upload_video(local_path: Path) -> str:
       # Upload to S3/R2
       # Return public URL
   ```
5. Update `app/render.py` to call `upload_video()` after creating video
6. Update `app/github_comment.py` to use cloud URL

**Test it:** Generate a video, verify URL works from your phone/browser.

---

## 💡 Tips

- **One step at a time** - Don't jump ahead. Complete each step fully.
- **Test after each step** - Make sure it works before moving on.
- **Keep it simple** - MVP means "minimum" - don't over-engineer.
- **Use existing services** - Don't build what you can use (S3, Redis, etc.)
- **PR comments are your UI** - Users interact via GitHub, not a web app.

---

## 📝 Notes

- This roadmap assumes ~20 hours/week of development
- Adjust timeline based on your availability
- Each step should take 1-3 days
- Focus on getting ONE thing working end-to-end before moving on

**Remember:** The goal is a **testable MVP**, not a perfect product. Ship it, get feedback, then iterate.

