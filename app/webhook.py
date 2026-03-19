from fastapi import FastAPI, Header, Request
import hmac, hashlib, json, os, asyncio
from pathlib import Path
from fastapi.responses import StreamingResponse
from threading import Thread
from app.github_comment import comment_on_pr
from app.llm_guards import check_already_ran, get_budget_status, record_run
from app.steps.pipeline import analyze_pr, run_pipeline
from app.steps.pr_extraction import fetch_pr_diff
from app.preview_url_resolver import get_preview_url, wait_for_preview_ready
from app.config import load_config
import time
from observability import init_tracing, pipeline_run_span, print_pipeline_summary, set_current_span_error
from github import Github

app = FastAPI()


@app.on_event("startup")
def on_startup():
    init_tracing()

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
VIDEO_PATH = BASE_DIR / "screenshots" / "out.mp4"

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
    def _parse_glimpse_command(comment_body: str, command_prefix: str) -> dict | None:
        body = (comment_body or "").strip()
        if not body:
            return None

        tokens = body.split()
        cmd_idx = None
        for i, t in enumerate(tokens):
            if t == command_prefix or t.startswith(command_prefix):
                cmd_idx = i
                break
        if cmd_idx is None:
            return None

        force = False
        route = None

        i = cmd_idx + 1
        while i < len(tokens):
            t = tokens[i]
            if t == "--force":
                force = True
            elif t == "--route":
                if i + 1 < len(tokens):
                    route = tokens[i + 1]
                    i += 1
            elif t.startswith("--route="):
                route = t.split("=", 1)[1]
            i += 1

        if route is not None:
            route = route.strip()
            if route and not route.startswith("/"):
                route = "/" + route
            if route == "":
                route = None

        return {"force": force, "route": route}

    def _count_patch_changed_lines(patch: str) -> int:
        plus_minus = 0
        for line in (patch or "").splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                continue
            if line.startswith("+"):
                plus_minus += 1
            elif line.startswith("-"):
                plus_minus += 1
        return plus_minus

    def _is_ui_path(path: str, include_prefixes: list[str], exclude_substrings: list[str]) -> bool:
        p = (path or "").lstrip("/")
        if not p:
            return False
        if any(sub in p for sub in exclude_substrings):
            return False
        if not include_prefixes:
            return True
        return any(p.startswith(pref.rstrip("/")) for pref in include_prefixes if pref)

    def _resolve_preview_url_for_route(preview_url: str, start_route: str | None) -> str:
        base = (preview_url or "").rstrip("/")
        if not start_route or start_route == "/":
            return base
        if not start_route.startswith("/"):
            start_route = "/" + start_route
        return base + start_route

    body = await request.body()

    if not verify_signature(x_hub_signature_256, body):
        return {"status": "invalid signature"}

    event = json.loads(body)
    repo_full_name = event.get("repository", {}).get("full_name", "unknown")
    config = load_config()
    trigger_cfg = config.get("trigger") or {}

    trigger_mode = (trigger_cfg.get("mode") or "auto").lower()
    threshold = int(trigger_cfg.get("threshold") or 5)
    comment_command = trigger_cfg.get("commentCommand") or "/glimpse"
    skip_comment = bool(trigger_cfg.get("skipComment", True))
    include_prefixes = trigger_cfg.get("include") or ["src/", "app/"]
    exclude_substrings = trigger_cfg.get("exclude") or [".test.", ".spec.", "/tests/", "/test/", "__tests__"]

    # Decide event kind + extract PR context.
    pr_number: int | None = None
    pr_title: str | None = None
    pr_branch: str | None = None
    commit_sha: str = ""
    start_route: str | None = None
    force: bool = False
    diff_files: list[dict[str, str]] | None = None

    # --- Case A: pull_request event ---
    if "pull_request" in event:
        pr = event["pull_request"]
        pr_number = pr.get("number")
        pr_title = pr.get("title")
        pr_branch = (pr.get("head") or {}).get("ref")
        commit_sha = ((pr.get("head") or {}).get("sha") or "") if pr_number is not None else ""

        action = event.get("action")
        allowed_actions = ["opened", "synchronize", "reopened", "ready_for_review"]
        if action and action not in allowed_actions:
            print(f"[webhook] ignoring PR action={action}", flush=True)
            return {"status": "ignored"}
        if not action:
            print("[webhook] PR redelivery/no action proceeding", flush=True)

        if pr_number is None:
            return {"status": "ignored"}

        # Trigger mode enforcement
        if trigger_mode == "on-demand":
            if skip_comment:
                comment_on_pr(
                    repo_full_name,
                    pr_number,
                    None,
                    error_message=(
                        f"**Demo not generated**\n\n"
                        f"On-demand mode is enabled. Comment `{comment_command}` on this PR to generate a demo."
                    ),
                )
            return {"status": "skipped"}

        if trigger_mode == "smart" and not force:
            # Lightweight pre-check: fetch PR diffs and count changed lines
            # for UI-relevant files only.
            diff_files = fetch_pr_diff(repo_full_name, pr_number)
            changed_lines = 0
            for f in diff_files:
                if _is_ui_path(f.get("path", ""), include_prefixes=include_prefixes, exclude_substrings=exclude_substrings):
                    changed_lines += _count_patch_changed_lines(f.get("patch", ""))

            if changed_lines < threshold:
                if skip_comment:
                    comment_on_pr(
                        repo_full_name,
                        pr_number,
                        None,
                        error_message=(
                            f"**Demo not generated**\n\n"
                            f"Smart mode skipped this run: UI changed lines={changed_lines} < threshold={threshold}.\n\n"
                            f"Comment `{comment_command} --force` to override."
                        ),
                    )
                return {"status": "skipped"}

    # --- Case B: issue_comment event ---
    else:
        # Only handle comments on PRs.
        if not (event.get("comment") and event.get("issue", {}).get("pull_request")):
            print(f"[webhook] ignored event for repo={repo_full_name}", flush=True)
            return {"status": "ignored"}

        comment = event["comment"]
        comment_body = comment.get("body") or ""
        parsed = _parse_glimpse_command(comment_body, comment_command)
        if not parsed:
            print(f"[webhook] ignored comment (no command) repo={repo_full_name}", flush=True)
            return {"status": "ignored"}

        pr_number = event.get("issue", {}).get("number")
        if pr_number is None:
            return {"status": "ignored"}

        force = bool(parsed.get("force", False))
        start_route = parsed.get("route")

        # Fetch PR details to resolve branch/head sha reliably.
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN not set in .env")
        pr_obj = Github(token).get_repo(repo_full_name).get_pull(int(pr_number))
        pr_title = pr_obj.title
        pr_branch = pr_obj.head.ref
        commit_sha = pr_obj.head.sha or ""

    # If we got here, we are starting a pipeline run.
    def background_job():
        run_llm_cost_usd = 0.0
        run_budget_status = None
        with pipeline_run_span() as span:
            span.set_attribute("repo", repo_full_name)
            span.set_attribute("pr_number", pr_number)
            try:
                print("\n[webhook] === PREVIEW RESOLUTION ===", flush=True)
                if check_already_ran(repo_full_name, pr_number, commit_sha):
                    print("[llm-guards] skipping duplicate run", flush=True)
                    return
                record_run(repo_full_name, pr_number, commit_sha)

                delay = config.get("deployment_delay_seconds", 0)
                if delay > 0:
                    print(f"[webhook] waiting deployment delay={delay}s", flush=True)
                    time.sleep(delay)

                preview_url = get_preview_url(pr_number=pr_number, branch=pr_branch)
                span.set_attribute("preview_url", preview_url)

                if not wait_for_preview_ready(preview_url):
                    comment_on_pr(
                        repo_full_name,
                        pr_number,
                        None,
                        error_message=(
                            "**Demo video not generated**\n\n"
                            "Preview deployment did not become ready in time. "
                            "Try re-running after your preview (e.g. Vercel) has finished building."
                        ),
                    )
                    return

                staging_url = _resolve_preview_url_for_route(preview_url, start_route)

                print("\n[webhook] === STEP GENERATION ===", flush=True)
                flow = asyncio.run(
                    analyze_pr(
                        repo_full_name=repo_full_name,
                        pr_number=pr_number,
                        pr_title=pr_title,
                        staging_url=staging_url,
                        diff_files=diff_files,
                        start_route=start_route,
                    )
                )

                steps = flow.get("steps") or [{"action": "screenshot"}]
                generation_context = flow.get("generation_context")
                budget_exceeded = flow.get("budget_exceeded", False)
                run_llm_cost_usd = float(flow.get("llm_cost_usd", 0.0) or 0.0)
                span.set_attribute("steps_generated", len(steps))

                PURPLE = "\033[35m"
                RESET = "\033[0m"
                try:
                    print(f"{PURPLE}Generated steps:{RESET}", flush=True)
                    for idx, step in enumerate(steps, start=1):
                        print(f"{PURPLE}  {idx}. {step}{RESET}", flush=True)
                except Exception:
                    pass

                print("\n[webhook] === VIDEO PIPELINE ===", flush=True)
                try:
                    video_url, capture_summary = run_pipeline(
                        pr_number=pr_number,
                        preview_url=preview_url,
                        steps=steps,
                        generation_context=generation_context,
                    )

                    summary_path = BASE_DIR / "data" / "pipeline_run_summary.json"
                    summary_path.parent.mkdir(parents=True, exist_ok=True)
                    run_summary = {
                        "pr_number": pr_number,
                        "steps_generated": len(steps),
                        "steps_succeeded": capture_summary["steps_succeeded"],
                        "steps_failed": capture_summary["steps_failed"],
                        "failure_reason": capture_summary.get("failure_reason"),
                        "cost_usd": round(flow.get("llm_cost_usd", 0.0), 4),
                    }
                    with open(summary_path, "w") as f:
                        json.dump(run_summary, f, indent=2)
                    print(f"[webhook] run summary file={summary_path.name}", flush=True)

                    try:
                        run_budget_status = get_budget_status()
                    except Exception:
                        run_budget_status = None

                    print("[webhook] posting comment to PR", flush=True)
                    extra_note = None
                    if budget_exceeded:
                        extra_note = "**Monthly budget limit reached.** This demo used fallback steps (no LLM)."
                    comment_on_pr(repo_full_name, pr_number, video_url, extra_note=extra_note)
                except Exception as e:
                    # Never leave the user with a misleading success state.
                    error_message = (
                        "**Demo video not generated**\n\n"
                        f"{type(e).__name__}: {e}\n\n"
                        "Debug context may be available in the server logs under `execution.*` JSON events."
                    )
                    comment_on_pr(repo_full_name, pr_number, None, error_message=error_message)
                    raise

            except Exception as e:
                set_current_span_error(str(e))
                print(f"[webhook] job failed: {type(e).__name__}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                raise
            finally:
                print("\n===== PIPELINE SUMMARY =====", flush=True)
                try:
                    print(f"LLM this run        ${run_llm_cost_usd:.4f}", flush=True)
                    if run_budget_status:
                        spent = float(run_budget_status.get("current_spend_usd", 0.0) or 0.0)
                        limit = float(run_budget_status.get("limit_usd", 0.0) or 0.0)
                        source = run_budget_status.get("source", "local")
                        print(
                            f"LLM month-to-date   ${spent:.2f} / ${limit:.2f} ({source})",
                            flush=True,
                        )
                        credit = run_budget_status.get("credit_balance")
                        currency = run_budget_status.get("credit_balance_currency", "USD")
                        if credit is not None:
                            print(f"Azure credit        {credit:.2f} {currency}", flush=True)
                except Exception:
                    pass
                print_pipeline_summary()

    Thread(target=background_job).start()
    print("[webhook] pipeline job started in background", flush=True)
    return {"status": "accepted"}
