

import re
from typing import Any, Dict, List, Optional, Set


VALID_ACTIONS = {"goto", "click", "screenshot", "assert_terminal"}



_PASSTHROUGH_FIELDS = (
    "success_condition",
    "validation_condition",
    "validation_source",
    "expected_url",
    "expected_testid",
    "terminal",
)


def _normalize_selector_quotes(selector: str) -> str:
    return re.sub(r'\[(\w[\w-]*)\s*=\s*"([^"]+)"\]', r"[\1='\2']", selector)


def validate_steps(steps: Any) -> List[Dict[str, Any]]:
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
    normalized: List[Dict[str, Any]] = []

    for step in steps:
        action = step.get("action")

        if action == "click":
            selector = (
                step.get("selector")
                or step.get("element")
                or step.get("target")
                or ""
            ).strip()
            label = (step.get("label") or "").strip()
            text = (step.get("text") or "").strip()

            if label:
                base: Dict[str, Any] = {"action": "click", "label": label}
            elif text:
                base = {"action": "click", "label": text}
            elif selector:
                base = {"action": "click", "selector": selector}
            else:

                print(
                    "[step_normalizer] dropped click step: "
                    "no label, text, or selector found",
                    flush=True,
                )
                continue



            for field in _PASSTHROUGH_FIELDS:
                val = step.get(field)
                if val is not None:
                    base[field] = val


            for field in ("dom_confirmed", "match_confidence",
                          "dom_warning", "contract_missing"):
                val = step.get(field)
                if val is not None:
                    base[field] = val

            normalized.append(base)

        elif action == "goto":
            url = (step.get("url", "/") or "/").strip()
            normalized.append({"action": "goto", "url": url})

        elif action == "screenshot":
            normalized.append(
                {
                    "action": "screenshot",
                    "label": step.get("label", ""),
                }
            )

        elif action == "assert_terminal":

            terminal_step: Dict[str, Any] = {"action": "assert_terminal"}
            for field in (
                "condition",
                "expected_url",
                "expected_text",
                "expected_element",
            ):
                val = step.get(field)
                if val is not None:
                    terminal_step[field] = val
            normalized.append(terminal_step)

    return normalized


def _extract_routes_from_diff(diff_files: List[Dict[str, str]]) -> Set[str]:
    routes: Set[str] = set()
    for f in diff_files:
        path = f.get("path", "")


        m = re.match(r"(?:src/)?app/(.+)/page\.[jt]sx?$", path)
        if m:
            route = "/" + m.group(1)
            route = re.sub(r"/index$", "", route)
            routes.add(route)
            continue


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
    contract: Optional[Any] = None,
) -> List[Dict[str, Any]]:




    valid_routes: Set[str] = set(dom_data.get("routes") or ["/"])
    valid_routes.add("/")

    if allowed_routes_override:
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

    for tid in dom_data.get("data_testids") or []:
        testid = (tid.get("testid") or "").strip()
        if testid:
            valid_selectors.add(f"[data-testid='{testid}']")

    if diff_files:
        inferred = _extract_routes_from_diff(diff_files)
        if allowed_routes_override:
            valid_routes |= inferred & valid_routes
        else:
            valid_routes |= inferred


    valid_texts_lower = {t.lower() for t in valid_texts}




    if contract is not None:
        try:
            for target in contract.targets or []:
                lbl = (target.label or "").strip()
                if lbl:
                    valid_texts.add(lbl)
                    valid_texts_lower.add(lbl.lower())
        except Exception:
            pass




    accepted: List[Dict[str, Any]] = []

    for step in steps:
        action = step.get("action")




        if action in {"screenshot", "assert_terminal"}:
            accepted.append(step)
            continue




        if action == "goto":
            url = (step.get("url") or "").strip()
            if url and url in valid_routes:
                accepted.append(step)
            else:
                print(
                    f"[step-validator] rejected goto url={url!r} "
                    f"(not in valid_routes)",
                    flush=True,
                )
            continue




        if action == "click":
            selector = (step.get("selector") or "").strip()
            selector_norm = _normalize_selector_quotes(selector) if selector else ""

            label = (step.get("label") or step.get("text") or "").strip()
            label_lower = label.lower()

            if selector_norm and selector_norm in valid_selectors:
                annotated = {
                    **step,
                    "dom_confirmed": True,
                    "match_confidence": "exact",
                }

            elif label and label in valid_texts:
                annotated = {
                    **step,
                    "dom_confirmed": True,
                    "match_confidence": "exact",
                }

            elif label_lower and label_lower in valid_texts_lower:
                annotated = {
                    **step,
                    "dom_confirmed": True,
                    "match_confidence": "high",
                }

            elif label_lower and any(
                label_lower in t or t in label_lower
                for t in valid_texts_lower
                if len(t) > 3 and len(label_lower) > 3
            ):
                annotated = {
                    **step,
                    "dom_confirmed": True,
                    "match_confidence": "low",
                }

            else:
                annotated = {
                    **step,
                    "dom_confirmed": False,
                    "match_confidence": "none",
                    "dom_warning": f"Label '{label}' not found in crawled DOM",
                }

                print(
                    f"[step-validator] unconfirmed click "
                    f"label={label!r} selector={selector!r} "
                    f"— keeping for pre-flight gate",
                    flush=True,
                )

            accepted.append(annotated)
            continue




        accepted.append(step)

    return accepted