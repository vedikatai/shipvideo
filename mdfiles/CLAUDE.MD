¸**YES, WE'RE ALIGNED. HERE'S WHAT WE DECIDED:**

---

## ✅ THE MVP ROADMAP (Final)

### What we're building:
**Auto-generate demo videos from PRs using GitHub webhooks + template system**

### Core architecture:
1. **GitHub webhook** triggers on PR open (NOT Vercel webhook)
2. Records from **static deployed URL** `https://shipvideo-demo.vercel.app` (good enough for MVP)
3. Uses **pre-defined templates** (not LLM-generated steps)
4. **Simple keyword matching** to pick template (PR title → template)
5. **No LLM for MVP** (just if/else matching: "billing" → billing template)

### What's working now:
- ✅ GitHub webhook receives PR events
- ✅ Records from deployed app URL
- ✅ Generates video (FFmpeg)
- ✅ Uploads to R2
- ✅ Posts comment to PR

### What needs fixing:
- ❌ Hardcoded `button#new-feature` clicks (breaks on other PRs)
- ❌ No template system yet

---

## 🎯 NEXT STEPS (This Week)

### Step 1: Add template system (Python dicts, not DB)
```python
TEMPLATES = {
    "billing": [
        {"action": "goto", "url": "/settings/billing"},
        {"action": "click", "selector": "#download-bill"}
    ],
    "default": [
        {"action": "screenshot"}
    ]
}
```'

### Step 2: Add keyword matching
```python
def match_template(pr_title):
    if "billing" in pr_title.lower():
        return TEMPLATES["billing"]
    return TEMPLATES["default"]
```

### Step 3: Update capture to use templates
```python
def capture_demo(preview_url, steps):
    for step in steps:
        if step["action"] == "click":
            page.click(step["selector"])
        elif step["action"] == "screenshot":
            page.screenshot()
```

---

## 🚫 WHAT WE'RE NOT DOING (For MVP)

- ❌ Vercel webhook integration
- ❌ PR-specific preview URLs (pr-123.vercel.app)
- ❌ LLM classification
- ❌ Database for templates
- ❌ UI for template creation
- ❌ Dynamic step generation

---

## 📋 THE SEQUENCE (Phases)

**Phase 1 (NOW - This Week):**
- Hardcoded templates in code
- Keyword matching (if/else)
- Test with 3 PR types

**Phase 2 (Next Week):**
- Move templates to database
- Build simple UI to create templates
- Test with real user

**Phase 3 (Week 3):**
- Add LLM for better matching
- Handle "no match" case
- Polish UX

---

## ✅ ARE WE ALIGNED?

**YES** if you agree:
1. GitHub webhook only (no Vercel)
2. Static app URL (no PR previews)
3. Hardcoded templates first (no LLM yet)
4. Ship this week, iterate next week

**Tell me if any of these 4 points are wrong.**