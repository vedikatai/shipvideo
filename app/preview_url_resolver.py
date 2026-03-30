from typing import Optional
from app.config import load_config
from observability import pipeline_step


@pipeline_step("preview_lookup")
def get_preview_url(
    pr_number: Optional[int] = None,
    branch: Optional[str] = None,
) -> str:
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
    import time
    import urllib.request
    import urllib.error
    import ssl

    config = load_config()
    timeout = timeout_seconds if timeout_seconds is not None else config.get("preview_ready_timeout_seconds", 300)
    interval = poll_interval_seconds if poll_interval_seconds is not None else config.get("preview_ready_poll_interval_seconds", 15)

    if timeout <= 0:
        print("[preview] skip ready check timeout=0", flush=True)
        return True

    deadline = time.monotonic() + timeout
    next_log = time.monotonic()



    ssl_context = None
    try:
        import certifi                

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_context = None

    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "ShipVideo-Engine/1.0")
            if ssl_context is not None:
                with urllib.request.urlopen(req, timeout=15, context=ssl_context) as resp:
                    if 200 <= resp.status < 400:
                        print(f"[preview] ready url={url}", flush=True)
                        return True
            else:
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
