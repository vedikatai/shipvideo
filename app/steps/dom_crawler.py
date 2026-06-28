import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from app.dom_schema import AgentBrowserSnapshot, DomSnapshot








_AUTH_WALL_SEGMENTS = frozenset({"login", "signin", "auth", "unauthorized"})


def _is_auth_wall(url: str) -> bool:
    try:
        path = urlparse(url).path
    except Exception:
        path = url
    segments = [s for s in path.lower().lstrip("/").split("/") if s]
    if not segments:
        return False
    return segments[0] in _AUTH_WALL_SEGMENTS






async def _discover_routes(page, staging_url: str) -> List[str]:
    try:
        print(f"[dom] discovering routes url={staging_url}", flush=True)
        await page.goto(staging_url, timeout=15000)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass
        # React hydration often lags networkidle on heavy SPAs (dashboard).
        try:
            await page.evaluate(
                """async () => {
                    if (document.fonts && document.fonts.ready) {
                        await document.fonts.ready;
                    }
                    await new Promise((r) =>
                        requestAnimationFrame(() => requestAnimationFrame(r))
                    );
                }"""
            )
        except Exception:
            pass

        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href'))",
        )

        routes = list(
            {

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






def _build_visit_order(
    seed_routes: List[str],
    discovered: List[str],
    max_routes: int,
) -> List[str]:
    home_in_seeds = "/" in seed_routes

    effective_max = max_routes if home_in_seeds else max(1, max_routes - 1)

    seen: set = set()
    order: List[str] = []

    for r in seed_routes:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            order.append(r)
            if len(order) >= effective_max:
                break                                                          

    for r in sorted(discovered):
        if len(order) >= effective_max:
            break
        if r not in seen:
            seen.add(r)
            order.append(r)


    if "/" not in seen:
        order.append("/")

    return order or ["/"]








MAX_ITEMS_BUTTONS = 40                                             
MAX_ITEMS_LINKS   = 30                   
MAX_ITEMS_INPUTS  = 20
MAX_ITEMS_TESTIDS = 40                                             


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
            "title":    "",                                                              
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
    try:
        print(f"[dom] collecting UI elements url={url}", flush=True)
        await page.goto(url, timeout=15000)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass
        try:
            await page.evaluate(
                """async () => {
                    if (document.fonts && document.fonts.ready) {
                        await document.fonts.ready;
                    }
                    await new Promise((r) =>
                        requestAnimationFrame(() => requestAnimationFrame(r))
                    );
                }"""
            )
        except Exception:
            pass
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






def _merge_snapshots(route_snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
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






async def crawl_dom_data(
    staging_url: str,
    *,
    seed_routes: Optional[List[str]] = None,
    max_routes: int = 6,
) -> DomSnapshot:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            try:

                home_page = await context.new_page()
                discovered_routes = await _discover_routes(home_page, staging_url)
                await home_page.close()


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


        merged = _merge_snapshots(route_snapshots)
        all_routes = sorted(set(discovered_routes) | set(seed_routes or []))

        return {
            "current_path":    "/",
            "routes":          all_routes or ["/"],
            "buttons":         merged["buttons"],
            "links":           merged["links"],
            "inputs":          merged["inputs"],
            "data_testids":    merged["data_testids"],
            "route_snapshots": route_snapshots,                                  
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






def crawl_ab_routes(
    base_url: str,
    routes: List[str],
    *,
    session: str = "ab_crawl",
) -> Dict[str, AgentBrowserSnapshot]:


    from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError

    results: Dict[str, AgentBrowserSnapshot] = {}
    cli = AgentBrowserCLI(session=session)

    try:
        for route in routes:
            full_url = base_url.rstrip("/") + route
            t0 = time.monotonic()
            try:
                print(f"[ab_crawl] crawling route={route!r} url={full_url}", flush=True)
                cli.open(full_url)
                snap = cli.snapshot()
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                results[route] = snap
                print(
                    f"[ab_crawl] route={route!r} "
                    f"elements={len(snap['interactive_elements'])} "
                    f"elapsed_ms={elapsed_ms}",
                    flush=True,
                )
            except AgentBrowserError as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                print(
                    f"[ab_crawl] route_failed route={route!r} "
                    f"elapsed_ms={elapsed_ms}: {exc}",
                    flush=True,
                )
    finally:
        try:
            cli.close()
        except Exception:
            pass

    return results
