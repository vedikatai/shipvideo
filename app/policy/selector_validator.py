from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page

# Playwright built-in selector engine prefixes — always valid to pass to Playwright.
# e.g.  "role=button name=Submit"  "text=Click Me"  "css=#id"  "xpath=//button"
_PLAYWRIGHT_ENGINE_RE = re.compile(
    r"^(role|text|css|xpath|id|data-testid|aria-label|nth|has-text|has)\s*=",
    re.IGNORECASE,
)


def _known_button_texts(dom_ctx: Dict[str, Any]) -> set:
    """All visible button/link texts present in the current DOM context."""
    known: set = set()
    for b in dom_ctx.get("buttons") or []:
        t = (b.get("text") or "").strip()
        if t:
            known.add(t)
    for l in dom_ctx.get("links") or []:
        t = (l.get("text") or "").strip()
        if t:
            known.add(t)
    return known


def _allowed_raw_css(selector: str, dom_ctx: Dict[str, Any]) -> bool:
    """
    Return True only for selectors that are verifiably present in the live DOM context.

    Allow list:
    - [data-testid='x'] / [aria-label='x']  → always semantic and safe
    - Playwright engine prefixes (role=, text=, css=, xpath=, …)
    - #id selectors  → only if that id is in the DOM candidates
    - Everything else → rejected
    """
    s = (selector or "").strip()
    if not s:
        return False

    # Semantic attribute selectors — always safe
    if s.startswith("[data-testid=") or s.startswith("[aria-label="):
        return True

    # Playwright selector engine prefixes — valid Playwright syntax, allow
    if _PLAYWRIGHT_ENGINE_RE.match(s):
        return True

    # Raw #id — allowed only when that id is found in the live DOM buttons/links
    if s.startswith("#"):
        raw_id = s[1:].split("[")[0].split(":")[0]  # strip any pseudo/attr suffix
        for b in (dom_ctx.get("buttons") or []) + (dom_ctx.get("links") or []):
            if (b.get("id") or "").strip() == raw_id:
                return True
        return False

    # .class and complex compound selectors are too brittle — reject
    return False


def _selector_count_on_page(page: Page, selector: str) -> int:
    """
    Return the count of elements matching selector on the current page.

    If the initial count is 0, waits up to 1500 ms for the element to attach
    (accounts for lazy-rendered components) then recounts.  Returns 0 on any
    exception so callers never crash on malformed or unsupported selectors.
    """
    try:
        count = page.locator(selector).count()
        if count == 0:
            try:
                page.wait_for_selector(selector, state="attached", timeout=1500)
                count = page.locator(selector).count()
            except Exception:
                pass
        return count
    except Exception:
        return 0


def validate_step_against_dom(
    step: Dict[str, Any],
    dom_ctx: Dict[str, Any],
    page: Optional[Page] = None,
) -> Tuple[bool, str]:
    """
    Validate a single step against the live DOM context.

    When ``page`` is None: static validation only (unchanged from pre-Phase 4).
    When ``page`` is provided: after static checks pass, an additional live
    existence check is run via ``_selector_count_on_page`` (selectors) or
    ``page.get_by_text`` (text clicks).  A count of 0 is a hard rejection.

    Returns (ok: bool, reason: str).
    """
    action = step.get("action")
    if action not in {"goto", "click", "screenshot"}:
        return False, f"invalid_action:{action}"

    if action == "goto":
        url = (step.get("url") or "").strip()
        if not url:
            return False, "missing_goto_url"
        if url not in set(dom_ctx.get("routes") or []):
            return False, f"route_not_in_dom:{url}"
        return True, "ok"

    if action == "click":
        selector = (step.get("selector") or "").strip()
        text = (step.get("text") or "").strip()

        if not selector and not text:
            return False, "missing_click_target"

        if selector:
            # Stage 1: static / syntax gate (same semantics as before Phase 4)
            is_testid = selector.startswith("[data-testid=")
            is_aria = selector.startswith("[aria-label=")
            is_playwright_engine = bool(_PLAYWRIGHT_ENGINE_RE.match(selector))

            if not (is_testid or is_aria or is_playwright_engine or _allowed_raw_css(selector, dom_ctx)):
                return False, f"raw_css_rejected:{selector}"

            # Stage 2: live existence check — authoritative gate when page available
            if page is not None:
                if _selector_count_on_page(page, selector) == 0:
                    return False, f"selector_not_found_on_page:{selector}"

            if is_testid:
                return True, "ok:testid"
            if is_aria:
                return True, "ok:aria"
            if is_playwright_engine:
                return True, "ok:playwright_engine"
            return True, "ok:raw_css_present_in_dom"

        # text-based click: static pre-filter, then live count as authoritative gate
        if text:
            known = _known_button_texts(dom_ctx)

            if page is not None:
                # Live count is the authoritative gate
                try:
                    live_count = page.get_by_text(text, exact=True).count()
                except Exception:
                    live_count = 0
                if live_count == 0:
                    return False, f"text_not_found_on_page:{text}"
                return True, "ok:text_live"

            # Static fallback when no page is available
            if text in known:
                return True, "ok:text_exact"
            text_lower = text.lower()
            if any(k.lower() == text_lower for k in known):
                return True, "ok:text_icase"
            if any(text_lower in k.lower() or k.lower() in text_lower for k in known if k):
                return True, "ok:text_partial"
            return False, f"text_not_in_dom:{text}"

    return True, "ok"
