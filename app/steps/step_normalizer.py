import re
from typing import Any, Dict, List, Optional, Set


VALID_ACTIONS = {"goto", "click", "screenshot"}


def _normalize_selector_quotes(selector: str) -> str:
    """Normalize attribute selector to single-quote form for consistent set membership.

    The crawler generates selectors with single quotes: [data-testid='x'].
    The LLM may output double quotes: [data-testid="x"].
    Without normalization, validate_against_dom rejects perfectly valid steps.
    """
    return re.sub(r'\[(\w[\w-]*)\s*=\s*"([^"]+)"\]', r"[\1='\2']", selector)


def validate_steps(steps: Any) -> List[Dict[str, Any]]:
    """
    Keep only steps with a known action to avoid executor crashes.
    """
    if not isinstance(steps, list):
        return []

    valid: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if action in VALID_ACTIONS:
            valid.append(step)
    return valid


def normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize heterogeneous LLM step shapes into the minimal executor format.
    """
    normalized: List[Dict[str, Any]] = []

    for step in steps:
        action = step.get("action")

        if action == "click":
            selector = (step.get("selector") or step.get("element") or step.get("target") or "").strip()
            text = (step.get("text") or "").strip()
            if selector:
                normalized.append({"action": "click", "selector": selector})
            elif text:
                normalized.append({"action": "click", "text": text})

        elif action == "goto":
            url = (step.get("url", "/") or "/").strip()
            normalized.append(
                {
                    "action": "goto",
                    "url": url,
                }
            )

        elif action == "screenshot":
            normalized.append(
                {
                    "action": "screenshot",
                    "label": step.get("label", ""),
                }
            )

    return normalized


def _extract_routes_from_diff(diff_files: List[Dict[str, str]]) -> Set[str]:
    """
    Derive likely URL routes from changed file paths.

    This is a best-effort heuristic so "new routes" added by the PR are not
    rejected just because the homepage crawl didn't include them.
    """
    routes: Set[str] = set()
    for f in diff_files:
        path = f.get("path", "")

        # Next.js app router: app/foo/bar/page.[jt]sx? -> /foo/bar
        m = re.match(r"(?:src/)?app/(.+)/page\.[jt]sx?$", path)
        if m:
            route = "/" + m.group(1)
            route = re.sub(r"/index$", "", route)
            routes.add(route)
            continue

        # Next.js pages router: pages/foo/[id].tsx -> /foo/[id]
        m = re.match(r"(?:src/)?pages/(.+)\.[jt]sx?$", path)
        if m:
            slug = m.group(1)
            if slug and not slug.startswith("_") and not slug.startswith("api/"):
                route = "/" + slug
                route = re.sub(r"/index$", "", route)
                routes.add(route)

    return routes


def validate_against_dom(
    steps: List[Dict[str, Any]],
    dom_data: Dict[str, Any],
    diff_files: Optional[List[Dict[str, str]]] = None,
    *,
    allowed_routes_override: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Hard-reject steps whose targets don't exist in the live DOM snapshot.

    - goto  → URL must appear in crawled routes/links OR be derivable from diff
    - click → selector must match a known selector OR visible text must match
    - screenshot → always passes
    """
    # Build sets of valid targets from the DOM crawl.
    valid_routes: Set[str] = set(dom_data.get("routes") or ["/"])
    valid_routes.add("/")

    if allowed_routes_override:
        # In "route override" mode we restrict routes to the chosen one(s) so
        # the model can't wander.
        valid_routes = set(allowed_routes_override) | {"/"}

    valid_selectors: Set[str] = set()
    valid_texts: Set[str] = set()

    for btn in dom_data.get("buttons") or []:
        sel = (btn.get("selector") or "").strip()
        if sel:
            valid_selectors.add(sel)
        txt = (btn.get("text") or "").strip()
        if txt:
            valid_texts.add(txt)

    for link in dom_data.get("links") or []:
        href = (link.get("href") or "").strip()
        if href:
            valid_routes.add(href)
        txt = (link.get("text") or "").strip()
        if txt:
            valid_texts.add(txt)

    # data_testids: dom_crawler returns objects like {testid, tag, text}
    for tid in dom_data.get("data_testids") or []:
        testid = (tid.get("testid") or "").strip()
        if testid:
            valid_selectors.add(f"[data-testid='{testid}']")

    if diff_files:
        inferred = _extract_routes_from_diff(diff_files)
        if allowed_routes_override:
            # When restricting routes (route override), don't widen the valid
            # set with unrelated inferred routes.
            valid_routes |= (inferred & valid_routes)
        else:
            valid_routes |= inferred

    accepted: List[Dict[str, Any]] = []
    for step in steps:
        action = step.get("action")

        if action == "screenshot":
            accepted.append(step)
            continue

        if action == "goto":
            url = (step.get("url") or "").strip()
            if url and url in valid_routes:
                accepted.append(step)
            else:
                print(
                    f"[step-validator] rejected goto url={url!r} (not in valid_routes)",
                    flush=True,
                )
            continue

        if action == "click":
            selector = (step.get("selector") or "").strip()
            # Normalize quote style before set lookup so [data-testid="x"]
            # matches the single-quote form stored by _short_selector.
            selector_norm = _normalize_selector_quotes(selector) if selector else ""
            text = (step.get("text") or "").strip()
            if (selector_norm and selector_norm in valid_selectors) or (text and text in valid_texts):
                accepted.append(step)
            else:
                print(
                    "[step-validator] rejected click "
                    f"selector={selector!r} text={text!r} (not in live DOM)",
                    flush=True,
                )
            continue

        # Unknown action: keep; validate_steps later will drop it.
        accepted.append(step)

    return accepted

