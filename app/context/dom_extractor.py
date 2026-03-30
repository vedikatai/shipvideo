from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from playwright.sync_api import Page

from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, DomSnapshot

if TYPE_CHECKING:



    from app.browser.agent_browser_cli import AgentBrowserCLI


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
        "inputs": [],                                                                
        "data_testids": dedup_tids,
    }


def extract_ab_context(
    cli: "AgentBrowserCLI",
    *,
    save_raw: bool = True,
) -> AgentBrowserSnapshot:
    """
    Agent Browser-backed context extraction for the current page.

    Routes page extraction through the Agent Browser backend when the
    experiment backend is selected (Phase 3). Conceptual parallel to
    extract_dom_context() for the agent_browser_cli experiment path.

    The snapshot is suitable for immediate use with ref_selector.select_ref():

        snap   = extract_ab_context(cli)
        result = select_ref("Generate API Key", snap)

    In the Phase 3 execution loop, call with save_raw=False for intermediate
    post-click re-snapshots (state-change detection) to avoid flooding disk:

        snap_after = extract_ab_context(cli, save_raw=False)

    Caller responsibilities:
        - Call cli.open(url) before calling this function.
        - Call cli.close() when the browser session is no longer needed.

    Args:
        cli      — an AgentBrowserCLI instance that has already navigated to
                   the target page via cli.open(url).
        save_raw — when True (default), the raw CLI JSON payload is persisted
                   to app/data/ab_snapshots/ for experiment debugging.
                   Pass False for intermediate snapshots in the execution loop
                   to keep disk usage bounded.

    Returns:
        AgentBrowserSnapshot with current_url, snapshot_text,
        interactive_elements, and raw_snapshot_path.
    """


    from app.browser.agent_browser_cli import AgentBrowserCLI as _CLI              

    print(
        f"[dom_extractor] extract_ab_context: save_raw={save_raw}",
        flush=True,
    )
    return cli.snapshot(save_raw=save_raw)


def merge_ab_route_snapshots(
    route_snapshots: Dict[str, AgentBrowserSnapshot],
) -> Dict[str, Any]:
    """
    Merge multiple AgentBrowserSnapshot objects (keyed by route path) into a
    route-aware extraction summary for apples-to-apples comparison with the
    Playwright dom_crawler output.

    Used in Phase 4 to compare extraction coverage between backends on the
    same set of routes. The returned dict structure mirrors dom_crawler.py's
    per-route data format so callers can apply the same analysis logic to both.

    Deduplication: interactive elements are deduplicated across routes by a
    (role, name.lower()) key to produce a unique elements list comparable to
    the merged buttons/links lists from crawl_dom_data().

    Args:
        route_snapshots — mapping route_path → AgentBrowserSnapshot, typically
                          produced by app.steps.dom_crawler.crawl_ab_routes().

    Returns:
        Dict with:
            routes                    — list of crawled route paths.
            total_interactive_elements — total elements (before dedup).
            unique_elements           — count after (role, name) dedup.
            all_elements              — deduplicated List[AgentBrowserElement].
            elements_by_route         — {route: List[AgentBrowserElement]}.
            snapshot_texts_by_route   — {route: snapshot_text str}.
    """
    all_elements: List[AgentBrowserElement] = []
    elements_by_route: Dict[str, List[AgentBrowserElement]] = {}
    snapshot_texts_by_route: Dict[str, str] = {}
    seen_keys: set = set()

    for route, snap in route_snapshots.items():
        elements_by_route[route] = list(snap["interactive_elements"])
        snapshot_texts_by_route[route] = snap.get("snapshot_text", "")
        for el in snap["interactive_elements"]:
            dedup_key = f"{el['role']}:{el['name'].lower().strip()}"
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                all_elements.append(el)

    total = sum(
        len(s["interactive_elements"]) for s in route_snapshots.values()
    )

    return {
        "routes": list(route_snapshots.keys()),
        "total_interactive_elements": total,
        "unique_elements": len(all_elements),
        "all_elements": all_elements,
        "elements_by_route": elements_by_route,
        "snapshot_texts_by_route": snapshot_texts_by_route,
    }

