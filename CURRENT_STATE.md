# Current Project State Summary

## What This Project Does

**ShipVideo Engine** is an automated demo video generator that creates videos from GitHub pull requests. The vision is to automatically record UI interactions, add narration, and post polished demo videos to PRs.

**Current MVP Level:** Basic proof-of-concept that demonstrates the core pipeline.

---

## How It Currently Works

### The Flow

```
1. GitHub PR Event → 2. Webhook Handler → 3. Background Job → 4. Capture → 5. Render → 6. Post Comment
```

#### Step-by-Step Breakdown:

**1. GitHub Webhook Trigger** (`app/webhook.py`)
- Listens for GitHub PR events (opened, synchronized, reopened)
- Validates webhook signature for security
- Extracts PR number and repository name
- Triggers background job in a separate thread

**2. Background Job Runner** (`app/job_runner.py`)
- Orchestrates the pipeline sequentially
- Runs capture script → then render script
- Handles errors and prints logs

**3. Capture Phase** (`app/capture.py`)
- Uses Playwright to launch headless Chrome
- Navigates to hardcoded URL: `http://localhost:3000`
- Takes screenshot: `shot1.png`
- Clicks hardcoded button: `button#new-feature`
- Takes screenshot: `shot2.png`
- Closes browser

**4. Render Phase** (`app/render.py`)
- Uses FFmpeg to combine screenshots into video
- Creates `out.mp4` (6 seconds: 3s per screenshot)
- No audio, no transitions, just static images

**5. Post to PR** (`app/github_comment.py`)
- Uses GitHub API to post comment
- Includes hardcoded video URL: `http://localhost:8000/out.mp4`
- Comment format: "🎬 **Auto-generated demo video**\n\n{URL}"

**6. Video Serving** (`app/webhook.py`)
- FastAPI endpoint serves video file
- Supports HTTP range requests (video streaming)
- Returns 206 Partial Content for proper video playback

---

## Current Architecture

```
┌─────────────────┐
│  GitHub PR      │
│  (Webhook)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  FastAPI Server │
│  /webhook       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────┐
│  Background     │─────▶│  Capture     │
│  Thread         │      │  (Playwright)│
└────────┬────────┘      └──────┬───────┘
         │                       │
         │                       ▼
         │              ┌──────────────┐
         │              │  Render      │
         │              │  (FFmpeg)    │
         │              └──────┬───────┘
         │                     │
         ▼                     ▼
┌─────────────────┐      ┌──────────────┐
│  Post Comment   │      │  out.mp4     │
│  (GitHub API)   │      │  (local)     │
└─────────────────┘      └──────────────┘
```

---

## What's Implemented ✅

1. **GitHub Integration**
   - Webhook endpoint with signature verification
   - PR event filtering (opened, synchronize, reopened)
   - GitHub API integration for posting comments

2. **Basic Recording**
   - Playwright browser automation
   - Screenshot capture
   - Simple button clicking

3. **Video Generation**
   - FFmpeg video stitching
   - MP4 output format
   - Basic video serving with range requests

4. **Pipeline Orchestration**
   - Sequential job execution
   - Background processing
   - Basic error handling

---

## What's Missing ❌

### Critical Gaps:

1. **No PR Intelligence**
   - Doesn't analyze what changed in the PR
   - Doesn't understand which features to demo
   - Hardcoded flow (always clicks same button)

2. **No Dynamic Flow Detection**
   - Can't determine what to record based on PR changes
   - No flow registry or mapping
   - Always records the same hardcoded interaction

3. **No LLM Integration**
   - No change summarization
   - No narration script generation
   - No intelligent flow suggestions

4. **No TTS Narration**
   - `tts.py` is empty
   - Videos have no audio
   - No professional voiceover

5. **No Video Storage**
   - Videos stored locally only
   - Hardcoded localhost URL in comments
   - No cloud storage (S3/GCS)
   - Videos not accessible outside local network

6. **No Staging Integration**
   - Hardcoded `localhost:3000` URL
   - Doesn't wait for staging deployment
   - No environment detection
   - No health checks

7. **No Configuration**
   - Hardcoded URLs everywhere
   - No environment variables structure
   - No multi-environment support

8. **No Job Queue**
   - Uses simple Thread (no persistence)
   - No retry logic
   - No job status tracking
   - Can't handle multiple PRs concurrently

9. **No Error Recovery**
   - Basic try/catch only
   - No retries
   - No timeout handling
   - Errors not reported back to PR

10. **No Logging/Monitoring**
    - Uses `print()` statements
    - No structured logging
    - No metrics
    - No observability

---

## Current Limitations

### Technical Limitations:

- **Hardcoded Everything**: URLs, button selectors, flows are all hardcoded
- **Static Screenshots**: Not real video recording, just image slideshow
- **No Audio**: Silent videos
- **Localhost Only**: Videos only accessible on local machine
- **Single Flow**: Can only record one predefined interaction
- **No Intelligence**: Doesn't understand PR context
- **No Persistence**: Jobs lost if server restarts
- **No Scaling**: Can't handle multiple PRs well

### Functional Limitations:

- **Doesn't Understand PRs**: Can't determine what to record
- **No Narration**: Videos are silent
- **No Polish**: Basic stitching, no transitions/effects
- **Not Production Ready**: Missing error handling, monitoring, config

---

## Example Current Behavior

**When a PR is opened:**

1. Webhook receives event
2. System always:
   - Goes to `http://localhost:3000`
   - Takes screenshot
   - Clicks `button#new-feature` (if it exists)
   - Takes another screenshot
   - Creates 6-second silent video
3. Posts comment with `http://localhost:8000/out.mp4` (only works locally)

**Result:** Same video for every PR, regardless of what changed.

---

## What Needs to Happen Next

To make this production-ready, the system needs to:

1. **Understand the PR** → Analyze diff, identify changed features
2. **Choose the right flow** → Map changes to UI flows dynamically
3. **Record real interactions** → Actual video recording, not screenshots
4. **Add narration** → TTS with intelligent script generation
5. **Store properly** → Cloud storage with accessible URLs
6. **Handle staging** → Wait for deployment, health checks
7. **Be configurable** → Environment-based settings
8. **Be reliable** → Job queue, retries, error handling
9. **Be observable** → Logging, monitoring, metrics

---

## Technology Stack (Current)

- **Framework**: FastAPI
- **Browser Automation**: Playwright
- **Video Processing**: FFmpeg
- **GitHub Integration**: PyGithub
- **Deployment**: Not containerized (no Dockerfile)
- **Storage**: Local filesystem only
- **Queue**: Python Thread (no proper queue)

---

## Project Maturity Level

**Current: MVP / Proof of Concept** 🟡

- ✅ Core pipeline works
- ✅ Basic integration functional
- ❌ Not production-ready
- ❌ Missing critical intelligence
- ❌ Not scalable or reliable

**Target: Production MVP** 🟢

- Need: PR analysis, dynamic flows, TTS, storage, job queue
- Estimated: 4-6 weeks of focused development
- See `ROADMAP.md` for detailed plan

---

## Quick Assessment

**What works:** The basic pipeline (webhook → capture → render → comment)

**What doesn't:** Everything that makes it intelligent, reliable, and production-ready

**Bottom line:** You have a solid foundation that proves the concept works. Now it needs intelligence, polish, and infrastructure to become production-grade.
