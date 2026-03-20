from __future__ import annotations

from typing import Any, Dict, List

from playwright.sync_api import Page

from app.dom_schema import DomSnapshot


def extract_dom_context(page: Page, *, max_items: int = 40) -> DomSnapshot:
    """
    Extract fresh, interaction-focused DOM context from the CURRENT page.

    Returns a DomSnapshot. All button dicts conform to ButtonCandidate:
      - `aria`  holds aria-label only (use for [aria-label='x'] selectors).
      - `title` holds title attribute only (display-only, not for selectors).
      - `selector` is "" — runtime extractor does not precompute CSS selectors.
    """
    current_path = page.evaluate("() => window.location.pathname || '/'")

    buttons = page.eval_on_selector_all(
        "button, [role='button'], [aria-label], [data-testid], input[type='button'], input[type='submit']",
        f"""els => els.slice(0, {max_items}).map(e => ({{
            role: (e.getAttribute('role') || (e.tagName || '').toLowerCase()).toLowerCase(),
            text: (e.innerText || e.value || "").trim().slice(0, 100),
            testid: e.getAttribute('data-testid') || "",
            aria: e.getAttribute('aria-label') || "",
            title: e.getAttribute('title') || "",
            id: e.id || "",
            selector: ""
        }}))""",
    ) or []

    links = page.eval_on_selector_all(
        "a[href]",
        f"""els => els.slice(0, {max_items}).map(e => ({{
            text: (e.innerText || "").trim().slice(0, 100),
            href: e.getAttribute('href') || "",
            testid: e.getAttribute('data-testid') || "",
            aria: e.getAttribute('aria-label') || "",
            id: e.id || ""
        }}))""",
    ) or []

    testids = page.eval_on_selector_all(
        "[data-testid]",
        f"""els => els.slice(0, {max_items * 2}).map(e => ({{
            testid: e.getAttribute('data-testid') || "",
            tag: (e.tagName || "").toLowerCase(),
            text: (e.innerText || "").trim().slice(0, 80)
        }}))""",
    ) or []

    dedup_tids: List[Dict[str, str]] = []
    seen = set()
    for t in testids:
        tid = (t.get("testid") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            dedup_tids.append(
                {
                    "testid": tid,
                    "tag": (t.get("tag") or "").strip(),
                    "text": (t.get("text") or "").strip(),
                }
            )
        if len(dedup_tids) >= max_items:
            break

    routes = set([current_path, "/"])
    for l in links:
        href = (l.get("href") or "").strip()
        if href.startswith("/"):
            routes.add(href)

    return {
        "current_path": current_path or "/",
        "routes": sorted(routes),
        "buttons": buttons,
        "links": links,
        "inputs": [],  # runtime extractor does not query inputs; crawler covers this
        "data_testids": dedup_tids,
    }

