from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from playwright.sync_api import Page

from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, DomSnapshot

if TYPE_CHECKING:



    from app.browser.agent_browser_cli import AgentBrowserCLI


def extract_dom_context(page: Page, *, max_items: int = 40) -> DomSnapshot:
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


    from app.browser.agent_browser_cli import AgentBrowserCLI as _CLI              

    print(
        f"[dom_extractor] extract_ab_context: save_raw={save_raw}",
        flush=True,
    )
    return cli.snapshot(save_raw=save_raw)


def merge_ab_route_snapshots(
    route_snapshots: Dict[str, AgentBrowserSnapshot],
) -> Dict[str, Any]:
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

