"""
DOM crawler: discovers routes and collects UI elements from a staging URL.

Phase 3 upgrade: multi-route BFS seeded by diff-inferred routes and routeMap.
One BrowserContext is launched and a fresh page is created per route — this
preserves cookies/localStorage across visits without carrying scroll or modal
state between routes.

Public API:
    crawl_dom_data(staging_url, *, seed_routes, max_routes) -> DomSnapshot
"""
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from app.dom_schema import DomSnapshot


# ---------------------------------------------------------------------------
# Auth-wall detection
# ---------------------------------------------------------------------------

# Check the FIRST PATH SEGMENT only — substring matching on the full URL causes
# false positives (e.g. /authors, /authenticate, ?authToken=...).
_AUTH_WALL_SEGMENTS = frozenset({"login", "signin", "auth", "unauthorized"})


def _is_auth_wall(url: str) -> bool:
    """Return True if the URL path signals a redirect to an authentication wall.

    Extracts the URL path via urlparse and checks only the first path segment,
    preventing false positives on routes like /authors, /authenticate, or URLs
    with auth-related query parameters.
    """
    try:
        path = urlparse(url).path
    except Exception:
        path = url
    segments = [s for s in path.lower().lstrip("/").split("/") if s]
    if not segments:
        return False
    return segments[0] in _AUTH_WALL_SEGMENTS


# ---------------------------------------------------------------------------
# Route discovery (homepage link scan)
# ---------------------------------------------------------------------------

async def _discover_routes(page, staging_url: str) -> List[str]:
    """
    Discover internal routes from a live staging URL using an existing page.
    Returns a list of clean paths starting with '/'.

    Query strings (?ref=nav) and fragments (#section) are stripped to avoid
    treating parameterised copies of the same page as distinct routes.
    """
    try:
        print(f"[dom] discovering routes url={staging_url}", flush=True)
        await page.goto(staging_url, timeout=15000)
        await page.wait_for_load_state("networkidle")

        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href'))",
        )

        routes = list(
            {
                # Strip query strings and fragments so /page?tab=x → /page
                l.split("?")[0].split("#")[0]
                for l in links
                if l and l.startswith("/") and len(l) < 100
            }
        )
        if not routes:
            routes = ["/"]
        print(f"[dom] routes_found={len(routes)}", flush=True)
        return routes

    except Exception as e:
        print(f"[dom] route discovery failed: {type(e).__name__}: {e}", flush=True)
        return ["/"]


# ---------------------------------------------------------------------------
# Visit order builder
# ---------------------------------------------------------------------------

def _build_visit_order(
    seed_routes: List[str],
    discovered: List[str],
    max_routes: int,
) -> List[str]:
    """
    Return the ordered list of routes to crawl, capped at max_routes.

    Priority: seed_routes first (diff-inferred + routeMap; already ordered by
    the caller with highest-value routes first), then homepage-discovered links
    (sorted for determinism).

    "/" is ALWAYS included: the homepage contains global navigation elements
    (main menu, header links) that ground click steps on every route.  If "/"
    is not already present in seed_routes, one slot is reserved for it so that
    filling seed slots cannot crowd it out.
    """
    home_in_seeds = "/" in seed_routes
    # Reserve one slot for "/" unless it is already a seed.
    effective_max = max_routes if home_in_seeds else max(1, max_routes - 1)

    seen: set = set()
    order: List[str] = []

    for r in seed_routes:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            order.append(r)
            if len(order) >= effective_max:
                break  # do NOT early-return; fall through to the "/" guarantee

    for r in sorted(discovered):
        if len(order) >= effective_max:
            break
        if r not in seen:
            seen.add(r)
            order.append(r)

    # Guarantee the homepage is always crawled for global navigation context.
    if "/" not in seen:
        order.append("/")

    return order or ["/"]


# ---------------------------------------------------------------------------
# Per-page UI element extraction
# ---------------------------------------------------------------------------

# Per-route caps: buttons get a larger budget because they are the primary
# grounding signal for click steps.  Links and inputs are less critical.
MAX_ITEMS_BUTTONS = 40   # raised from 20 — JS eval now fetches 100
MAX_ITEMS_LINKS   = 30   # raised from 20
MAX_ITEMS_INPUTS  = 20
MAX_ITEMS_TESTIDS = 40   # raised from 20 — JS eval now fetches 100


def _short_selector(meta: Dict[str, Any], fallback_tag: str) -> str:
    testid = (meta.get("testid") or "").strip()
    aria = (meta.get("aria") or "").strip()
    el_id = (meta.get("id") or "").strip()
    classes = (meta.get("classes") or "").strip().split()
    if testid:
        return f"[data-testid='{testid}']"
    if aria:
        return f"[aria-label='{aria}']"
    if el_id:
        return f"#{el_id}"
    if classes:
        return f"{fallback_tag}.{classes[0]}"
    return fallback_tag


async def _extract_ui_from_current_page(page) -> Dict[str, Any]:
    """
    Extract UI elements from an already-navigated page (no navigation side-effect).
    Returns a dict matching ButtonCandidate / LinkCandidate / InputCandidate /
    TestIdCandidate schemas from dom_schema.py.
    """
    buttons = await page.eval_on_selector_all(
        "button, [role='button'], input[type='button'], input[type='submit']",
        """els => els.slice(0, 100).map(e => ({
            text: (e.innerText || e.value || "").trim().slice(0, 80),
            testid: e.getAttribute('data-testid') || "",
            aria: e.getAttribute('aria-label') || "",
            id: e.id || "",
            classes: e.className || ""
        }))""",
    )
    links = await page.eval_on_selector_all(
        "a[href]",
        """els => els.slice(0, 60).map(e => ({
            text: (e.innerText || "").trim().slice(0, 80),
            href: e.getAttribute('href') || "",
            testid: e.getAttribute('data-testid') || "",
            aria: e.getAttribute('aria-label') || "",
            id: e.id || "",
            classes: e.className || ""
        }))""",
    )
    inputs = await page.eval_on_selector_all(
        "input, textarea, select",
        """els => els.slice(0, 40).map(e => ({
            placeholder: (e.getAttribute('placeholder') || "").slice(0, 60),
            name: (e.getAttribute('name') || "").slice(0, 60),
            type: e.getAttribute('type') || "",
            testid: e.getAttribute('data-testid') || "",
            aria: e.getAttribute('aria-label') || "",
            id: e.id || "",
            classes: e.className || ""
        }))""",
    )
    data_testid_els = await page.eval_on_selector_all(
        "[data-testid]",
        """els => els.slice(0, 100).map(e => ({
            testid: e.getAttribute('data-testid') || "",
            tag: e.tagName.toLowerCase(),
            text: (e.innerText || "").trim().slice(0, 60)
        }))""",
    )

    out: Dict[str, Any] = {"buttons": [], "links": [], "inputs": []}

    for meta in buttons[:MAX_ITEMS_BUTTONS]:
        out["buttons"].append({
            "text":     meta.get("text", ""),
            "testid":   meta.get("testid", ""),
            "aria":     meta.get("aria", ""),
            "title":    "",  # dom_crawler JS eval does not collect title; extractor does
            "id":       meta.get("id", ""),
            "role":     "button",
            "selector": _short_selector(meta, "button"),
        })

    for meta in links[:MAX_ITEMS_LINKS]:
        out["links"].append({
            "text":   meta.get("text", ""),
            "href":   meta.get("href", ""),
            "testid": meta.get("testid", ""),
            "aria":   meta.get("aria", ""),
            "id":     meta.get("id", ""),
        })

    for meta in inputs[:MAX_ITEMS_INPUTS]:
        out["inputs"].append({
            "placeholder": meta.get("placeholder", ""),
            "name":        meta.get("name", ""),
            "input_type":  meta.get("type", ""),
            "testid":      meta.get("testid", ""),
            "aria":        meta.get("aria", ""),
            "id":          meta.get("id", ""),
        })

    seen: set = set()
    data_testids: List[Dict[str, str]] = []
    for el in data_testid_els[:MAX_ITEMS_TESTIDS * 2]:
        t = (el.get("testid") or "").strip()
        if t and t not in seen:
            seen.add(t)
            data_testids.append({
                "testid": t,
                "tag":    (el.get("tag") or "").lower(),
                "text":   (el.get("text") or "").strip()[:60],
            })
        if len(data_testids) >= MAX_ITEMS_TESTIDS:
            break
    out["data_testids"] = data_testids

    return out


async def _collect_ui_elements(page, url: str) -> Dict[str, Any]:
    """
    Navigate to url then extract UI elements.
    Kept for backward-compat; not used by the multi-route crawl_dom_data path.
    """
    try:
        print(f"[dom] collecting UI elements url={url}", flush=True)
        await page.goto(url, timeout=15000)
        await page.wait_for_load_state("networkidle")
        result = await _extract_ui_from_current_page(page)
        print(
            f"[dom] buttons={len(result.get('buttons', []))} "
            f"links={len(result.get('links', []))} "
            f"inputs={len(result.get('inputs', []))}",
            flush=True,
        )
        return result
    except Exception as e:
        print(f"[dom] UI collection failed url={url}: {type(e).__name__}: {e}", flush=True)
        return {"buttons": [], "links": [], "inputs": [], "data_testids": []}


# ---------------------------------------------------------------------------
# Snapshot merging
# ---------------------------------------------------------------------------

def _merge_snapshots(route_snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge per-route UI snapshots into top-level union fields.

    Deduplication rules across routes:
      buttons  — by testid (if non-empty) else text.lower()
      links    — by href
      inputs   — by name + "|" + placeholder
      testids  — by testid value
    """
    seen_btn: set = set()
    seen_href: set = set()
    seen_inp: set = set()
    seen_tid: set = set()

    merged_buttons: List[Dict[str, Any]] = []
    merged_links:   List[Dict[str, Any]] = []
    merged_inputs:  List[Dict[str, Any]] = []
    merged_testids: List[Dict[str, Any]] = []

    for _route, ui in route_snapshots.items():
        for btn in (ui.get("buttons") or []):
            key = (btn.get("testid") or "").strip() or (btn.get("text") or "").lower().strip()
            if key and key not in seen_btn:
                seen_btn.add(key)
                merged_buttons.append(btn)

        for link in (ui.get("links") or []):
            href = (link.get("href") or "").strip()
            if href and href not in seen_href:
                seen_href.add(href)
                merged_links.append(link)

        for inp in (ui.get("inputs") or []):
            key = (inp.get("name") or "") + "|" + (inp.get("placeholder") or "")
            key = key.strip("|")
            if key and key not in seen_inp:
                seen_inp.add(key)
                merged_inputs.append(inp)

        for tid in (ui.get("data_testids") or []):
            t = (tid.get("testid") or "").strip()
            if t and t not in seen_tid:
                seen_tid.add(t)
                merged_testids.append(tid)

    return {
        "buttons":      merged_buttons,
        "links":        merged_links,
        "inputs":       merged_inputs,
        "data_testids": merged_testids,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def crawl_dom_data(
    staging_url: str,
    *,
    seed_routes: Optional[List[str]] = None,
    max_routes: int = 6,
) -> DomSnapshot:
    """
    Multi-route BFS crawl over staging_url.

    Visit order (capped at max_routes):
      1. seed_routes  — diff-inferred + routeMap routes, highest priority.
      2. Homepage-discovered <a href> links — sorted for determinism.

    One BrowserContext is used for all routes; each route gets its own fresh
    page (context.new_page() / page.close()) to avoid carrying scroll or
    overlay state while preserving cookies/localStorage across visits.

    Per-route:
      - Timeout: 12 s networkidle.
      - Auth-wall guard: if page.url contains /login, /signin, /auth, or
        /unauthorized after navigation, the route is skipped silently.
      - All exceptions are caught per-route; the crawl continues.

    Returns a DomSnapshot with:
      top-level buttons/links/inputs/data_testids — merged + deduped across routes.
      routes        — union of discovered homepage links and seed_routes.
      route_snapshots — per-route raw UI data (extra key, not in DomSnapshot typedef).
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            try:
                # Step 1: Discover homepage routes using a fresh page.
                home_page = await context.new_page()
                discovered_routes = await _discover_routes(home_page, staging_url)
                await home_page.close()

                # Step 2: Determine ordered visit list.
                visit_order = _build_visit_order(
                    seed_routes=seed_routes or [],
                    discovered=discovered_routes,
                    max_routes=max_routes,
                )
                print(
                    f"[dom] visit_order={visit_order} max_routes={max_routes}",
                    flush=True,
                )

                base_url = staging_url.rstrip("/")
                route_snapshots: Dict[str, Dict[str, Any]] = {}

                # Step 3: Per-route collection — new page per route.
                for route in visit_order:
                    full_url = base_url + route
                    page = await context.new_page()
                    t0 = time.monotonic()
                    try:
                        print(f"[dom] collecting UI elements url={full_url}", flush=True)
                        await page.goto(
                            full_url,
                            timeout=12000,
                            wait_until="networkidle",
                        )

                        # Auth-wall guard: discard routes that redirect to a login page.
                        if _is_auth_wall(page.url):
                            elapsed_ms = int((time.monotonic() - t0) * 1000)
                            print(
                                f"[dom] auth wall detected route={route} "
                                f"redirected_to={page.url} elapsed_ms={elapsed_ms}",
                                flush=True,
                            )
                            continue

                        ui = await _extract_ui_from_current_page(page)
                        route_snapshots[route] = ui
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        print(
                            f"[dom] route={route} elapsed_ms={elapsed_ms} "
                            f"buttons={len(ui.get('buttons', []))} "
                            f"links={len(ui.get('links', []))} "
                            f"inputs={len(ui.get('inputs', []))}",
                            flush=True,
                        )

                    except Exception as e:
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        print(
                            f"[dom] route failed route={route} elapsed_ms={elapsed_ms}: "
                            f"{type(e).__name__}: {e}",
                            flush=True,
                        )

                    finally:
                        await page.close()

            finally:
                await context.close()
                await browser.close()

        # Step 4: Merge per-route snapshots into top-level union fields.
        merged = _merge_snapshots(route_snapshots)
        all_routes = sorted(set(discovered_routes) | set(seed_routes or []))

        return {
            "current_path":    "/",
            "routes":          all_routes or ["/"],
            "buttons":         merged["buttons"],
            "links":           merged["links"],
            "inputs":          merged["inputs"],
            "data_testids":    merged["data_testids"],
            "route_snapshots": route_snapshots,  # extra key for future consumers
        }

    except Exception as e:
        print(f"[dom] crawl failed: {type(e).__name__}: {e}", flush=True)
        return {
            "current_path":    "/",
            "routes":          ["/"],
            "buttons":         [],
            "links":           [],
            "inputs":          [],
            "data_testids":    [],
            "route_snapshots": {},
        }
