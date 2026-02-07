
Here’s a focused B2B roadmap and customer journey, based on how your engine works today.

---

# ShipVideo B2B: Product Roadmap & Customer Journey

## Overview: How Customers Use ShipVideo

ShipVideo runs as a **GitHub webhook service**. When a customer opens or updates a PR, GitHub sends a webhook to your service. Your service records their preview URL, generates a video, and posts it as a PR comment. The customer does **not** need to modify their app code; they integrate once and then use it via GitHub.

---

## End-to-End Flow: What Happens When a Customer Pushes a PR

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CUSTOMER'S WORLD (their repo, their CI, their preview)                          │
└─────────────────────────────────────────────────────────────────────────────────┘

  1. Developer pushes PR
       │
       ▼
  2. Customer's CI (Vercel, Netlify, etc.) deploys preview
       │
       ▼
  3. GitHub fires webhook to ShipVideo
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SHIPVIDEO'S WORLD (your cloud)                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

  4. ShipVideo receives webhook → extracts repo, PR#, branch
       │
       ▼
  5. Wait for preview ready (poll URL until 200)
       │
       ▼
  6. PR Analyzer: fetch diff → generate steps (what to click, what to record)
       │
       ▼
  7. Capture: Playwright visits preview URL, runs steps, records screenshots
       │
       ▼
  8. Render: FFmpeg stitches video (optionally + TTS)
       │
       ▼
  9. Upload to R2/S3 → get public URL
       │
       ▼
  10. Post comment on PR with video link
       │
       ▼
  Customer sees video in their PR 🎬
```

---

## 1. How Does Their Code Connect?

They **do not** need ShipVideo code in their app. Integration is configuration + GitHub:

| What they do | Where | Why |
|--------------|--------|-----|
| Add a **GitHub webhook** | GitHub repo settings | Sends PR events to your URL when PRs are opened/updated |
| Add `project_config.json` to repo | Repo root (or config elsewhere) | Tells ShipVideo where the preview URL is and how to build it |
| Optionally: ShipVideo GitHub App | GitHub App marketplace | OAuth + one-click install; manages webhook for them |

### Config file (`project_config.json` or `.shipvideo/config.json`)

```json
{
  "project_name": "acme-dashboard",
  "preview_url_template": "https://acme-app-git-{branch_slug}-acme.vercel.app",
  "preview_ready_timeout_seconds": 300,
  "deployment_delay_seconds": 120
}
```

This is **their only required “code” connection**: a small config file describing where their preview lives. No ShipVideo SDK or runtime in their app.

---

## 2. Where Does the Video Request Come From?

- **Trigger:** PR opened, synchronized, or reopened.
- **Mechanism:** GitHub webhook → `POST /webhook` to your ShipVideo API.
- **Auth:** Webhook secret (HMAC) validates the request.
- **Optional:** Manual trigger (e.g. re-run from PR comment or a ShipVideo UI).

---

## 3. Where Do They See the Video?

Today: **PR comment only**.

```
PR #42: Add billing settings toggle
─────────────────────────────────────

🎬 Auto-generated demo video

https://videos.shipvideo.io/abc123.mp4

[▶ Video plays inline in GitHub]
```

B2B extensions:

| Surface | Description |
|---------|-------------|
| **PR comment** | Default, works today |
| **ShipVideo dashboard** | Per-project, per-PR video list, status, logs |
| **Slack/Teams** | Webhook when video is ready, link to PR |
| **Embed in docs** | Public/private links for changelogs, release notes |

---

## 4. Where Can They Do Edits?

Current behavior: fully automatic. No edit UI yet.

For B2B, you’d add editing capabilities:

| Capability | Where it lives | Notes |
|------------|----------------|-------|
| **Edit flow steps** | ShipVideo dashboard | Override AI-generated steps: add/remove clicks, change selectors, reorder |
| **Edit narration** | Dashboard or config | Adjust script before/after TTS |
| **Retake** | PR comment or dashboard | “Regenerate video” with same or modified config |
| **Custom branding** | Project settings | Logo, outro, intro |

A natural path: **PR comment** → “Edit & regenerate” → **ShipVideo dashboard** where they tweak steps/narration → “Regenerate” → updated comment or new URL.

---

## 5. Do They Need Code Access?

Yes, but in a controlled way:

| Access | What you need | Why |
|--------|----------------|-----|
| **PR diff** | GitHub token (user or GitHub App) | Analyze changes and infer recording steps |
| **Preview URL** | Config + optional deployment API | Know where to record; optional integration with Vercel/Netlify for status |
| **No repo clone** | Not required | You only need PR diff and metadata, not full checkout |

### How they give access

**Option A: GitHub App (recommended for B2B)**

- Install from GitHub App marketplace.
- Per-repo or org-wide installation.
- You get: webhook events, PR API, file content for PR diff.
- They grant read access to PRs and metadata (no direct code write).

**Option B: Personal Access Token**

- Customer creates PAT with `repo` scope.
- Stores it in ShipVideo project settings.
- Simpler, but less structured and harder to audit.

---

## 6. High-Level B2B Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CUSTOMER                                                               │
│  • GitHub repo (their product)                                          │
│  • CI/CD: Vercel / Netlify / custom → deploys preview per PR            │
│  • project_config.json (preview URL template)                           │
│  • GitHub webhook → points to ShipVideo                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Webhook (PR opened/updated)
                                    │ GitHub API (PR diff)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SHIPVIDEO (SaaS)                                                       │
│  • Webhook endpoint                                                     │
│  • Job queue (Redis/RQ)                                                 │
│  • PR analyzer → generate steps                                         │
│  • Capture (Playwright) → visit preview, run steps                      │
│  • Render → FFmpeg + TTS                                                │
│  • Storage → R2/S3                                                      │
│  • Post comment to PR                                                   │
│  • Dashboard (optional): projects, videos, edit, regenerate             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Video URL in comment
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  CUSTOMER SEES                                                          │
│  • PR comment with video link                                           │
│  • (Optional) Dashboard for edit/regenerate                             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Recommended Roadmap

### Phase 1: Single-tenant B2B (Weeks 1–2)

- Multi-tenant config: per-customer `project_config`, API keys or GitHub App.
- Hosted webhook URL per customer (or shared URL with project routing).
- Cloud storage per customer or shared with clear URLs.
- Onboarding: setup guide + sample `project_config.json`.

### Phase 2: Self-serve (Weeks 3–4)

- ShipVideo GitHub App with one-click install.
- Project settings UI: preview URL template, timeouts, auth hints.
- Basic dashboard: list projects, PRs, videos, status.

### Phase 3: Edit & Polish (Weeks 5–6)

- Flow editor: add/remove/edit steps before regenerate.
- Narration editing.
- “Regenerate” from PR comment or dashboard.
- Notifications (Slack, etc.).

### Phase 4: Scale (Ongoing)

- Organization-level settings and billing.
- SSO, audit logs, SOC2 considerations.
- Integration with Netlify, Railway, and other preview providers.

---

## 8. Summary

| Question | Answer |
|----------|--------|
| How will they use it? | Open/update PR → automatic video in PR comment |
| Where does the request come from? | GitHub webhook when PR is opened/updated |
| How does their code connect? | Webhook + `project_config.json`; no ShipVideo code in their app |
| Where do they see the video? | PR comment today; dashboard + Slack/Teams later |
| Where can they edit? | Future: ShipVideo dashboard for steps and narration |
| Do they need code access? | Yes: PR diff via GitHub API; no repo clone needed |

