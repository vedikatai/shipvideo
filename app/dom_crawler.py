from typing import List, Dict, Any

from playwright.async_api import async_playwright


async def _discover_routes(page, staging_url: str) -> List[str]:
    """
    Discover internal routes from a live staging/preview URL using an existing page.
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
                l
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


MAX_ITEMS = 20


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


async def _collect_ui_elements(page, url: str) -> Dict[str, Any]:
    """
    Collect structured UI elements: buttons (text + selector), links (text + href),
    inputs (placeholder/name), and elements with data-testid. Max 20 per category.
    """
    try:
        print(f"[dom] collecting UI elements url={url}", flush=True)
        await page.goto(url, timeout=15000)
        await page.wait_for_load_state("networkidle")

        buttons = await page.eval_on_selector_all(
            "button, [role='button']",
            """els => els.slice(0, 50).map(e => ({
                text: (e.innerText || "").trim().slice(0, 80),
                testid: e.getAttribute('data-testid') || "",
                aria: e.getAttribute('aria-label') || "",
                id: e.id || "",
                classes: e.className || ""
            }))""",
        )
        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.slice(0, 50).map(e => ({
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
            """els => els.slice(0, 50).map(e => ({
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
            """els => els.slice(0, 50).map(e => ({
                testid: e.getAttribute('data-testid') || "",
                tag: e.tagName.toLowerCase(),
                text: (e.innerText || "").trim().slice(0, 60)
            }))""",
        )

        out: Dict[str, Any] = {
            "buttons": [],
            "links": [],
            "inputs": [],
        }

        for meta in buttons[:MAX_ITEMS]:
            out["buttons"].append({
                "text": meta.get("text", ""),
                "selector": _short_selector(meta, "button"),
            })

        for meta in links[:MAX_ITEMS]:
            out["links"].append({
                "text": meta.get("text", ""),
                "href": meta.get("href", ""),
            })

        for meta in inputs[:MAX_ITEMS]:
            out["inputs"].append({
                "placeholder": meta.get("placeholder", ""),
                "name": meta.get("name", ""),
            })

        # Dedupe by testid, keep max 20
        seen = set()
        data_testids: List[Dict[str, str]] = []
        for el in data_testid_els[:MAX_ITEMS * 2]:
            t = (el.get("testid") or "").strip()
            if t and t not in seen:
                seen.add(t)
                data_testids.append({
                    "testid": t,
                    "tag": (el.get("tag") or "").lower(),
                    "text": (el.get("text") or "").strip()[:60],
                })
            if len(data_testids) >= MAX_ITEMS:
                break
        out["data_testids"] = data_testids

        print(f"[dom] buttons={len(out['buttons'])} links={len(out['links'])} inputs={len(out['inputs'])}", flush=True)
        return out
    except Exception as e:
        print(f"[dom] UI collection failed url={url}: {type(e).__name__}: {e}", flush=True)
        return {"buttons": [], "links": [], "inputs": [], "data_testids": []}


async def crawl_dom_data(staging_url: str) -> Dict[str, Any]:
    """
    Launch Playwright once; return structured UI data:
      routes (internal <a href>), buttons (text + selector), links (text + href),
      inputs (placeholder/name), data_testids. Max 20 per category.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            routes = await _discover_routes(page, staging_url)
            home_url = staging_url.rstrip("/") + "/"
            ui = await _collect_ui_elements(page, home_url)

            await browser.close()

            return {
                "routes": routes or ["/"],
                "buttons": ui.get("buttons") or [],
                "links": ui.get("links") or [],
                "inputs": ui.get("inputs") or [],
                "data_testids": ui.get("data_testids") or [],
            }
    except Exception as e:
        print(f"[dom] crawl failed: {type(e).__name__}: {e}", flush=True)
        return {
            "routes": ["/"],
            "buttons": [],
            "links": [],
            "inputs": [],
            "data_testids": [],
        }

