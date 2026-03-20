from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

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


def validate_step_against_dom(step: Dict[str, Any], dom_ctx: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate a single step against the live DOM context.

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
            if selector.startswith("[data-testid="):
                return True, "ok:testid"
            if selector.startswith("[aria-label="):
                return True, "ok:aria"
            if _PLAYWRIGHT_ENGINE_RE.match(selector):
                return True, "ok:playwright_engine"
            if not _allowed_raw_css(selector, dom_ctx):
                return False, f"raw_css_rejected:{selector}"
            return True, "ok:raw_css_present_in_dom"

        # text-based click: check against known visible texts (case-insensitive partial OK)
        if text:
            known = _known_button_texts(dom_ctx)
            # Exact match first
            if text in known:
                return True, "ok:text_exact"
            # Case-insensitive fallback
            text_lower = text.lower()
            if any(k.lower() == text_lower for k in known):
                return True, "ok:text_icase"
            # Partial match — if the text is a substring of a known element (lenient)
            if any(text_lower in k.lower() or k.lower() in text_lower for k in known if k):
                return True, "ok:text_partial"
            return False, f"text_not_in_dom:{text}"

    return True, "ok"

