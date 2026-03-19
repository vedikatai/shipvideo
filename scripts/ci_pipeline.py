import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from github import Github

from app.config import load_config
from app.github_comment import comment_on_pr
from app.preview_url_resolver import get_preview_url, wait_for_preview_ready
from app.steps.pipeline import analyze_pr, run_pipeline
from observability import init_tracing, pipeline_run_span, print_pipeline_summary, set_current_span_error


REPO_ROOT = Path(__file__).resolve().parent.parent


def _get_pr_title_and_branch(repo_full_name: str, pr_number: int, token: str) -> tuple[str, str, str]:
    g = Github(token)
    pr = g.get_repo(repo_full_name).get_pull(pr_number)
    title = pr.title
    branch = pr.head.ref
    sha = pr.head.sha or ""
    return title, branch, sha


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=False, default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr_number", required=True, type=int)
    parser.add_argument("--pr_branch", required=False, default="")
    args = parser.parse_args()

    repo_full_name = args.repo
    if not repo_full_name:
        raise ValueError("Missing --repo or GITHUB_REPOSITORY")

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token:
        raise ValueError("GITHUB_TOKEN must be set (needed for GitHub API + diff fetch)")

    init_tracing()

    pr_title: Optional[str] = None
    pr_branch: Optional[str] = None
    _, default_branch, _ = _get_pr_title_and_branch(repo_full_name, args.pr_number, token)
    pr_branch = args.pr_branch or default_branch
    pr_title, _, _ = _get_pr_title_and_branch(repo_full_name, args.pr_number, token)

    preview_url = get_preview_url(pr_number=args.pr_number, branch=pr_branch)
    if not wait_for_preview_ready(preview_url):
        raise RuntimeError(f"Preview URL did not become ready in time: {preview_url}")

    config = load_config()
    delay = int(config.get("deployment_delay_seconds", 0) or 0)
    if delay > 0:
        import time

        time.sleep(delay)

    async def _run() -> None:
        with pipeline_run_span() as span:
            span.set_attribute("repo", repo_full_name)
            span.set_attribute("pr_number", args.pr_number)
            span.set_attribute("preview_url", preview_url)
            try:
                with open(REPO_ROOT / "project_config.json") as f:
                    pass

                flow: Dict[str, Any] = await analyze_pr(
                    repo_full_name=repo_full_name,
                    pr_number=args.pr_number,
                    pr_title=pr_title,
                    staging_url=preview_url,
                    diff_files=None,
                    start_route=None,
                )
                steps = flow.get("steps") or [{"action": "screenshot"}]
                generation_context = flow.get("generation_context")

                video_url, capture_summary = run_pipeline(
                    pr_number=args.pr_number,
                    preview_url=preview_url,
                    steps=steps,
                    generation_context=generation_context,
                    upload=False,  # CI: upload as workflow artifact instead of R2
                )

                data_dir = REPO_ROOT / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                summary_path = data_dir / "pipeline_run_summary.json"
                run_summary = {
                    "pr_number": args.pr_number,
                    "steps_generated": len(steps),
                    "steps_succeeded": capture_summary.get("steps_succeeded"),
                    "steps_failed": capture_summary.get("steps_failed"),
                    "failure_reason": capture_summary.get("failure_reason"),
                    "success": capture_summary.get("success"),
                    "cost_usd": round(flow.get("llm_cost_usd", 0.0) or 0.0, 4),
                    "video_url": video_url,
                }
                with open(summary_path, "w") as f:
                    json.dump(run_summary, f, indent=2)

                # Post comment with best-effort URL (in CI, local path may not be useful).
                # Workflow artifacts provide the real evidence.
                comment_on_pr(
                    repo_full_name,
                    args.pr_number,
                    video_url=video_url,
                    extra_note="CI run: see workflow artifacts for `out.mp4` + `screenshots/debug` logs.",
                )
            except Exception as e:
                set_current_span_error(str(e))
                comment_on_pr(
                    repo_full_name,
                    args.pr_number,
                    None,
                    error_message=f"**CI demo generation failed**\n\n{type(e).__name__}: {e}",
                )
                raise
            finally:
                print_pipeline_summary()

    asyncio.run(_run())


if __name__ == "__main__":
    main()

