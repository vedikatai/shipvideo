from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


@dataclass
class NavigationState:
    path: str
    dom_hash: str


def _dom_signature(page: Page) -> str:
    text = page.evaluate(
        "() => ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 4000)"
    ) or ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def capture_state(page: Page) -> NavigationState:
    path = page.evaluate("() => window.location.pathname || '/'") or "/"
    return NavigationState(path=path, dom_hash=_dom_signature(page))


def detect_major_change(before: NavigationState, after: NavigationState) -> bool:
    if before.path != after.path:
        return True
    return before.dom_hash != after.dom_hash


def wait_stable_after_navigation(page: Page, timeout_ms: int = 12000) -> None:
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

