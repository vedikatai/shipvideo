from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse


SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS = 12
SHIPVIDEO_AUDIT_MAX_CANDIDATES = 40
SHIPVIDEO_AUDIT_DWELL_MS = 900


@dataclass
class JourneyStep:
    index: int
    action: str
    url: str
    title: str = ""
    label: str = ""
    subtitle: str = ""
    screenshot_path: str = ""
    duration_sec: float = 2.8


@dataclass
class JourneyPlan:
    start_url: str
    steps: List[JourneyStep] = field(default_factory=list)
    end_reached: bool = False
    end_reason: str = ""


_CTA_RE = re.compile(
    r"\b(get\s*started|sign\s*up|try\s*(it|free|now)?|start\s*(free|now|trial)?|"
    r"learn\s*more|see\s*(more|demo|how)|explore|continue|next|submit|"
    r"buy\s*now|shop|pricing|features|docs|documentation|product|"
    r"watch\s*demo|request\s*demo|book\s*a?\s*demo|contact|download)\b",
    re.I,
)

_SKIP_RE = re.compile(
    r"\b(login|log\s*in|sign\s*in|cart|cookie|privacy|terms|careers|"
    r"twitter|linkedin|facebook|instagram|youtube|github\.com/login)\b",
    re.I,
)


def _same_site(base: str, href: str) -> bool:
    try:
        b = urlparse(base)
        h = urlparse(href)
        if not h.netloc:
            return True
        return h.netloc.lower().replace("www.", "") == b.netloc.lower().replace("www.", "")
    except Exception:
        return False


def _normalize_url(base: str, href: str) -> str:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    full = urljoin(base, href)
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https"):
        return ""
    return full.split("#")[0]


def score_candidate(text: str, href: str, role: str = "") -> int:
    blob = f"{text} {href} {role}".strip()
    if not blob:
        return 0
    if _SKIP_RE.search(blob):
        return -50
    score = 0
    if _CTA_RE.search(blob):
        score += 40
    if role in ("button", "link"):
        score += 5
    words = len(text.split())
    if 1 <= words <= 5:
        score += 10
    elif words <= 10:
        score += 4
    if href and not href.startswith("http"):
        score += 3
    return score


def pick_next_targets(
    candidates: List[Dict[str, Any]],
    *,
    current_url: str,
    visited: set[str],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for c in candidates[:SHIPVIDEO_AUDIT_MAX_CANDIDATES]:
        text = str(c.get("text") or "").strip()
        href = str(c.get("href") or "").strip()
        role = str(c.get("role") or "").strip()
        full = _normalize_url(current_url, href) if href else ""
        if full and (full in visited or not _same_site(current_url, full)):
            continue
        sc = score_candidate(text, href or full, role)
        if sc <= 0 and not text:
            continue
        ranked.append((sc, {**c, "resolved_url": full, "text": text}))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for sc, item in ranked:
        key = (item.get("resolved_url") or "") + "|" + (item.get("text") or "").casefold()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def narrate_step(step: JourneyStep, *, is_first: bool, is_last: bool) -> str:
    title = (step.title or "this page").strip()
    label = (step.label or "").strip()
    if is_first:
        return f"We open the starting link and land on {title}."
    if step.action == "click" and label:
        if is_last:
            return f"We click “{label}” and reach the end of the journey on {title}."
        return f"Next, we click “{label}” and arrive at {title}."
    if step.action == "goto":
        if is_last:
            return f"We navigate to {title} — end of the recorded path."
        return f"We navigate onward to {title}."
    if is_last:
        return f"Journey complete on {title}."
    return f"We capture the state of {title}."


def build_subtitles(steps: List[JourneyStep], seconds_per_frame: float = 2.8) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    t = 0.0
    n = len(steps)
    for i, step in enumerate(steps):
        dur = float(step.duration_sec or seconds_per_frame)
        text = step.subtitle or narrate_step(
            step, is_first=(i == 0), is_last=(i == n - 1)
        )
        cues.append(
            {
                "index": i + 1,
                "start": t,
                "end": t + dur,
                "text": text,
            }
        )
        t += dur
    return cues
