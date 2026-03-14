"""
Capture screenshots from preview: goto, click, screenshot via Playwright.
We always wait for networkidle after goto/click and never crash the pipeline on a single step.
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

from app.execution_engine import safe_click, safe_click_by_text
from observability import pipeline_step

APP_DIR = Path(__file__).resolve().parent

DEFAULT_STEPS = [
    # Simple default: just take two screenshots with no clicks
    {"action": "screenshot"},
    {"action": "screenshot"},
]


def _resolve_url(preview_url: str, url: str) -> str:
    """Resolve a possisbly-relative URL against the preview base URL."""
    if not url:
        return preview_url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return preview_url.rstrip("/") + "/" + url.lstrip("/")


def _wait_networkidle(page) -> None:
    """Always wait for networkidle; log and continue on failure (never crash pipeline)."""
    try:
        page.wait_for_load_state("networkidle")
    except Exception as e:
        print(f"[capture] wait_for_load_state(networkidle) failed: {type(e).__name__}: {e}", flush=True)


@pipeline_step("capture")
def capture_demo(preview_url: str, steps=None):
    """
    Capture screenshots from preview environment.

    Returns:
        dict: {"steps_succeeded": int, "steps_failed": int, "failure_reason": str|None}
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    if not steps:
        steps = DEFAULT_STEPS

    for old_shot in APP_DIR.glob("shot*.png"):
        old_shot.unlink()

    step_results = []  # True = success, False = failed
    last_failure_reason = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(preview_url)
            _wait_networkidle(page)
        except Exception as e:
            print(f"[capture] Initial goto failed: {type(e).__name__}: {e}", flush=True)

        screenshot_index = 1
        for i, step in enumerate(steps):
            action = step.get("action")
            step_ok = True
            try:
                if action == "screenshot":
                    path = APP_DIR / f"shot{screenshot_index}.png"
                    print(f"[capture] screenshot file={path.name}", flush=True)
                    page.screenshot(path=str(path))
                    screenshot_index += 1
                elif action == "click":
                    selector = step.get("selector")
                    text = step.get("text")
                    if selector:
                        print(f"[capture] click selector={selector}", flush=True)
                        ok = safe_click(
                            page,
                            selector,
                            description=step.get("description") or step.get("text"),
                        )
                        if not ok:
                            step_ok = False
                            last_failure_reason = "selector not found"
                            print(f"[capture] click failed selector={selector}", flush=True)
                        else:
                            _wait_networkidle(page)
                    elif text:
                        print(f"[capture] click text={text!r}", flush=True)
                        ok = safe_click_by_text(page, text)
                        if not ok:
                            step_ok = False
                            last_failure_reason = "selector not found"
                            print(f"[capture] click failed text={text!r}", flush=True)
                        else:
                            _wait_networkidle(page)
                    else:
                        print("[capture] skip click step (no selector or text)", flush=True)
                        step_ok = False
                elif action == "goto":
                    target = step.get("url")
                    resolved = _resolve_url(preview_url, target)
                    print(f"[capture] navigating url={target or '/'}", flush=True)
                    page.goto(resolved)
                    _wait_networkidle(page)
                else:
                    print(f"[capture] unknown action={action!r} skipping", flush=True)
                    step_ok = False
            except Exception as e:
                step_ok = False
                last_failure_reason = f"{type(e).__name__}: {e}"
                print(f"[capture] Step {i} failed ({action}): {last_failure_reason}", flush=True)
            step_results.append(step_ok)

        # Fallback: if no screenshots were taken for any reason, capture final state once.
        has_shots = any(APP_DIR.glob("shot*.png"))
        if not has_shots:
            fallback_path = APP_DIR / "shot1.png"
            print("[capture] fallback screenshot file=shot1.png", flush=True)
            page.screenshot(path=str(fallback_path))

        browser.close()

    succeeded = sum(1 for r in step_results if r)
    failed = len(step_results) - succeeded
    return {
        "steps_succeeded": succeeded,
        "steps_failed": failed,
        "failure_reason": last_failure_reason if failed else None,
    }

if __name__ == "__main__":
    # Support direct call for testing
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError("PREVIEW_URL environment variable or command line argument required")
    capture_demo(preview_url=preview_url)
