from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from playwright.sync_api import Page

from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot, DomSnapshot

if TYPE_CHECKING:
    from app.browser.agent_browser_cli import AgentBrowserCLI


# Walk light DOM + open shadow roots so web-component UIs are not invisible
# to the planner / matcher.
_COLLECT_INTERACTIVE_JS = """
(maxItems) => {
  const out = [];
  const seen = new Set();

  const push = (e) => {
    if (!e || out.length >= maxItems) return;
    const key = (e.getAttribute && (
      e.getAttribute('data-testid')
      || e.id
      || e.getAttribute('aria-label')
      || ''
    )) + '|' + (e.innerText || e.value || '').trim().slice(0, 40);
    if (seen.has(key) && key !== '|') return;
    seen.add(key);
    out.push({
      role: (e.getAttribute('role') || (e.tagName || '').toLowerCase()).toLowerCase(),
      text: (e.innerText || e.value || '').trim().slice(0, 100),
      testid: e.getAttribute('data-testid') || '',
      aria: e.getAttribute('aria-label') || '',
      title: e.getAttribute('title') || '',
      id: e.id || '',
      selector: ''
    });
  };

  const matchesInteractive = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'button') return true;
    if (tag === 'a' && el.hasAttribute('href')) return true;
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      return t === 'button' || t === 'submit' || t === 'checkbox' || t === 'radio';
    }
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 'textbox'].includes(role)) {
      return true;
    }
    if (el.hasAttribute('data-testid') || el.hasAttribute('aria-label')) return true;
    return false;
  };

  const walk = (root) => {
    if (!root || out.length >= maxItems) return;
    const nodes = root.querySelectorAll
      ? root.querySelectorAll('*')
      : [];
    for (const el of nodes) {
      if (matchesInteractive(el)) push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
      if (out.length >= maxItems) return;
    }
  };

  walk(document);
  return out;
}
"""


_COLLECT_LINKS_JS = """
(maxItems) => {
  const out = [];
  const seen = new Set();
  const walk = (root) => {
    if (!root) return;
    const nodes = root.querySelectorAll ? root.querySelectorAll('a[href], [role="link"]') : [];
    for (const e of nodes) {
      const href = e.getAttribute('href') || '';
      const text = (e.innerText || '').trim().slice(0, 100);
      const key = href + '|' + text;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        text,
        href,
        testid: e.getAttribute('data-testid') || '',
        aria: e.getAttribute('aria-label') || '',
        id: e.id || ''
      });
      if (e.shadowRoot) walk(e.shadowRoot);
      if (out.length >= maxItems) return;
    }
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) {
      if (el.shadowRoot) walk(el.shadowRoot);
      if (out.length >= maxItems) return;
    }
  };
  walk(document);
  return out.slice(0, maxItems);
}
"""


_COLLECT_TESTIDS_JS = """
(maxItems) => {
  const out = [];
  const seen = new Set();
  const walk = (root) => {
    if (!root) return;
    const nodes = root.querySelectorAll ? root.querySelectorAll('[data-testid]') : [];
    for (const e of nodes) {
      const testid = e.getAttribute('data-testid') || '';
      if (!testid || seen.has(testid)) continue;
      seen.add(testid);
      out.push({
        testid,
        tag: (e.tagName || '').toLowerCase(),
        text: (e.innerText || '').trim().slice(0, 80)
      });
      if (out.length >= maxItems) return;
    }
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) {
      if (el.shadowRoot) walk(el.shadowRoot);
      if (out.length >= maxItems) return;
    }
  };
  walk(document);
  return out;
}
"""


def extract_dom_context(page: Page, *, max_items: int = 40) -> DomSnapshot:
    current_path = page.evaluate("() => window.location.pathname || '/'")

    buttons = page.evaluate(_COLLECT_INTERACTIVE_JS, max_items) or []
    links = page.evaluate(_COLLECT_LINKS_JS, max_items) or []
    testids = page.evaluate(_COLLECT_TESTIDS_JS, max_items * 2) or []

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

    headings = page.eval_on_selector_all(
        "h1, h2, h3, [role='heading']",
        f"""els => els.slice(0, {max_items}).map(e => (
            (e.innerText || "").trim().slice(0, 120)
        )).filter(Boolean)""",
    ) or []

    active_surfaces = page.eval_on_selector_all(
        "[role='dialog'], [role='tabpanel'], [aria-modal='true'], [data-testid], section, main",
        f"""els => els.slice(0, {max_items}).map(e => (
            e.getAttribute('aria-label')
            || e.getAttribute('data-testid')
            || e.getAttribute('id')
            || (e.tagName || '').toLowerCase()
        )).filter(Boolean)""",
    ) or []

    return {
        "current_path": current_path or "/",
        "routes": sorted(routes),
        "buttons": buttons,
        "links": links,
        "inputs": [],
        "data_testids": dedup_tids,
        "headings": [str(item).strip() for item in headings if str(item).strip()][:max_items],
        "active_surfaces": [str(item).strip() for item in active_surfaces if str(item).strip()][:max_items],
    }


def extract_ab_context(
    cli: "AgentBrowserCLI",
    *,
    save_raw: bool = True,
) -> AgentBrowserSnapshot:
    from app.browser.agent_browser_cli import AgentBrowserCLI as _CLI  # noqa: F401

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
