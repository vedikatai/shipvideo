from typing import List, Dict, Any

from playwright.async_api import async_playwright


async def _discover_routes(page, staging_url: str) -> List[str]:
    """
    Discover internal routes from a live staging/preview URL using an existing page.
    """
    try:
        print(f"🔍 [dom-ground] Discovering routes from {staging_url}", flush=True)
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
        print(f"✅ [dom-ground] Discovered routes: {routes}", flush=True)
        return routes

    except Exception as e:
        print(f"⚠️ [dom-ground] Route discovery failed: {type(e).__name__}: {e}", flush=True)
        return ["/"]


async def _get_accessibility_snapshot(page, url: str) -> Dict[str, Any]:
    """
    Collect a compact snapshot of interactive elements for a given URL
    using an existing page. Helps the LLM see real labels and roles.
    """
    try:
        print(f"🔍 [dom-ground] Getting accessibility snapshot for {url}", flush=True)
        await page.goto(url, timeout=15000)
        await page.wait_for_load_state("networkidle")

        # Collect raw element metadata in the page context
        buttons = await page.eval_on_selector_all(
            "button, [role='button']",
            """els => els.slice(0, 50).map(e => ({
                text: e.innerText || "",
                testid: e.getAttribute('data-testid') || "",
                aria: e.getAttribute('aria-label') || "",
                id: e.id || "",
                classes: e.className || ""
            }))""",
        )
        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.slice(0, 50).map(e => ({
                text: e.innerText || "",
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
                placeholder: e.getAttribute('placeholder') || "",
                type: e.getAttribute('type') || "",
                name: e.getAttribute('name') || "",
                testid: e.getAttribute('data-testid') || "",
                aria: e.getAttribute('aria-label') || "",
                id: e.id || "",
                classes: e.className || ""
            }))""",
        )

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

        snapshot: Dict[str, Any] = {
            "buttons": [],
            "links": [],
            "inputs": [],
        }

        for meta in buttons[:20]:
            snapshot["buttons"].append(
                {
                    "text": meta.get("text", ""),
                    "selector": _short_selector(meta, "button"),
                }
            )

        for meta in links[:20]:
            snapshot["links"].append(
                {
                    "text": meta.get("text", ""),
                    "href": meta.get("href", ""),
                }
            )

        for meta in inputs[:20]:
            snapshot["inputs"].append(
                {
                    "placeholder": meta.get("placeholder", ""),
                    "selector": _short_selector(meta, "input"),
                }
            )

        return snapshot
    except Exception as e:
        print(f"⚠️ [dom-ground] Snapshot failed for {url}: {type(e).__name__}: {e}", flush=True)
        return {}


async def crawl_dom_data(staging_url: str) -> Dict[str, Any]:
    """
    Launch Playwright once to collect:
      - routes: discovered internal routes
      - snapshot: accessibility tree for the home page
    """
    from urllib.parse import urljoin

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            routes = await _discover_routes(page, staging_url)
            home_url = staging_url.rstrip("/") + "/"
            snapshot = await _get_accessibility_snapshot(page, home_url)

            await browser.close()

            return {
                "routes": routes or ["/"],
                "snapshot": snapshot or {},
            }
    except Exception as e:
        print(f"⚠️ [dom-ground] crawl_dom_data failed: {type(e).__name__}: {e}", flush=True)
        return {
            "routes": ["/"],
            "snapshot": {},
        }

