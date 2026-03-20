"""
Step execution: run capture steps (goto, click, screenshot) against a preview URL via Playwright.

Production-grade responsibilities:
- Deterministic timing hardening (element-level waits, bounded stability checks)
- Accessibility-first selector strategy with scoring/logging
- Visual fallback click (last resort) when selectors fail
- Self-healing retry loop (max 3) with LLM feedback that replaces ONLY the failing step
- Strict failure behavior: never produce misleading successful recordings

This module is intentionally verbose in structured logging to make debugging reliable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeoutError

from observability import pipeline_step
from app.config import load_config
from app.execution.step_runner import run_stepwise

BASE_APP_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR = SCREENSHOT_DIR / "debug"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_STEPS: List[Dict[str, Any]] = [
    {"action": "screenshot"},
    {"action": "screenshot"},
]

MAX_STEP_RETRIES = 3

# Base timeouts are tuned for CI and real-world B2B apps; they are further bounded
# by element presence and stability checks.
GOTO_TIMEOUT_MS = 15000
CLICK_TIMEOUT_MS = 10000
STABILITY_POLL_MS = 250
STABILITY_CHECKS = 6  # ~1.5s stability window


@dataclass
class CaptureSettings:
    viewport_width: int = 1280
    viewport_height: int = 720
    full_page_screenshots: bool = False
    full_page_debug_screenshots: bool = True


def _load_capture_settings() -> CaptureSettings:
    cfg = load_config()
    capture_cfg = cfg.get("capture") or {}
    viewport_cfg = capture_cfg.get("viewport") or {}
    return CaptureSettings(
        viewport_width=int(viewport_cfg.get("width", 1280)),
        viewport_height=int(viewport_cfg.get("height", 720)),
        full_page_screenshots=bool(capture_cfg.get("full_page_screenshots", False)),
        full_page_debug_screenshots=bool(capture_cfg.get("full_page_debug_screenshots", True)),
    )


def _log_json(event: str, payload: Dict[str, Any]) -> None:
    msg = {"event": event, **payload}
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def _resolve_url(preview_url: str, path: str) -> str:
    """Resolve a possibly-relative URL against the preview base URL."""
    if not path:
        return preview_url
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return preview_url.rstrip("/") + "/" + path.lstrip("/")


def _trim_text(s: str, max_chars: int) -> str:
    s = s or ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n... (trimmed)"


def _safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _capture_page_screenshot(page: Page, path: Path, *, full_page: bool) -> None:
    page.screenshot(path=str(path), full_page=full_page)


def _short_error_message(err_msg: str, max_chars: int = 240) -> str:
    msg = (err_msg or "").replace("\n", " ").strip()
    if len(msg) <= max_chars:
        return msg
    return msg[:max_chars] + "..."


def _compact_runtime_dom_context(runtime_dom_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Keep retry payload small and high-signal for LLM repair.
    """
    ctx = runtime_dom_context or {}
    buttons = ctx.get("buttons") or []
    links = ctx.get("links") or []
    testids = ctx.get("data_testids") or []
    # Keep top-N meaningful entries only.
    compact_buttons = []
    for b in buttons[:12]:
        compact_buttons.append(
            {
                "text": (b.get("text") or "").strip()[:80],
                "testid": (b.get("testid") or "").strip(),
                "aria": (b.get("aria") or "").strip()[:80],
                "id": (b.get("id") or "").strip(),
            }
        )
    compact_links = []
    for l in links[:12]:
        compact_links.append(
            {
                "text": (l.get("text") or "").strip()[:80],
                "href": (l.get("href") or "").strip(),
            }
        )
    compact_tids = []
    for t in testids[:20]:
        compact_tids.append(
            {
                "testid": (t.get("testid") or "").strip(),
                "tag": (t.get("tag") or "").strip(),
                "text": (t.get("text") or "").strip()[:60],
            }
        )
    return {
        "current_path": (ctx.get("current_path") or "/").strip() or "/",
        "buttons": compact_buttons,
        "links": compact_links,
        "data_testids": compact_tids,
    }


def _wait_dom_ready(page: Page, timeout_ms: int) -> None:
    """Wait for a deterministic readiness signal (domcontentloaded + readyState)."""
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            page.wait_for_function("document.readyState === 'complete'", timeout=5000)
            return
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.1)
    if last_err:
        raise RuntimeError(f"DOM ready check failed: {last_err}")


def _wait_locator_stable(page: Page, locator, *, timeout_ms: int) -> None:
    """
    Stability heuristic:
    - bounding box exists
    - bounding box position/size doesn't change across several polls
    """
    end = time.monotonic() + timeout_ms / 1000.0
    last_box: Optional[Dict[str, float]] = None
    stable_hits = 0

    while time.monotonic() < end:
        try:
            box = locator.bounding_box()
            if not box:
                stable_hits = 0
                last_box = None
                time.sleep(STABILITY_POLL_MS / 1000.0)
                continue
            # Normalize keys for comparison.
            box_norm = {
                "x": round(float(box.get("x", 0.0)), 1),
                "y": round(float(box.get("y", 0.0)), 1),
                "w": round(float(box.get("width", 0.0)), 1),
                "h": round(float(box.get("height", 0.0)), 1),
            }
            if last_box == box_norm:
                stable_hits += 1
            else:
                stable_hits = 0
            last_box = box_norm
            if stable_hits >= STABILITY_CHECKS:
                return
        except Exception:
            stable_hits = 0
        time.sleep(STABILITY_POLL_MS / 1000.0)

    raise TimeoutError("Locator stability check timed out")


def _selector_strategy_from_step(selector: str) -> List[Tuple[str, str]]:
    """
    Convert a step selector into a ranked set of strategies.

    Returns list of (strategy_name, value) where value is used by Playwright APIs.
    """
    s = (selector or "").strip()
    if not s:
        return []

    # data-testid style: [data-testid='x'] or [data-testid="x"]
    m = re.match(r"^\[data-testid=(['\"])(.+?)\1\]$", s)
    if m:
        return [("test_id", m.group(2))]

    # aria-label style: [aria-label='x'] or [aria-label="x"]
    m = re.match(r"^\[aria-label=(['\"])(.+?)\1\]$", s)
    if m:
        return [("aria_label", m.group(2))]

    # css id
    if s.startswith("#"):
        return [("css_id", s[1:]), ("css", s)]

    return [("css", s)]


def _compute_click_confidence(step: Dict[str, Any]) -> float:
    """
    Coarse confidence scoring:
    - testid/aria-label selectors are most stable
    - css/id is medium
    - text-only is least stable
    """
    if (step.get("selector") or "").strip():
        selector = (step.get("selector") or "").strip()
        if selector.startswith("[data-testid=") and "]" in selector:
            return 0.95
        if selector.startswith("[aria-label=") and "]" in selector:
            return 0.85
        if selector.startswith("#"):
            return 0.7
        return 0.5
    if (step.get("text") or "").strip():
        return 0.45
    return 0.0


def _extract_step_descriptor(step: Dict[str, Any]) -> Dict[str, str]:
    return {
        "selector": (step.get("selector") or "").strip(),
        "text": (step.get("text") or "").strip(),
        "label": (step.get("label") or "").strip(),
    }


def _get_azure_openai_client() -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        raise RuntimeError(f"OpenAI client not available: {e}")

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not azure_endpoint or not azure_key:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set")

    base_url = azure_endpoint.rstrip("/")
    if not base_url.endswith("openai/v1"):
        base_url = base_url + "/openai/v1/"

    return OpenAI(base_url=base_url, api_key=azure_key)


def _call_llm_replace_step(
    *,
    messages: List[Dict[str, str]],
    max_tokens: int,
) -> Dict[str, Any]:
    """
    Call Azure OpenAI with strict json_schema to get replacement steps.
    """
    client = _get_azure_openai_client()
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not azure_deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT must be set")

    replacement_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "replacement_steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["goto", "click", "screenshot"]},
                        "url": {"type": "string"},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["action", "url", "selector", "text", "label"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["replacement_steps"],
        "additionalProperties": False,
    }

    # Structured output where supported; fallback to json_object not implemented
    # here because execution-time regeneration must not silently degrade.
    completion = client.chat.completions.create(
        model=azure_deployment,
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "replacement_steps",
                "strict": True,
                "schema": replacement_schema,
            },
        },
    )
    content = completion.choices[0].message.content or "{}"
    return json.loads(content)


def _click_with_strategies(page: Page, step: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Try click via selector/text strategies.
    Returns (ok, strategy_name).
    """
    selector = (step.get("selector") or "").strip()
    text = (step.get("text") or "").strip()

    # Strategy: selector-based
    if selector:
        strategies = _selector_strategy_from_step(selector)
        for name, value in strategies:
            try:
                if name == "test_id":
                    loc = page.get_by_test_id(value)
                elif name == "aria_label":
                    loc = page.get_by_label(value)
                elif name == "css_id":
                    loc = page.locator(f"#{value}")
                elif name == "css":
                    loc = page.locator(value)
                else:
                    continue

                loc.wait_for(state="visible", timeout=CLICK_TIMEOUT_MS)
                _wait_locator_stable(page, loc, timeout_ms=int(CLICK_TIMEOUT_MS * 0.8))
                loc.click(timeout=CLICK_TIMEOUT_MS)
                return True, name
            except Exception:
                continue

    # Strategy: text-based
    if text:
        for exact in (True, False):
            try:
                loc = page.get_by_text(text, exact=exact)
                loc.first.wait_for(state="visible", timeout=CLICK_TIMEOUT_MS)
                _wait_locator_stable(page, loc.first, timeout_ms=int(CLICK_TIMEOUT_MS * 0.8))
                loc.first.click(timeout=CLICK_TIMEOUT_MS)
                return True, f"text_exact={exact}"
            except Exception:
                continue

    return False, "all_strategies_failed"


def _visual_fallback_click(
    page: Page,
    *,
    step: Dict[str, Any],
    screenshot_path: Path,
    screenshot_full_page: bool,
    max_tokens: int,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Last-resort visual fallback: identify the element in the screenshot and click by coordinates.

    Requires:
    - AZURE_OPENAI_VISION_DEPLOYMENT set
    """
    vision_deployment = os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT")
    if not vision_deployment:
        raise RuntimeError("visual_fallback_unavailable: AZURE_OPENAI_VISION_DEPLOYMENT is not set")

    client = _get_azure_openai_client()
    # Capture fresh screenshot in the desired mode (viewport vs full-page)
    _capture_page_screenshot(page, screenshot_path, full_page=screenshot_full_page)
    img_bytes = screenshot_path.read_bytes()
    b64 = base64.b64encode(img_bytes).decode("ascii")

    descriptor = _extract_step_descriptor(step)
    prompt = (
        "You are a UI vision assistant. Identify the element that matches this descriptor and provide "
        "a bounding box in normalized coordinates relative to the screenshot.\n\n"
        f"Descriptor: {json.dumps(descriptor, ensure_ascii=False)}\n\n"
        "Return ONLY JSON with schema:\n"
        "{ \"x\": number, \"y\": number, \"width\": number, \"height\": number, \"confidence\": number }\n"
        "Where x,y,width,height are in [0,1].\n"
        "x,y represent top-left of the box."
    )

    # Note: we intentionally do not use json_schema here to keep compatibility. We'll parse manually.
    completion = client.chat.completions.create(
        model=vision_deployment,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    content = completion.choices[0].message.content or "{}"
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    bbox = json.loads(content)

    x = float(bbox.get("x"))
    y = float(bbox.get("y"))
    w = float(bbox.get("width"))
    h = float(bbox.get("height"))
    conf = bbox.get("confidence")
    conf_f = float(conf) if conf is not None else None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and w > 0 and h > 0):
        raise ValueError(f"Vision bbox out of range: {bbox}")

    vp = page.viewport_size or {"width": 1280, "height": 720}
    center_x_norm = x + w / 2.0
    center_y_norm = y + h / 2.0

    if screenshot_full_page:
        # Map normalized bbox coordinates to document coordinates, scroll there,
        # then click in viewport coordinates.
        dims = page.evaluate(
            """() => ({
                docWidth: Math.max(
                    document.documentElement.scrollWidth || 0,
                    document.body ? document.body.scrollWidth : 0,
                    window.innerWidth || 0
                ),
                docHeight: Math.max(
                    document.documentElement.scrollHeight || 0,
                    document.body ? document.body.scrollHeight : 0,
                    window.innerHeight || 0
                ),
                innerWidth: window.innerWidth || 0,
                innerHeight: window.innerHeight || 0
            })"""
        )
        doc_w = float(dims.get("docWidth") or vp["width"])
        doc_h = float(dims.get("docHeight") or vp["height"])
        inner_w = float(dims.get("innerWidth") or vp["width"])
        inner_h = float(dims.get("innerHeight") or vp["height"])

        page_x = center_x_norm * doc_w
        page_y = center_y_norm * doc_h

        target_scroll_x = max(0.0, min(page_x - inner_w / 2.0, max(0.0, doc_w - inner_w)))
        target_scroll_y = max(0.0, min(page_y - inner_h / 2.0, max(0.0, doc_h - inner_h)))
        page.evaluate(
            "(sx, sy) => { window.scrollTo({ left: sx, top: sy, behavior: 'instant' }); }",
            target_scroll_x,
            target_scroll_y,
        )
        time.sleep(0.15)
        scroll_pos = page.evaluate("() => ({ x: window.scrollX || 0, y: window.scrollY || 0 })")
        click_x = page_x - float(scroll_pos.get("x") or 0.0)
        click_y = page_y - float(scroll_pos.get("y") or 0.0)
    else:
        click_x = center_x_norm * vp["width"]
        click_y = center_y_norm * vp["height"]

    page.mouse.click(click_x, click_y)
    if conf_f is not None:
        bbox["confidence"] = conf_f
    bbox["click_x"] = float(click_x)
    bbox["click_y"] = float(click_y)
    bbox["screenshot_full_page"] = bool(screenshot_full_page)
    return True, bbox


def _capture_dom_snapshot(page: Page, *, max_chars: int = 6000) -> str:
    try:
        html = page.content()
        return _trim_text(html, max_chars=max_chars)
    except Exception as e:
        return f"<dom_snapshot_failed: {type(e).__name__}: {e}>"


def _collect_runtime_dom_context(page: Page) -> Dict[str, Any]:
    """
    Collect a fresh, lightweight DOM context from the current page state.
    Used for retry-time LLM repair so we don't rely only on stale generation-time crawl.
    """
    out: Dict[str, Any] = {
        "current_path": "",
        "buttons": [],
        "links": [],
        "data_testids": [],
    }
    try:
        out["current_path"] = page.evaluate("() => window.location.pathname || '/'") or "/"
    except Exception:
        out["current_path"] = "/"

    try:
        buttons = page.eval_on_selector_all(
            "button, [role='button']",
            """els => els.slice(0, 50).map(e => ({
                text: (e.innerText || "").trim().slice(0, 80),
                testid: e.getAttribute('data-testid') || "",
                aria: e.getAttribute('aria-label') || "",
                id: e.id || ""
            }))""",
        ) or []
    except Exception:
        buttons = []

    try:
        links = page.eval_on_selector_all(
            "a[href]",
            """els => els.slice(0, 50).map(e => ({
                text: (e.innerText || "").trim().slice(0, 80),
                href: e.getAttribute('href') || "",
                testid: e.getAttribute('data-testid') || "",
                aria: e.getAttribute('aria-label') || "",
                id: e.id || ""
            }))""",
        ) or []
    except Exception:
        links = []

    try:
        tids = page.eval_on_selector_all(
            "[data-testid]",
            """els => els.slice(0, 80).map(e => ({
                testid: e.getAttribute('data-testid') || "",
                tag: e.tagName.toLowerCase(),
                text: (e.innerText || "").trim().slice(0, 60)
            }))""",
        ) or []
    except Exception:
        tids = []

    out["buttons"] = buttons
    out["links"] = links
    out["data_testids"] = tids
    return out


def _ensure_valid_step_or_raise(step: Dict[str, Any], idx: int) -> None:
    action = step.get("action")
    if action not in {"goto", "click", "screenshot"}:
        raise ValueError(f"Invalid step action at idx={idx}: {action!r}")
    if action == "goto" and not (step.get("url") or "").strip():
        raise ValueError(f"Invalid goto step at idx={idx}: missing url")
    if action == "click":
        if not (step.get("selector") or "").strip() and not (step.get("text") or "").strip():
            raise ValueError(f"Invalid click step at idx={idx}: missing selector and text")
    if action == "screenshot" and not isinstance(step.get("label", ""), str):
        # label is optional for execution but schema expects string
        raise ValueError(f"Invalid screenshot step at idx={idx}: label type")


def _validate_steps_strict(steps: List[Dict[str, Any]], dom_data: Dict[str, Any], diff_files: Optional[List[Dict[str, str]]]) -> None:
    from app.steps.step_normalizer import validate_against_dom, validate_steps
    normalized = validate_steps(steps)
    validated = validate_against_dom(normalized, dom_data, diff_files)
    if len(validated) != len(normalized):
        raise RuntimeError(
            "Strict validation failed: some steps do not exist in the live DOM "
            f"(accepted={len(validated)} expected={len(normalized)})"
        )


def _replace_failing_step_with_llm(
    *,
    generation_context: Dict[str, Any],
    failing_step: Dict[str, Any],
    failing_step_index: int,
    error_context: Dict[str, Any],
    diff_files_for_prompt: List[Dict[str, str]],
    dom_data: Dict[str, Any],
    runtime_dom_context: Optional[Dict[str, Any]],
    retry_history: Optional[List[Dict[str, Any]]],
    max_tokens: int,
    max_replacement_steps: int = 2,
) -> List[Dict[str, Any]]:
    """
    Ask the LLM to output ONLY replacement steps for the failing step index.
    """
    diffs_for_prompt = diff_files_for_prompt
    compact_runtime_ctx = _compact_runtime_dom_context(runtime_dom_context)
    runtime_path = compact_runtime_ctx.get("current_path") or "/"
    runtime_buttons = compact_runtime_ctx.get("buttons") or []
    runtime_links = compact_runtime_ctx.get("links") or []
    runtime_testids = compact_runtime_ctx.get("data_testids") or []

    real_routes = (
        generation_context.get("real_routes")
        or dom_data.get("routes")
        or ["/"]
    )
    real_routes = list(set(real_routes + [runtime_path]))

    # Prefer fresh runtime DOM context for repair to avoid stale-crawl retries.
    real_buttons = runtime_buttons or generation_context.get("real_buttons") or dom_data.get("buttons") or []
    real_links = runtime_links or generation_context.get("real_links") or dom_data.get("links") or []
    data_testids = runtime_testids or generation_context.get("data_testids") or dom_data.get("data_testids") or []
    real_inputs = generation_context.get("real_inputs") or dom_data.get("inputs") or []
    start_route = generation_context.get("start_route")

    system_msg = (
        "You are a demo-flow self-healing assistant.\n"
        "A previously generated demo step failed. You must output ONLY replacement_steps "
        "for the failing step index.\n\n"
        "Rules:\n"
        "• Use ONLY routes from real_routes for goto.\n"
        "• For click, use ONLY selectors from real_buttons or data_testids (prefer [data-testid='x']), "
        "OR exact visible text that appears in real_buttons/real_links.\n"
        "• Prefer text or stable testid selectors over brittle CSS id/class selectors when uncertain.\n"
        "• Set unused fields to empty string ''.\n"
        "• Never invent selectors or routes not present in the provided DOM crawl.\n"
        "• Return strictly valid JSON.\n"
    )

    user_msg = {
        "failing_step_index": failing_step_index,
        "failing_step": failing_step,
        "error_context": error_context,
        "runtime_dom_context": compact_runtime_ctx,
        "retry_history": retry_history or [],
        "real_routes": real_routes,
        "real_buttons": real_buttons,
        "real_links": real_links,
        "real_inputs": real_inputs,
        "data_testids": data_testids,
        "start_route": start_route,
        "diff_files_for_prompt": diffs_for_prompt,
    }

    messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": json.dumps(user_msg, ensure_ascii=False)}]
    data = _call_llm_replace_step(messages=messages, max_tokens=max_tokens)

    replacement = data.get("replacement_steps") or []
    if not isinstance(replacement, list) or len(replacement) == 0 or len(replacement) > max_replacement_steps:
        raise ValueError(f"LLM returned invalid replacement_steps: {replacement!r}")

    # Normalize replacement steps to executor format (supports selector/text click).
    from app.steps.step_normalizer import normalize_steps, validate_steps
    replacement_valid = validate_steps(replacement)
    replacement_normalized = normalize_steps(replacement_valid)

    return replacement_normalized


@pipeline_step("step_execution")
def run_capture(
    preview_url: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    screenshot_dir: Optional[Path] = None,
    generation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute capture steps against the preview URL and write screenshots to disk.

    Args:
        preview_url: Base URL of the preview deployment.
        steps: List of step dicts (action: goto | click | screenshot, plus url/selector/text).
        screenshot_dir: Directory for shot*.png files; defaults to app/screenshots directory.

    Returns:
        Dict with steps_succeeded, steps_failed, failure_reason, success, debug.
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    steps = steps or DEFAULT_STEPS
    out_dir = screenshot_dir or SCREENSHOT_DIR
    capture_settings = _load_capture_settings()

    # New mandatory execution model: step-by-step with navigation boundary
    # detection + re-anchoring after major DOM changes.
    objective = {
        "goal": "Generate reliable demo actions from current DOM only",
        "generation_context": generation_context or {},
    }
    stepwise = run_stepwise(
        preview_url=preview_url,
        initial_steps=steps,
        objective=objective,
        screenshot_dir=out_dir,
        max_retries_per_failure=MAX_STEP_RETRIES,
    )
    if stepwise.get("success"):
        return {
            "steps_succeeded": int(stepwise.get("steps_succeeded", 0)),
            "steps_failed": int(stepwise.get("steps_failed", 0)),
            "failure_reason": None,
            "success": True,
            "debug": {"engine": "stepwise", "results": stepwise.get("results", [])},
        }
    return {
        "steps_succeeded": 0,
        "steps_failed": 1,
        "failure_reason": stepwise.get("failure_reason") or "stepwise_execution_failed",
        "success": False,
        "debug": {"engine": "stepwise", "results": stepwise.get("results", [])},
    }

    for old_shot in out_dir.glob("shot*.png"):
        old_shot.unlink()

    step_results: List[bool] = []
    last_failure_reason: Optional[str] = None
    debug: Dict[str, Any] = {"steps": []}

    # Strict pre-validation: if we have generation_context, validate steps exist.
    dom_data = generation_context.get("dom_data") if generation_context else None
    diffs_for_prompt = generation_context.get("diffs_for_prompt") if generation_context else None
    if generation_context:
        if not dom_data:
            raise ValueError("generation_context.dom_data missing")
        if "diffs_for_prompt" in generation_context:
            diffs_for_prompt = generation_context["diffs_for_prompt"]
        else:
            diffs_for_prompt = None
        _validate_steps_strict(steps, dom_data, diffs_for_prompt)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={
                "width": capture_settings.viewport_width,
                "height": capture_settings.viewport_height,
            }
        )
        page.goto(preview_url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        _wait_dom_ready(page, timeout_ms=GOTO_TIMEOUT_MS)

        screenshot_index = 1
        working_steps: List[Dict[str, Any]] = list(steps)

        i = 0
        while i < len(working_steps):
            step = working_steps[i]
            action = step.get("action")
            _ensure_valid_step_or_raise(step, i)

            step_debug: Dict[str, Any] = {
                "index": i,
                "action": action,
                "step": step,
                "attempts": [],
            }

            step_ok = False
            for attempt in range(1, MAX_STEP_RETRIES + 1):
                attempt_ctx: Dict[str, Any] = {"attempt": attempt}
                try:
                    _log_json("execution.step_start", {"step_index": i, "attempt": attempt, "action": action})

                    if action == "screenshot":
                        path = out_dir / f"shot{screenshot_index}.png"
                        _log_json("execution.screenshot", {"step_index": i, "path": path.name})
                        _capture_page_screenshot(
                            page,
                            path,
                            full_page=capture_settings.full_page_screenshots,
                        )
                        screenshot_index += 1
                        step_ok = True
                        break

                    if action == "goto":
                        target = (step.get("url") or "").strip()
                        resolved = _resolve_url(preview_url, target)
                        expected_path = resolved
                        _log_json("execution.goto", {"step_index": i, "resolved": resolved})
                        page.goto(resolved, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                        _wait_dom_ready(page, timeout_ms=GOTO_TIMEOUT_MS)
                        step_ok = True
                        break

                    if action == "click":
                        conf = _compute_click_confidence(step)
                        if conf < 0.2:
                            raise RuntimeError(f"Low-confidence click step (confidence={conf:.2f}): {step!r}")

                        ok, strategy = _click_with_strategies(page, step)
                        if not ok:
                            # Last resort visual fallback.
                            _log_json(
                                "execution.selector_strategies_failed",
                                {"step_index": i, "selector": step.get("selector"), "text": step.get("text"), "strategy": strategy},
                            )
                            # Visual fallback
                            shot_path = DEBUG_DIR / f"step{i}_attempt{attempt}_visual.png"
                            # Visual fallback can use full-page screenshot mode to
                            # find elements outside viewport.
                            _capture_page_screenshot(
                                page,
                                shot_path,
                                full_page=capture_settings.full_page_screenshots,
                            )
                            _log_json("execution.visual_fallback_triggered", {"step_index": i, "shot": shot_path.name})
                            if os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT"):
                                vision_ok, bbox = _visual_fallback_click(
                                    page,
                                    step=step,
                                    screenshot_path=shot_path,
                                    screenshot_full_page=capture_settings.full_page_screenshots,
                                    max_tokens=500,
                                )
                                if not vision_ok:
                                    raise RuntimeError("Visual fallback click failed")
                                step_ok = True
                                attempt_ctx["visual_bbox"] = bbox
                                break
                            else:
                                attempt_ctx["visual_fallback"] = "unavailable"
                                raise RuntimeError(
                                    "Selector strategies failed and visual fallback is unavailable "
                                    "(AZURE_OPENAI_VISION_DEPLOYMENT not set)"
                                )

                        _log_json(
                            "execution.click_success",
                            {
                                "step_index": i,
                                "confidence": conf,
                                "strategy": strategy,
                            },
                        )

                        # After click: wait for DOM/body stability (bounded).
                        # We do not rely on networkidle.
                        previous_text = None
                        for _ in range(10):
                            try:
                                txt = page.evaluate("() => document.body ? document.body.innerText : ''")
                                if txt == previous_text and txt:
                                    break
                                previous_text = txt
                            except Exception:
                                break
                            time.sleep(0.25)

                        step_ok = True
                        break

                    raise RuntimeError(f"Unknown action at runtime: {action!r}")

                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    last_failure_reason = err_msg
                    attempt_ctx["error"] = err_msg
                    dom_snapshot = _capture_dom_snapshot(page)
                    dom_hash = hashlib.sha256(dom_snapshot.encode("utf-8", errors="ignore")).hexdigest()[:12]
                    attempt_ctx["dom_snapshot_hash"] = dom_hash
                    attempt_ctx["dom_snapshot_chars"] = len(dom_snapshot)
                    screenshot_path = DEBUG_DIR / f"step{i}_attempt{attempt}_failure.png"
                    try:
                        _capture_page_screenshot(
                            page,
                            screenshot_path,
                            full_page=capture_settings.full_page_debug_screenshots,
                        )
                        attempt_ctx["failure_screenshot"] = screenshot_path.name
                    except Exception:
                        attempt_ctx["failure_screenshot"] = None

                    _log_json(
                        "execution.step_failed",
                        {"step_index": i, "attempt": attempt, "action": action, "error": err_msg},
                    )

                    # Retry with LLM feedback if we have generation_context.
                    if attempt < MAX_STEP_RETRIES:
                        if not generation_context:
                            raise RuntimeError("LLM retry requires generation_context but it was not provided")

                        compact_retry_history: List[Dict[str, Any]] = []
                        for a in step_debug.get("attempts", []):
                            compact_retry_history.append(
                                {
                                    "attempt": a.get("attempt"),
                                    "error": _short_error_message(str(a.get("error") or "")),
                                    "visual_fallback": a.get("visual_fallback"),
                                }
                            )

                        error_context = {
                            "failed_step_index": i,
                            "failed_step": step,
                            "error_message": _short_error_message(err_msg),
                            "selector_used": (step.get("selector") or "").strip() or (step.get("text") or "").strip(),
                            "dom_snapshot_hash": dom_hash,
                            "dom_snapshot_preview": _trim_text(dom_snapshot, 1200),
                            "failure_screenshot_path": str(attempt_ctx.get("failure_screenshot") or ""),
                        }

                        trace_path = DEBUG_DIR / f"step{i}_attempt{attempt}_trace.json"
                        try:
                            trace_path.write_text(
                                json.dumps(
                                    {
                                        "step_index": i,
                                        "attempt": attempt,
                                        "failed_step": step,
                                        "error_context": error_context,
                                    },
                                    ensure_ascii=False,
                                    default=str,
                                ),
                                encoding="utf-8",
                            )
                            attempt_ctx["trace_file"] = trace_path.name
                        except Exception:
                            pass

                        _log_json(
                            "execution.llm_retry_triggered",
                            {"step_index": i, "attempt": attempt, "error": err_msg},
                        )

                        # Retry replacement
                        replacement_steps = _replace_failing_step_with_llm(
                            generation_context=generation_context,
                            failing_step=step,
                            failing_step_index=i,
                            error_context=error_context,
                            diff_files_for_prompt=generation_context.get("diffs_for_prompt") or [],
                            dom_data=generation_context.get("dom_data") or {},
                            runtime_dom_context=_collect_runtime_dom_context(page),
                            retry_history=compact_retry_history,
                            max_tokens=800,
                        )

                        if not replacement_steps:
                            raise RuntimeError("LLM returned no replacement_steps")

                        # Replace ONLY the failing step with replacement_steps.
                        working_steps[i : i + 1] = replacement_steps
                        step_debug["attempts"].append(attempt_ctx)

                        # Continue to next attempt by retrying same step index.
                        continue

                    # Exhausted retries: fail loud with full debug context.
                    step_debug["attempts"].append(attempt_ctx)
                    step_results.append(False)
                    debug["steps"].append(step_debug)
                    attempt_summaries = []
                    for a in step_debug.get("attempts", []):
                        attempt_summaries.append(
                            {
                                "attempt": a.get("attempt"),
                                "error": a.get("error"),
                                "visual_fallback": a.get("visual_fallback"),
                                "failure_screenshot": a.get("failure_screenshot"),
                                "trace_file": a.get("trace_file"),
                                "dom_snapshot_hash": a.get("dom_snapshot_hash"),
                            }
                        )
                    raise RuntimeError(
                        f"Unrecoverable step failure at index={i}, action={action}. "
                        f"attempts={json.dumps(attempt_summaries, ensure_ascii=False)}"
                    )

            if step_ok:
                step_results.append(True)
                step_debug["attempts"].append({"attempt": "final_success"})
                debug["steps"].append(step_debug)
                i += 1

        # Ensure at least one screenshot exists; otherwise it's invalid output.
        has_shots = any(out_dir.glob("shot*.png"))
        if not has_shots:
            raise RuntimeError("No screenshots were captured; refusing to render misleading output.")
        browser.close()

    succeeded = sum(1 for r in step_results if r)
    failed = len(step_results) - succeeded
    success = failed == 0
    return {
        "steps_succeeded": succeeded,
        "steps_failed": failed,
        "failure_reason": last_failure_reason if failed else None,
        "success": success,
        "debug": debug,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError(
                "PREVIEW_URL environment variable or command line argument required"
            )
    run_capture(preview_url=preview_url)
