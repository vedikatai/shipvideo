# Code Quality Analysis - Separation of Concerns

## ✅ **Good Separation (Well-Organized)**

### 1. **`storage.py`** - Storage Layer
**Responsibility:** R2 cloud storage operations
- ✅ Single responsibility: Upload, list, cleanup videos
- ✅ Well-encapsulated: All R2 logic in one place
- ✅ Reusable functions

### 2. **`github_comment.py`** - GitHub API Layer,
**Responsibility:** GitHub API interactions
- ✅ Single responsibility: Post comments to PRs
- ✅ Clean interface: Takes repo, PR number, video URL
- ✅ No mixing with other concerns.

### 3. **`capture.py`** - Screenshot Capture
**Responsibility:** Browser automation for screenshots
- ✅ Single responsibility: Playwright screenshot capture
- ✅ Standalone script: Can run independently

### 4. **`render.py`** - Video Rendering
**Responsibility:** Video encoding/rendering
- ✅ Single responsibility: FFmpeg video creation
- ✅ Standalone script: Can run independently

### 5. **`job_runner.py`** - Pipeline Orchestration
**Responsibility:** Coordinates the video pipeline
- ✅ Single responsibility: Orchestrates capture → render → upload
- ✅ Clean flow: Sequential steps, clear error handling

---

## ⚠️ **Issues Found (Needs Improvement)**

### 1. **`webhook.py`** - Multiple Responsibilities ❌

**Current Issues:**
- 🔴 **HTTP Server Setup** (lines 9-19)
- 🔴 **Webhook Signature Verification** (lines 72-74)
- 🔴 **Webhook Event Parsing/Validation** (lines 83-106)
- 🔴 **Video Serving Endpoint** (lines 31-67) - **Potentially obsolete** (using R2 now)
- 🔴 **Background Job Orchestration** (lines 110-120)

**Problems:**
1. Too many responsibilities in one file
2. Video serving endpoint might be unused (videos are on R2)
3. Webhook validation logic mixed with HTTP handling
4. Hard to test individual components

**Recommended Refactoring:**
```
app/
  ├── webhook.py          # HTTP server setup only
  ├── webhook_handler.py  # Webhook validation & parsing
  ├── video_server.py     # Video serving (if still needed)
  └── background_job.py   # Background job execution
```

### 2. **`jobs.py`** - Duplicate Code ❌

**Issue:** Duplicates functionality of `github_comment.py`
- Same purpose: Post video to GitHub PR
- Different implementation (uses env var for repo)
- **Recommendation:** Delete `jobs.py` or merge into `github_comment.py`

### 3. **`tts.py`** - Empty File ❌

**Issue:** Empty file with no implementation
- **Recommendation:** Remove or implement TTS functionality

---

## 📊 **Separation of Concerns Score: 7/10**

### What's Good:
✅ Core business logic is well-separated
✅ Storage, GitHub API, and pipeline are isolated
✅ Each module has a clear, single purpose
✅ Easy to understand and maintain

### What Needs Work:
❌ `webhook.py` violates single responsibility principle
❌ Dead/unused code (`jobs.py`, video serving endpoint)
❌ Some mixing of HTTP handling with business logic

---

## 🔧 **Recommended Refactoring**

### Priority 1: Split `webhook.py`

**Create `app/webhook_handler.py`:**
```python
# Webhook validation and event parsing
def verify_webhook_signature(signature, payload, secret):
    ...

def parse_pr_event(event):
    """Extract PR info from webhook event"""
    ...

def should_process_event(event):
    """Determine if event should trigger pipeline"""
    ...
```

**Update `app/webhook.py`:**
```python
# Only HTTP server and routing
from app.webhook_handler import verify_webhook_signature, parse_pr_event
from app.background_job import run_background_job

@app.post("/webhook")
async def webhook(...):
    # Just routing, delegate to handlers
    ...
```

### Priority 2: Remove Dead Code

1. **Delete `jobs.py`** (duplicate of `github_comment.py`)
2. **Remove video serving endpoint** from `webhook.py` (if R2 is working)
3. **Delete or implement `tts.py`**

### Priority 3: Extract Background Job

**Create `app/background_job.py`:**
```python
# Background job execution
def execute_video_pipeline(repo_full_name, pr_number):
    """Run pipeline and post result to PR"""
    video_url = run_pipeline()
    comment_on_pr(repo_full_name, pr_number, video_url)
```

---

## ✅ **Current Architecture (Visual)**

```
┌─────────────┐
│  webhook.py │  ← HTTP Server + Webhook Handling (TOO MUCH)
└──────┬──────┘
       │
       ├─→ verify_signature()
       ├─→ parse_event()
       ├─→ serve_video()  ← Potentially obsolete
       └─→ background_job()  ← Should be extracted
            │
            └─→ job_runner.py
                 ├─→ capture.py
                 ├─→ render.py
                 └─→ storage.py
                      └─→ R2 Upload
                 
            └─→ github_comment.py
                 └─→ GitHub API
```

---

## 🎯 **Target Architecture (Improved)**

```
┌─────────────┐
│  webhook.py │  ← HTTP Server Only
└──────┬──────┘
       │
       ├─→ webhook_handler.py  ← Validation & Parsing
       │    ├─→ verify_signature()
       │    └─→ parse_pr_event()
       │
       └─→ background_job.py  ← Job Execution
            │
            └─→ job_runner.py
                 ├─→ capture.py
                 ├─→ render.py
                 └─→ storage.py
                 
            └─→ github_comment.py
```

---

## 📝 **Summary**

**Overall:** Your codebase has **good separation of concerns** for the core business logic. The main issue is `webhook.py` doing too much.

**Action Items:**
1. ✅ Keep: `storage.py`, `github_comment.py`, `job_runner.py`, `capture.py`, `render.py`
2. ⚠️ Refactor: Split `webhook.py` into smaller modules
3. 🗑️ Remove: `jobs.py`, video serving endpoint (if unused), `tts.py` (if empty)

**For MVP:** Current structure is **acceptable** - refactoring can wait until you have more features.

**For Production:** Split `webhook.py` before scaling.

