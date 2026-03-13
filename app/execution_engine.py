from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

try:
    # Stagehand is optional; if not installed we just fall back to plain Playwright.
    from stagehand import Stagehand  # type: ignore
except Exception:  # pragma: no cover - best-effort import
    Stagehand = None  # type: ignore


def _init_stagehand(page: Page) -> Optional["Stagehand"]:
    """
    Initialize a Stagehand instance for the given page, if available.
    """
    if Stagehand is None:
        return None
    try:
        return Stagehand(page)
    except Exception as e:
        print(f"[execution] Failed to initialize Stagehand: {type(e).__name__}: {e}", flush=True)
        return None


def safe_click(page: Page, selector: str) -> bool:
    """
    Click using Playwright first; if that fails, ask Stagehand to recover
    an appropriate element to click.

    Returns True on any successful click, False otherwise.
    """
    # 1) Deterministic Playwright click
    try:
        page.click(selector, timeout=2000)
        return True
    except PlaywrightTimeoutError:
        print(f"[execution] selector timed out: {selector}", flush=True)
    except Exception as e:
        print(f"[execution] selector failed: {selector} ({type(e).__name__}: {e})", flush=True)

    # 2) Stagehand-assisted recovery (best-effort)
    sh = _init_stagehand(page)
    if sh is None:
        print("[execution] Stagehand unavailable, cannot recover selector", flush=True)
        return False

    try:
        prompt = (
            "Find the button or link on the page that best matches this selector "
            f"or its intent: {selector}"
        )
        candidate = sh.observe(prompt)  # API shape is owned by Stagehand
    except Exception as e:
        print(f"[execution] Stagehand observe failed: {type(e).__name__}: {e}", flush=True)
        return False

    if not candidate:
        print("[execution] Stagehand did not return a candidate element", flush=True)
        return False

    # Try a couple of generic ways to click using Playwright, without letting Stagehand drive.
    try:
        # If Stagehand returns a selector-like string
        if isinstance(candidate, str):
            page.click(candidate, timeout=2000)
        # If Stagehand returns an object with a selector attribute
        elif hasattr(candidate, "selector"):
            page.click(getattr(candidate, "selector"), timeout=2000)
        # If Stagehand returns an element handle with a click method
        elif hasattr(candidate, "click"):
            candidate.click()
        else:
            print("[execution] Stagehand candidate is not clickable, giving up", flush=True)
            return False

        print("[execution] Stagehand recovered element and click succeeded", flush=True)
        return True
    except PlaywrightTimeoutError:
        print("[execution] Stagehand candidate click timed out", flush=True)
    except Exception as e:
        print(f"[execution] Stagehand candidate click failed: {type(e).__name__}: {e}", flush=True)

    print("[execution] recovery failed", flush=True)
    return False

