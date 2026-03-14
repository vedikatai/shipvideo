"""
Playwright-only execution for capture steps (click by selector or text).
"""
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def safe_click(
    page: Page,
    selector: str,
    *,
    description: Optional[str] = None,
) -> bool:
    """Click by selector. Returns True on success, False on timeout or error."""
    try:
        page.click(selector, timeout=2000)
        return True
    except PlaywrightTimeoutError:
        print(f"[execution] selector timed out: {selector}", flush=True)
    except Exception as e:
        print(f"[execution] selector failed: {selector} ({type(e).__name__}: {e})", flush=True)
    return False


def safe_click_by_text(page: Page, text: str) -> bool:
    """Click by visible text. Returns True on success, False on timeout or error."""
    try:
        page.get_by_text(text).click(timeout=2000)
        return True
    except PlaywrightTimeoutError:
        print(f"[execution] click by text timed out: {text!r}", flush=True)
    except Exception as e:
        print(f"[execution] click by text failed: {text!r} ({type(e).__name__}: {e})", flush=True)
    return False
