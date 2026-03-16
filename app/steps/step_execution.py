"""
Step execution: run capture steps (goto, click, screenshot) against a preview URL via Playwright.

Single responsibility: take a list of normalized steps and a preview URL, execute them
in order, and write screenshots to disk. Uses networkidle after goto/click. Failed steps
are logged; pipeline does not crash on a single step failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeoutError

from observability import pipeline_step

BASE_APP_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_STEPS: List[Dict[str, Any]] = [
    {"action": "screenshot"},
    {"action": "screenshot"},
]

CLICK_TIMEOUT_MS = 2000


def _resolve_url(preview_url: str, path: str) -> str:
    """Resolve a possibly-relative URL against the preview base URL."""
    if not path:
        return preview_url
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return preview_url.rstrip("/") + "/" + path.lstrip("/")


def _wait_networkidle(page: Page) -> None:
    """Wait for networkidle; log and continue on failure (never crash pipeline)."""
    try:
        page.wait_for_load_state("networkidle")
    except Exception as e:
        print(
            f"[step_execution] wait_for_load_state(networkidle) failed: {type(e).__name__}: {e}",
            flush=True,
        )


def safe_click(
    page: Page,
    selector: str,
    *,
    description: Optional[str] = None,
) -> bool:
    """Click by selector. Returns True on success, False on timeout or error."""
    try:
        page.click(selector, timeout=CLICK_TIMEOUT_MS)
        return True
    except PlaywrightTimeoutError:
        print(f"[step_execution] selector timed out: {selector}", flush=True)
    except Exception as e:
        print(
            f"[step_execution] selector failed: {selector} ({type(e).__name__}: {e})",
            flush=True,
        )
    return False


def safe_click_by_text(page: Page, text: str) -> bool:
    """Click by visible text. Returns True on success, False on timeout or error."""
    try:
        page.get_by_text(text).click(timeout=CLICK_TIMEOUT_MS)
        return True
    except PlaywrightTimeoutError:
        print(f"[step_execution] click by text timed out: {text!r}", flush=True)
    except Exception as e:
        print(
            f"[step_execution] click by text failed: {text!r} ({type(e).__name__}: {e})",
            flush=True,
        )
    return False


@pipeline_step("step_execution")
def run_capture(
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    screenshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Execute capture steps against the preview URL and write screenshots to disk.

    Args:
        preview_url: Base URL of the preview deployment.
        steps: List of step dicts (action: goto | click | screenshot, plus url/selector/text).
        screenshot_dir: Directory for shot*.png files; defaults to app/screenshots directory.

    Returns:
        Dict with steps_succeeded, steps_failed, failure_reason (last failure if any).
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
    out_dir = screenshot_dir or SCREENSHOT_DIR

    for old_shot in out_dir.glob("shot*.png"):
        old_shot.unlink()

    step_results: List[bool] = []
    last_failure_reason: Optional[str] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(preview_url)
            _wait_networkidle(page)
        except Exception as e:
            print(
                f"[step_execution] initial goto failed: {type(e).__name__}: {e}",
                flush=True,
            )

        screenshot_index = 1
        for i, step in enumerate(steps):
            action = step.get("action")
            step_ok = True
            try:
                if action == "screenshot":
                    path = out_dir / f"shot{screenshot_index}.png"
                    print(f"[step_execution] screenshot file={path.name}", flush=True)
                    page.screenshot(path=str(path))
                    screenshot_index += 1
                elif action == "click":
                    selector = step.get("selector")
                    text = step.get("text")
                    if selector:
                        print(f"[step_execution] click selector={selector}", flush=True)
                        ok = safe_click(
                            page,
                            selector,
                            description=step.get("description") or step.get("text"),
                        )
                        if not ok:
                            step_ok = False
                            last_failure_reason = "selector not found"
                            print(
                                f"[step_execution] click failed selector={selector}",
                                flush=True,
                            )
                        else:
                            _wait_networkidle(page)
                    elif text:
                        print(f"[step_execution] click text={text!r}", flush=True)
                        ok = safe_click_by_text(page, text)
                        if not ok:
                            step_ok = False
                            last_failure_reason = "selector not found"
                            print(
                                f"[step_execution] click failed text={text!r}",
                                flush=True,
                            )
                        else:
                            _wait_networkidle(page)
                    else:
                        print(
                            "[step_execution] skip click step (no selector or text)",
                            flush=True,
                        )
                        step_ok = False
                elif action == "goto":
                    target = step.get("url")
                    resolved = _resolve_url(preview_url, target or "/")
                    print(f"[step_execution] navigating url={target or '/'}", flush=True)
                    page.goto(resolved)
                    _wait_networkidle(page)
                else:
                    print(
                        f"[step_execution] unknown action={action!r} skipping",
                        flush=True,
                    )
                    step_ok = False
            except Exception as e:
                step_ok = False
                last_failure_reason = f"{type(e).__name__}: {e}"
                print(
                    f"[step_execution] Step {i} failed ({action}): {last_failure_reason}",
                    flush=True,
                )
            step_results.append(step_ok)

        has_shots = any(out_dir.glob("shot*.png"))
        if not has_shots:
            fallback_path = out_dir / "shot1.png"
            print(
                "[step_execution] fallback screenshot file=shot1.png",
                flush=True,
            )
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
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError(
                "PREVIEW_URL environment variable or command line argument required"
            )
    run_capture(preview_url=preview_url)
