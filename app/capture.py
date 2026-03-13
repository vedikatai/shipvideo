import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.execution_engine import safe_click

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


def capture_demo(preview_url: str, steps=None):
    """
    Capture screenshots from preview environment.
    
    Args:
        preview_url: The preview URL to record from (e.g., "https://yourapp-pr456.vercel.app")
        steps: List of step dictionaries, e.g.
            [
                {"action": "click", "selector": "#download-bill"},
                {"action": "screenshot"},
            ]
    
    Raises:
        ValueError: If preview_url is None or empty
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    if not steps:
        steps = DEFAULT_STEPS
    
    print(f"🌐 Navigating to preview URL: {preview_url}", flush=True)
    print(f"🧩 Using {len(steps)} capture steps", flush=True)
    
    # Clean up old screenshots before starting
    for old_shot in APP_DIR.glob("shot*.png"):
        old_shot.unlink()
        print(f"🧹 Cleaned up old screenshot: {old_shot.name}", flush=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(preview_url)

        screenshot_index = 1
        for step in steps:
            action = step.get("action")

            if action == "screenshot":
                path = APP_DIR / f"shot{screenshot_index}.png"
                print(f"📸 Taking screenshot -> {path.name}", flush=True)
                page.screenshot(path=str(path))
                screenshot_index += 1
            elif action == "click":
                selector = step.get("selector")
                if not selector:
                    print("⚠️ Skipping click step with no selector", flush=True)
                    continue
                print(f"🖱️ Clicking selector: {selector}", flush=True)
                ok = safe_click(page, selector)
                if not ok:
                    print(f"[execution] safe_click failed for selector: {selector}", flush=True)
            elif action == "goto":
                target = step.get("url")
                resolved = _resolve_url(preview_url, target)
                print(f"🌐 Navigating to: {resolved}", flush=True)
                page.goto(resolved)
            else:
                print(f"⚠️ Unknown action '{action}', skipping", flush=True)

        # Fallback: if no screenshots were taken for any reason, capture final state once.
        has_shots = any(APP_DIR.glob("shot*.png"))
        if not has_shots:
            fallback_path = APP_DIR / "shot1.png"
            print("[capture] No screenshots taken, capturing fallback -> shot1.png", flush=True)
            page.screenshot(path=str(fallback_path))

        browser.close()

if __name__ == "__main__":
    # Support direct call for testing
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError("PREVIEW_URL environment variable or command line argument required")
    capture_demo(preview_url=preview_url)
