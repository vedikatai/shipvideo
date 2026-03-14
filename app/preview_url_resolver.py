"""
Resolves the preview URL from project configuration.

Supports:
- Static URL: use as-is (e.g. production or a fixed staging URL).
- PR-specific URL: set preview_url_template with placeholders so we record
  the deployment for the PR (e.g. Vercel preview) and wait for it to be ready.

Placeholders in preview_url_template:
- {pr_number}  → PR number (e.g. 42)
- {branch_slug} → PR head branch with slashes replaced by hyphens (e.g. feature-faq)

Example for Vercel PR previews (if your project uses pr-XX subdomain):
  "preview_url_template": "https://pr-{pr_number}.your-project.vercel.app"

Example for Vercel branch previews:
  "preview_url_template": "https://your-project-git-{branch_slug}-your-team.vercel.app"
"""
from typing import Optional
from app.config import load_config
from observability import pipeline_step


@pipeline_step("preview_lookup")
def get_preview_url(
    pr_number: Optional[int] = None,
    branch: Optional[str] = None,
) -> str:
    """
    Gets the preview URL from project configuration, with optional placeholders.

    Args:
        pr_number: If provided, {pr_number} in the template is replaced.
        branch: If provided, {branch_slug} is replaced (e.g. feature/faq → feature-faq).

    Returns:
        The resolved preview URL.
    """
    config = load_config()
    template = config.get("preview_url_template")

    if not template:
        raise ValueError(
            "preview_url_template not configured in project_config.json. "
            "Set it to your app URL. For PR previews use placeholders: "
            "{pr_number} and/or {branch_slug}"
        )

    url = template
    if pr_number is not None and "{pr_number}" in url:
        url = url.replace("{pr_number}", str(pr_number))
    if branch is not None and "{branch_slug}" in url:
        slug = branch.strip().replace("/", "-")
        url = url.replace("{branch_slug}", slug)

    print(f"[preview] url={url}", flush=True)
    return url


@pipeline_step("preview_ready")
def wait_for_preview_ready(
    url: str,
    timeout_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[int] = None,
) -> bool:
    """
    Polls the URL until it returns HTTP 200 or timeout.

    Returns True if the URL became ready, False on timeout.
    """
    import time
    import urllib.request
    import urllib.error

    config = load_config()
    timeout = timeout_seconds if timeout_seconds is not None else config.get("preview_ready_timeout_seconds", 300)
    interval = poll_interval_seconds if poll_interval_seconds is not None else config.get("preview_ready_poll_interval_seconds", 15)

    if timeout <= 0:
        print("[preview] skip ready check timeout=0", flush=True)
        return True

    deadline = time.monotonic() + timeout
    next_log = time.monotonic()

    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "ShipVideo-Engine/1.0")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 400:
                    print(f"[preview] ready url={url}", flush=True)
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if time.monotonic() >= next_log:
                print(f"[preview] waiting for ready error={e!r}", flush=True)
                next_log = time.monotonic() + 30
        time.sleep(interval)

    print(f"[preview] not ready after timeout={timeout}s url={url}", flush=True)
    return False
