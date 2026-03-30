"""
Script generator: uses the LLM to produce a complete, executable Playwright
`run_demo(page, context)` function from diff + DOM context + suggested_demo_flow.

The generated function is intentionally narrow in contract:
  - Receives a Playwright Page (already loaded at base_url) and BrowserContext.
  - `base_url` is available as a module-level variable in its execution scope.
  - Must NOT call sync_playwright(), launch browsers, or close the browser/context.
  - Uses Playwright semantic locators (get_by_role, get_by_text, get_by_test_id).

Retry: on execution failure, the previous script + error are sent back for repair.
Max 2 retries before propagating the error to the fallback pipeline.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI, BadRequestError                
except Exception:
    OpenAI = None                
    BadRequestError = Exception                

MAX_SCRIPT_RETRIES = 2

_SCRIPT_JSON_SCHEMA: Dict[str, Any] = {
    "name": "playwright_script",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": (
                    "Complete Python source of a function `def run_demo(page, context):`.\n"
                    "The function receives a Playwright Page (already at base_url) and BrowserContext.\n"
                    "`base_url` is available as a module-level variable in scope.\n"
                    "Use semantic locators only: page.get_by_role(), page.get_by_text(), "
                    "page.get_by_test_id(), page.locator('[data-testid=...]').\n"
                    "Do NOT call sync_playwright(), browser.launch(), context.new_page(), or close anything.\n"
                    "Use page.wait_for_selector() / page.wait_for_url() for deterministic waits.\n"
                    "Include page.screenshot() calls at key moments to capture state.\n"
                    "Keep the function self-contained with no external imports beyond stdlib."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "1–2 sentences explaining the script strategy.",
            },
        },
        "required": ["script", "reasoning"],
        "additionalProperties": False,
    },
}


def _get_client() -> Any:
    if OpenAI is None:
        raise RuntimeError("openai package not installed")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set")
    base_url = endpoint.rstrip("/")
    if not base_url.endswith("openai/v1"):
        base_url = base_url + "/openai/v1/"
    return OpenAI(base_url=base_url, api_key=key)


def _call_llm(client: Any, deployment: str, messages: List[Dict[str, str]]) -> str:
    """Call LLM with json_schema, fall back to json_object if unsupported."""
    try:
        completion = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_completion_tokens=1500,
            response_format={"type": "json_schema", "json_schema": _SCRIPT_JSON_SCHEMA},
        )
        data = json.loads(completion.choices[0].message.content or "{}")
        return data.get("script") or ""
    except (BadRequestError, Exception) as e:
        err_str = str(e).lower()
        is_format_err = any(
            kw in err_str
            for kw in ("json_schema", "response_format", "unsupported", "invalid_request_error")
        )
        if not is_format_err:
            raise


    completion = client.chat.completions.create(
        model=deployment,
        messages=messages,
        max_completion_tokens=1500,
        response_format={"type": "json_object"},
    )
    content = (completion.choices[0].message.content or "{}").strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    data = json.loads(content)
    return data.get("script") or ""


def _build_action_menu(dom_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a concrete 'what you can click' menu from live DOM data.
    This is injected directly into the prompt so the LLM never has to guess.
    """
    clickable = []
    for b in (dom_data.get("buttons") or [])[:20]:
        entry: Dict[str, str] = {}
        testid = (b.get("testid") or "").strip()
        aria = (b.get("aria") or "").strip()
        text = (b.get("text") or "").strip()
        selector_val = (b.get("selector") or "").strip()

        if testid:
            entry["use"] = f"page.get_by_test_id('{testid}')"
        elif aria:
            entry["use"] = f"page.locator(\"[aria-label='{aria}']\")"
        elif text:
            entry["use"] = f"page.get_by_text('{text}', exact=True)"
        elif selector_val:
            entry["use"] = f"page.locator('{selector_val}')"
        else:
            continue

        if text:
            entry["visible_text"] = text
        clickable.append(entry)

    navigable = [
        {"route": (l.get("href") or "").strip(), "text": (l.get("text") or "").strip()}
        for l in (dom_data.get("links") or [])[:15]
        if (l.get("href") or "").startswith("/")
    ]
    routes = list({r for r in (dom_data.get("routes") or ["/"])})

    return {"clickable_elements": clickable, "navigable_links": navigable, "routes": routes}


def _build_messages(
    *,
    suggested_demo_flow: str,
    dom_data: Dict[str, Any],
    base_url: str,
    app_hints: str,
    diff_summary: str,
    previous_script: Optional[str],
    previous_error: Optional[str],
) -> List[Dict[str, str]]:
    action_menu = _build_action_menu(dom_data)

    system_msg = (
        "You are a Playwright script generator for automated UI demos.\n\n"
        "Generate a Python function `def run_demo(page, context):` that:\n"
        "• Receives an already-initialized Playwright Page (loaded at base_url) and BrowserContext.\n"
        "• `base_url` and `output_dir` are available as module-level variables.\n"
        "• Takes screenshots: page.screenshot(path=f'{output_dir}/step_1.png') at key moments.\n"
        "• Waits deterministically: page.wait_for_selector(state='visible', timeout=8000).\n"
        "• Does NOT call sync_playwright(), browser.launch(), context.new_page(), or close anything.\n"
        "• No external imports (stdlib only, e.g. `import time`).\n\n"
        "── STRICT ELEMENT TARGETING RULES ──\n"
        "The payload includes `action_menu.clickable_elements` — a ready-made list of\n"
        "EXACTLY how to target each interactive element. USE ONLY these expressions.\n"
        "Do NOT invent locators. Do NOT use get_by_role() with names not in the list.\n"
        "Do NOT use get_by_text() with text not in visible_text fields.\n"
        "If no element in the list matches what you need, use a screenshot step instead.\n\n"
        "After any click that might open a modal or navigate:\n"
        "  page.wait_for_load_state('domcontentloaded')\n"
        "  # Then use only elements from the CURRENT page state\n"
    )
    if app_hints:
        system_msg += f"\nApp-specific hints:\n{app_hints}\n"
    if previous_script:
        system_msg += (
            "\n── RETRY MODE ──\n"
            "The previous script failed. Fix ONLY the broken part:\n"
            "• Check which locator caused the TimeoutError.\n"
            "• Replace it with a different entry from action_menu.clickable_elements.\n"
            "• If no matching entry exists, remove that interaction and take a screenshot instead.\n"
            "• Keep the overall narrative intact.\n"
        )

    payload: Dict[str, Any] = {
        "base_url": base_url,
        "suggested_demo_flow": suggested_demo_flow,
        "diff_summary": diff_summary,
        "action_menu": action_menu,
    }
    if previous_script:
        payload["previous_script"] = previous_script
        payload["previous_error"] = previous_error or "unknown error"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_playwright_script(
    *,
    suggested_demo_flow: str,
    dom_data: Dict[str, Any],
    base_url: str,
    app_hints: str = "",
    diff_files: Optional[List[Dict[str, str]]] = None,
    previous_script: Optional[str] = None,
    previous_error: Optional[str] = None,
) -> str:
    """
    Generate a complete `def run_demo(page, context):` Playwright function.

    On retry (previous_script + previous_error provided) the LLM receives the
    broken script and error context and outputs a repaired version.

    Returns the raw Python source string.
    Raises RuntimeError if generation fails after retries.
    """
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT must be set")

    client = _get_client()


    diff_summary = ""
    if diff_files:
        diff_summary = "; ".join(
            f"{f.get('path', '?')} ({f.get('status', '?')})"
            for f in diff_files[:15]
        )

    messages = _build_messages(
        suggested_demo_flow=suggested_demo_flow,
        dom_data=dom_data,
        base_url=base_url,
        app_hints=app_hints,
        diff_summary=diff_summary,
        previous_script=previous_script,
        previous_error=previous_error,
    )

    script = _call_llm(client, deployment, messages)
    if not script or "def run_demo" not in script:
        raise ValueError(
            f"LLM did not produce a valid run_demo function. "
            f"Got {len(script)} chars: {script[:200]!r}"
        )

    print(
        f"[script_generator] script_chars={len(script)} "
        f"retry={'yes' if previous_script else 'no'}",
        flush=True,
    )
    return script
