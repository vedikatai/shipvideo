from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page






@dataclass
class PageFingerprint:
    path: str                                     
    title: str                          
    heading_set: str                                                           
    landmark_count: int                                        
    testid_set: str                                                     







@dataclass
class NavigationState:
    path: str
    fingerprint: PageFingerprint
    dom_hash: str                                                             






def _dom_signature(page: Page) -> str:
    text = page.evaluate(
        "() => ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 4000)"
    ) or ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _collect_page_fingerprint(page: Page) -> PageFingerprint:
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






def capture_state(page: Page) -> NavigationState:
    fp = _collect_page_fingerprint(page)
    dom_hash = _dom_signature(page)                           
    return NavigationState(path=fp.path, fingerprint=fp, dom_hash=dom_hash)


def detect_major_change(before: NavigationState, after: NavigationState) -> bool:
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


_REACT_HYDRATION_WAIT_JS = """async () => {
    if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
    }
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    // React 18 sets data-reactroot / #root; Next uses #__next. Wait until the
    // app root has children and is not stuck on a blank hydration shell.
    const root = document.getElementById('root')
        || document.getElementById('__next')
        || document.getElementById('app')
        || document.querySelector('[data-reactroot]')
        || document.body;
    if (!root) return false;
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
        const hasContent = !!(root.textContent || '').trim()
            || root.querySelector('button, a, input, [role="button"], [data-testid]');
        const busy = root.getAttribute('aria-busy') === 'true'
            || document.documentElement.dataset.hydrating === 'true';
        // React attaches event handlers post-hydration; presence of listeners on
        // interactive nodes is a strong signal handlers are live.
        const interactive = root.querySelector('button, a[href], input, [role="button"]');
        let handlersReady = !interactive;
        if (interactive) {
            try {
                const keys = Object.keys(interactive);
                handlersReady = keys.some((k) => k.startsWith('__reactFiber')
                    || k.startsWith('__reactProps')
                    || k.startsWith('__reactInternalInstance'));
            } catch (e) {
                handlersReady = true;
            }
        }
        if (hasContent && !busy && handlersReady) {
            return true;
        }
        await new Promise((r) => setTimeout(r, 50));
        await new Promise((r) => requestAnimationFrame(r));
    }
    return false;
}"""


def wait_for_react_hydration(page: Page, timeout_ms: int = 5000) -> None:
    """networkidle returns before React attaches event handlers on heavy SPAs."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 3000))
    except Exception:
        pass
    try:
        page.evaluate(_REACT_HYDRATION_WAIT_JS)
    except Exception:
        time.sleep(0.25)


def wait_stable_after_navigation(page: Page, timeout_ms: int = 12000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_hash = None
    stable_hits = 0
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    # Prefer fonts + first paint readiness so SPA hydration is not mid-CSS.
    wait_for_react_hydration(page, timeout_ms=min(timeout_ms, 5000))
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

    return


def wait_spa_ready_for_screenshot(page: Page, timeout_ms: int = 4000) -> None:
    """Block screenshot until DOM text stabilizes post-route-change (anti-FOUC)."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 3000))
    except Exception:
        pass
    wait_for_react_hydration(page, timeout_ms=timeout_ms)
    wait_stable_after_navigation(page, timeout_ms=timeout_ms)
