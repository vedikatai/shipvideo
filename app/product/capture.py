from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.product.journey import (
    SHIPVIDEO_AUDIT_DWELL_MS,
    SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS,
    JourneyPlan,
    JourneyStep,
    build_subtitles,
    narrate_step,
    pick_next_targets,
)


_COLLECT_CANDIDATES_JS = """
() => {
  const out = [];
  const push = (el, role) => {
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.value || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 120);
    const href = el.href || el.getAttribute('href') || '';
    if (!text && !href) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    out.push({ text, href, role, tag: (el.tagName || '').toLowerCase() });
  };
  document.querySelectorAll('a[href]').forEach(el => push(el, 'link'));
  document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]')
    .forEach(el => push(el, 'button'));
  return out.slice(0, 60);
}
"""


async def _safe_title(page) -> str:
    try:
        return (await page.title() or "").strip()[:160]
    except Exception:
        return ""


async def _safe_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


async def _collect_candidates(page) -> List[Dict[str, Any]]:
    try:
        return await page.evaluate(_COLLECT_CANDIDATES_JS)
    except Exception:
        return []


async def _click_by_text(page, text: str) -> bool:
    if not text:
        return False
    # Prefer exact-ish text clicks via Playwright locators
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=text, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=4000)
                return True
        except Exception:
            pass
    try:
        loc = page.get_by_text(text, exact=False)
        if await loc.count() > 0:
            await loc.first.click(timeout=4000)
            return True
    except Exception:
        pass
    return False


async def capture_journey(
    start_url: str,
    work_dir: Path,
    *,
    max_steps: int = SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS,
    viewport: tuple[int, int] = (1280, 720),
    headless: bool = True,
) -> JourneyPlan:
    from playwright.async_api import async_playwright

    # Session constraint: always headless — headed mode is not allowed.
    headless = True

    work_dir = Path(work_dir)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    url = start_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    plan = JourneyPlan(start_url=url)
    visited: set[str] = set()
    plan.headless = True  # type: ignore[attr-defined]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        print("[product.capture] playwright headless=True", flush=True)
        context = await browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(SHIPVIDEO_AUDIT_DWELL_MS)
        except Exception as e:
            plan.end_reason = f"failed_to_open: {type(e).__name__}: {e}"
            await browser.close()
            return plan

        for step_i in range(max_steps):
            current = await _safe_url(page)
            title = await _safe_title(page)
            visited.add(current.split("#")[0])

            shot_path = frames_dir / f"step_{step_i:03d}.png"
            try:
                await page.screenshot(path=str(shot_path), full_page=False)
            except Exception:
                # still record a placeholder-less step without image
                pass

            label = ""
            action = "goto" if step_i == 0 else "capture"
            # label filled after we decide click for *next* iteration;
            # for current frame we use previous click label stored on plan if any
            if plan.steps:
                label = plan.steps[-1].label if False else label

            # For steps after first, the label is what we clicked to get here
            if step_i > 0 and plan.steps:
                # previous step's intended next click is stored as pending
                pass

            pending_label = getattr(plan, "_pending_label", "")
            if pending_label:
                label = pending_label
                action = "click"
                plan._pending_label = ""  # type: ignore[attr-defined]

            step = JourneyStep(
                index=step_i,
                action=action if step_i > 0 else "goto",
                url=current,
                title=title or urlparse(current).path or current,
                label=label,
                screenshot_path=str(shot_path) if shot_path.exists() else "",
            )
            plan.steps.append(step)

            if step_i >= max_steps - 1:
                plan.end_reached = True
                plan.end_reason = "max_steps"
                break

            candidates = await _collect_candidates(page)
            targets = pick_next_targets(
                candidates, current_url=current, visited=visited, limit=5
            )
            advanced = False
            for target in targets:
                text = str(target.get("text") or "").strip()
                resolved = str(target.get("resolved_url") or "").strip()
                clicked = False
                if text:
                    clicked = await _click_by_text(page, text)
                if not clicked and resolved:
                    try:
                        await page.goto(
                            resolved, wait_until="domcontentloaded", timeout=30000
                        )
                        clicked = True
                        text = text or resolved
                    except Exception:
                        clicked = False
                if clicked:
                    plan._pending_label = text  # type: ignore[attr-defined]
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(SHIPVIDEO_AUDIT_DWELL_MS)
                    advanced = True
                    break

            if not advanced:
                plan.end_reached = True
                plan.end_reason = "no_more_targets"
                break

        # annotate subtitles
        n = len(plan.steps)
        for i, step in enumerate(plan.steps):
            step.subtitle = narrate_step(
                step, is_first=(i == 0), is_last=(i == n - 1)
            )

        await context.close()
        await browser.close()

    if plan.steps and not plan.end_reason:
        plan.end_reached = True
        plan.end_reason = "completed"
    return plan


def capture_journey_sync(
    start_url: str,
    work_dir: Path,
    **kwargs: Any,
) -> JourneyPlan:
    return asyncio.run(capture_journey(start_url, work_dir, **kwargs))
