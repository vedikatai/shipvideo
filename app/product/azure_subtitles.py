"""Generate subtitle lines from page DOM via Azure OpenAI (same endpoint as step_generation)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

from app.config import load_config

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


def azure_openai_client_from_env() -> Any:
    """Mirror app/steps/step_generation.py Azure wiring; project_config is loaded for context only."""
    if OpenAI is None:
        raise RuntimeError("openai package not installed")
    # Touch project config so config.py is part of the path (viewport/hints available later).
    try:
        load_config()
    except Exception:
        pass
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not (endpoint and key and deployment):
        raise RuntimeError(
            "Azure OpenAI is not configured "
            "(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT)"
        )
    base_url = endpoint.rstrip("/")
    if not base_url.endswith("openai/v1"):
        base_url = base_url + "/openai/v1/"
    client = OpenAI(base_url=base_url, api_key=key)
    return client, deployment


def generate_subtitles_from_dom(
    *,
    url: str,
    dom_text: str,
    step_summaries: Optional[Sequence[Dict[str, Any]]] = None,
    n_lines: int = 6,
) -> Dict[str, Any]:
    """
    Call Azure OpenAI to turn page DOM text + optional step context into
    short subtitle lines for the demo video.
    """
    client, deployment = azure_openai_client_from_env()
    n_lines = max(1, min(int(n_lines), 20))
    dom_trim = (dom_text or "").strip()
    if len(dom_trim) > 12000:
        dom_trim = dom_trim[:12000] + "\n…(truncated)"

    steps_blob = ""
    if step_summaries:
        steps_blob = json.dumps(list(step_summaries)[:20], ensure_ascii=False)

    system = (
        "You write concise spoken subtitle lines for a product demo video. "
        "Given page DOM text and optional navigation steps, return ONLY valid JSON: "
        '{"lines": ["...", "..."]}. '
        f"Produce exactly {n_lines} lines. Each line is one short spoken sentence "
        "(max ~18 words), present tense, accurate to the page content. "
        "No markdown, no numbering prefixes."
    )
    user = json.dumps(
        {
            "url": url,
            "dom_text": dom_trim,
            "navigation_steps": steps_blob or None,
            "line_count": n_lines,
        },
        ensure_ascii=False,
    )

    completion = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=500,
        response_format={"type": "json_object"},
    )
    raw = (completion.choices[0].message.content or "{}").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    lines_raw = data.get("lines") or data.get("subtitles") or []
    lines: List[str] = []
    for item in lines_raw:
        text = str(item or "").strip()
        text = re.sub(r"^\d+[\).\:\-]\s*", "", text)
        if text:
            lines.append(text[:220])
    # pad / trim to n_lines
    while len(lines) < n_lines:
        lines.append(lines[-1] if lines else f"Demo of {url}")
    lines = lines[:n_lines]

    usage = getattr(completion, "usage", None)
    return {
        "lines": lines,
        "source": "azure_openai_dom",
        "deployment": deployment,
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
    }
