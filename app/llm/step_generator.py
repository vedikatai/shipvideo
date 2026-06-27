from __future__ import annotations

import json
import os
import asyncio
from typing import Any, Dict, List, Tuple, Optional

from app.llm_guards import record_spend

try:
    from openai import OpenAI, BadRequestError                
except Exception:
    OpenAI = None                
    BadRequestError = Exception                


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        data = {
            key: _json_safe(val)
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
        data["_type"] = value.__class__.__name__
        return data
    return str(value)


def _get_client() -> Any:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set")
    base_url = endpoint.rstrip("/")
    if not base_url.endswith("openai/v1"):
        base_url = base_url + "/openai/v1/"
    return OpenAI(base_url=base_url, api_key=key)


def _call_with_fallback(
    client: Any,
    deployment: str,
    messages: List[Dict[str, str]],
    max_completion_tokens: int,
    schema: Dict[str, Any],
) -> Tuple[Any, Dict[str, Any]]:
    try:
        completion = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = completion.choices[0].message.content or "{}"
        print("[llm.step_generator] response_mode=json_schema", flush=True)
        return completion, json.loads(content)
    except BadRequestError as e:
        print(
            f"[llm.step_generator] json_schema unsupported ({type(e).__name__}); retrying json_object",
            flush=True,
        )
    except Exception as e:
        err_str = str(e).lower()
        is_format_error = any(
            kw in err_str
            for kw in ("json_schema", "response_format", "unsupported", "invalid_request_error")
        )
        if not is_format_error:
            raise
        print(
            f"[llm.step_generator] json_schema mode failed ({type(e).__name__}); retrying json_object",
            flush=True,
        )

    completion = client.chat.completions.create(
        model=deployment,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        response_format={"type": "json_object"},
    )
    content = (completion.choices[0].message.content or "{}").strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    print("[llm.step_generator] response_mode=json_object (fallback)", flush=True)
    return completion, json.loads(content)


def generate_next_steps(
    *,
    objective: Dict[str, Any],
    dom_context: Dict[str, Any],
    previous_error: Dict[str, Any] | None = None,
    max_steps: int = 2,
) -> List[Dict[str, Any]]:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT must be set")

    schema = {
        "name": "next_steps",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": max_steps,
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["goto", "click", "screenshot"]},
                            "selector": {"type": "string"},
                            "text": {"type": "string"},
                            "url": {"type": "string"},
                            "label": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["action", "selector", "text", "url", "label", "reasoning"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
    }



    available_buttons = [
        {"text": (b.get("text") or "").strip(), "testid": (b.get("testid") or "").strip(), "aria": (b.get("aria") or "").strip(), "id": (b.get("id") or "").strip()}
        for b in (dom_context.get("buttons") or [])
        if (b.get("text") or b.get("testid") or b.get("aria") or "").strip()
    ][:12]
    available_links = [
        {"text": (l.get("text") or "").strip(), "href": (l.get("href") or "").strip()}
        for l in (dom_context.get("links") or [])
        if (l.get("text") or "").strip()
    ][:8]
    data_testids = [
        (t.get("testid") if isinstance(t, dict) else t) or ""
        for t in (dom_context.get("data_testids") or [])
    ]
    data_testids = [str(t).strip() for t in data_testids if str(t).strip()][:20]
    routes = list(dom_context.get("routes") or ["/"])[:12]
    # Keep objective lean — full generation_context blows prompt tokens.
    objective_lean = {
        "goal": (objective or {}).get("goal") or (objective or {}).get("title") or "",
        "target_testid": (objective or {}).get("target_testid") or "",
        "start_route": (objective or {}).get("start_route") or "",
    }
    prev_err = previous_error or {}
    previous_error_lean = {
        k: prev_err.get(k)
        for k in ("error", "outcome", "reason", "message", "failed_action", "intent")
        if prev_err.get(k)
    }

    system_msg = (
        "Generate ONLY the next UI step(s) from CURRENT DOM evidence.\n"
        "Targeting priority: data-testid selector → aria-label selector → exact visible label.\n"
        "Never invent routes, labels, or CSS selectors not present in the payload.\n"
        "Set unused fields to empty string.\n"
    )

    payload = {
        "objective": objective_lean,
        "current_path": dom_context.get("current_path", "/"),
        "routes": routes,
        "available_buttons": available_buttons,
        "available_links": available_links,
        "data_testids": data_testids,
        "previous_error": previous_error_lean,
    }
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": json.dumps(_json_safe(payload), ensure_ascii=False)},
    ]
    client = _get_client()
    completion, data = _call_with_fallback(client, deployment, messages, 700, schema)

    usage = getattr(completion, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    record_spend(pt, ct)

    steps = data.get("steps") or []
    if not steps:
        raise RuntimeError("generate_next_steps: LLM returned empty steps via both response_format modes")
    return steps


def generate_single_step_toward_testid(
    *,
    target_testid: str,
    snapshot: Dict[str, Any],
    objective: Dict[str, Any],
    previous_error: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT must be set")

    schema = {
        "name": "single_step_toward_testid",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "step": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["goto", "click"]},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                        "url": {"type": "string"},
                        "label": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["action", "selector", "text", "url", "label", "reasoning"],
                    "additionalProperties": False,
                }
            },
            "required": ["step"],
            "additionalProperties": False,
        },
    }

    system_msg = (
        "Navigate toward one target testid. Return exactly one action.\n"
        "Prefer data-testid / aria-label selectors; else exact visible label.\n"
        "Never invent routes, labels, or selectors not in the payload."
    )
    interactive = snapshot.get("interactive_elements") or []
    compact_elements = [
        {
            "ref": str(e.get("ref") or ""),
            "role": str(e.get("role") or ""),
            "name": str(e.get("name") or "")[:80],
            "testid": str(e.get("testid") or ""),
            "aria_label": str(e.get("aria_label") or "")[:80],
        }
        for e in interactive[:40]
        if str(e.get("ref") or "").strip()
    ]
    objective_lean = {
        "goal": (objective or {}).get("goal") or (objective or {}).get("title") or "",
        "start_route": (objective or {}).get("start_route") or "",
    }
    prev_err = previous_error or {}
    previous_error_lean = {
        k: prev_err.get(k)
        for k in ("error", "outcome", "reason", "message", "failed_action", "intent")
        if prev_err.get(k)
    }
    payload = {
        "objective": objective_lean,
        "target_testid": target_testid,
        "current_url": snapshot.get("current_url", ""),
        "current_path": snapshot.get("current_path", "/"),
        "headings": (snapshot.get("headings") or [])[:8],
        "active_surfaces": (snapshot.get("active_surfaces") or [])[:8],
        "interactive_elements": compact_elements,
        "previous_error": previous_error_lean,
    }
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": json.dumps(_json_safe(payload), ensure_ascii=False)},
    ]
    client = _get_client()
    completion, data = _call_with_fallback(client, deployment, messages, 400, schema)

    usage = getattr(completion, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    record_spend(pt, ct)

    step = data.get("step") or {}
    if not step:
        raise RuntimeError("generate_single_step_toward_testid: empty step")
    return step


def _call_llm_simple(prompt: str) -> str:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        return ""
    try:
        client = _get_client()
        completion = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=120,
            response_format={"type": "text"},
        )
        usage = getattr(completion, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        record_spend(pt, ct)
        return str(completion.choices[0].message.content or "")
    except Exception:
        return ""


async def find_ref_with_llm(
    *,
    intent: str,
    interactive_elements: List[Dict[str, Any]],
    context_elements: List[Dict[str, Any]] | None = None,
) -> Optional[str]:
    if not intent or not interactive_elements:
        return None

    compact_interactive = [
        {
            "ref": str(e.get("ref") or ""),
            "role": str(e.get("role") or ""),
            "name": str(e.get("name") or "")[:80],
        }
        for e in interactive_elements[:50]
        if str(e.get("ref") or "").strip()
    ]
    if not compact_interactive:
        return None

    elements_text = "\n".join(
        [
            f"ref={el.get('ref')} role={el.get('role')} name={el.get('name')}"
            for el in compact_interactive
        ]
    )
    prompt = (
        f'Pick best click target for intent "{intent}".\n'
        f"Elements:\n{elements_text}\n"
        'Reply with only the ref (e.g. e10) or none.'
    )
    response = _call_llm_simple(prompt)
    ref = (response or "").strip().strip('"').strip("'")
    if not ref or ref.lower() == "none":
        return None
    if not ref.startswith("@"):
        ref = f"@{ref}"
    valid_refs = {str(e.get("ref") or "").strip() for e in compact_interactive}
    if ref in valid_refs:
        return ref
    return None


def find_ref_with_llm_sync(
    *,
    intent: str,
    interactive_elements: List[Dict[str, Any]],
    context_elements: List[Dict[str, Any]] | None = None,
) -> str:
    try:
        return asyncio.run(
            find_ref_with_llm(
                intent=intent,
                interactive_elements=interactive_elements,
                context_elements=context_elements,
            )
        ) or ""
    except RuntimeError:

        return ""
