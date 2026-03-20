from __future__ import annotations

import json
import os
from typing import Any, Dict, List


def _get_client() -> Any:
    from openai import OpenAI  # type: ignore

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set")
    base_url = endpoint.rstrip("/")
    if not base_url.endswith("openai/v1"):
        base_url = base_url + "/openai/v1/"
    return OpenAI(base_url=base_url, api_key=key)


def generate_next_steps(
    *,
    objective: Dict[str, Any],
    dom_context: Dict[str, Any],
    previous_error: Dict[str, Any] | None = None,
    max_steps: int = 2,
) -> List[Dict[str, Any]]:
    """
    Generate only NEXT step(s) from current DOM (re-anchored model).
    Strict contract:
      {"steps":[{"action":"click|goto|screenshot","selector":"","text":"","url":"","label":"","reasoning":""}]}
    """
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

    # Build a compact, human-readable action menu from the live DOM so the model
    # has a concrete list to pick from rather than guessing.
    available_buttons = [
        {"text": (b.get("text") or "").strip(), "testid": (b.get("testid") or "").strip(), "aria": (b.get("aria") or "").strip(), "id": (b.get("id") or "").strip()}
        for b in (dom_context.get("buttons") or [])
        if (b.get("text") or b.get("testid") or b.get("aria") or "").strip()
    ][:15]
    available_links = [
        {"text": (l.get("text") or "").strip(), "href": (l.get("href") or "").strip()}
        for l in (dom_context.get("links") or [])
        if (l.get("text") or "").strip()
    ][:10]

    system_msg = (
        "You generate ONLY the immediate next UI automation step(s) from the CURRENT DOM.\n"
        "Do NOT assume any selector from a previous step is still valid after navigation.\n\n"
        "── HOW TO TARGET ELEMENTS (follow this decision tree exactly) ──\n"
        "1. If the button/element has a data-testid listed in dom_context.data_testids:\n"
        "   → set selector=\"[data-testid='the-testid']\"  AND  text=\"\"\n"
        "2. If the button/element has an aria-label listed in dom_context.buttons[].aria:\n"
        "   → set selector=\"[aria-label='the-aria']\"  AND  text=\"\"\n"
        "3. For EVERYTHING ELSE (including role-based targeting):\n"
        "   → set selector=\"\"  AND  text=\"exact visible button/link text from dom_context\"\n"
        "   The text MUST appear verbatim in dom_context.available_buttons[].text or available_links[].text.\n\n"
        "── STRICTLY FORBIDDEN ──\n"
        "• NEVER put role descriptions in selector (e.g. 'role=button name=X' is WRONG).\n"
        "• NEVER put compound CSS in selector (e.g. 'button[role=\"button\"]' is WRONG).\n"
        "• NEVER invent button text not present in dom_context.available_buttons or available_links.\n"
        "• NEVER use a raw #id or .class selector unless that exact id/class is listed in dom_context.\n\n"
        "── GOTO RULES ──\n"
        "• url must be in dom_context.routes. Never invent routes.\n"
    )

    payload = {
        "objective": objective,
        "current_path": dom_context.get("current_path", "/"),
        "routes": dom_context.get("routes", ["/"]),
        "available_buttons": available_buttons,
        "available_links": available_links,
        "data_testids": dom_context.get("data_testids", []),
        "previous_error": previous_error or {},
    }
    client = _get_client()
    completion = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        max_tokens=700,
        response_format={"type": "json_schema", "json_schema": schema},
    )
    data = json.loads(completion.choices[0].message.content or "{}")
    return data.get("steps") or []

