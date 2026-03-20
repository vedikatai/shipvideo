from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


# ---------------------------------------------------------------------------
# PageFingerprint — structural signals that are stable across minor DOM updates
# ---------------------------------------------------------------------------

@dataclass
class PageFingerprint:
    path: str           # window.location.pathname
    title: str          # document.title
    heading_set: str    # sorted, "|"-joined h1+h2 texts (max 5, 60 chars each)
    landmark_count: int # count of structural landmark elements
    testid_set: str     # sorted, "|"-joined data-testid values (max 20)


# ---------------------------------------------------------------------------
# NavigationState — keeps dom_hash for the stability loop; adds fingerprint
# for the semantic major-change check
# ---------------------------------------------------------------------------

@dataclass
class NavigationState:
    path: str
    fingerprint: PageFingerprint
    dom_hash: str  # kept for wait_stable_after_navigation stability loop only


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dom_signature(page: Page) -> str:
    """Raw body-text hash — used only by wait_stable_after_navigation."""
    text = page.evaluate(
        "() => ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 4000)"
    ) or ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _collect_page_fingerprint(page: Page) -> PageFingerprint:
    """
    Collect structural page signals via a single page.evaluate() call.

    Signals collected:
      - path: window.location.pathname
      - title: document.title
      - headings: first 5 h1/h2 innerTexts, sorted
      - testids: first 20 data-testid values, sorted
      - landmarks: count of [role=main], main, [role=dialog], [role=alertdialog],
          [role=navigation], nav, [role=banner], header, [role=contentinfo],
          footer, aside
    """
    result: Any = page.evaluate("""() => {
        const hs = [...document.querySelectorAll('h1,h2')]
            .slice(0, 5)
            .map(e => (e.innerText || '').trim().slice(0, 60))
            .filter(Boolean);
        const tids = [...document.querySelectorAll('[data-testid]')]
            .slice(0, 20)
            .map(e => e.getAttribute('data-testid') || '')
            .filter(Boolean);
        const landmarks = document.querySelectorAll(
            '[role=main],main,[role=dialog],[role=alertdialog],' +
            '[role=navigation],nav,[role=banner],header,' +
            '[role=contentinfo],footer,aside'
        ).length;
        return {
            path:      window.location.pathname || '/',
            title:     (document.title || '').trim(),
            headings:  hs,
            testids:   tids,
            landmarks: landmarks,
        };
    }""") or {}
    return PageFingerprint(
        path=result.get("path", "/"),
        title=result.get("title", ""),
        heading_set=" | ".join(sorted(result.get("headings") or [])),
        landmark_count=int(result.get("landmarks") or 0),
        testid_set=" | ".join(sorted(result.get("testids") or [])),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def capture_state(page: Page) -> NavigationState:
    fp = _collect_page_fingerprint(page)
    dom_hash = _dom_signature(page)  # kept for stability loop
    return NavigationState(path=fp.path, fingerprint=fp, dom_hash=dom_hash)


def detect_major_change(before: NavigationState, after: NavigationState) -> bool:
    """
    Return True when the page has undergone a semantically significant change.

    Tier 1 — path change: always a major change.
    Tier 2 — structural change: title, headings, or testid layout changed.
    Tier 3 — landmark delta >= 2: modal opened, panel added, etc.

    Counter increments, toast messages, and notification badges change only
    body text (not captured here), so they no longer trigger a spurious replan.
    """
    if before.path != after.path:
        return True

    fp_b, fp_a = before.fingerprint, after.fingerprint

    if fp_b.title != fp_a.title:
        return True
    if fp_b.heading_set != fp_a.heading_set:
        return True
    if fp_b.testid_set != fp_a.testid_set:
        return True
    if abs(fp_b.landmark_count - fp_a.landmark_count) >= 2:
        return True

    return False


def wait_stable_after_navigation(page: Page, timeout_ms: int = 12000) -> None:
    """
    Poll until the page body text stabilises (3 consecutive identical hashes).

    Uses the raw _dom_signature (body-text hash) because this function only
    needs self-consistency — is the page still changing? — not semantic meaning.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_hash = None
    stable_hits = 0
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    while time.monotonic() < deadline:
        h = _dom_signature(page)
        if h == last_hash:
            stable_hits += 1
            if stable_hits >= 3:
                return
        else:
            stable_hits = 0
            last_hash = h
        time.sleep(0.2)
    # bounded best-effort; do not hard-fail here
    return
