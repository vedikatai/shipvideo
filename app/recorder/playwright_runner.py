from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright



_CURSOR_RIPPLE_JS = r"""
(function () {
  if (document.__shipvideoOverlay) return;
  document.__shipvideoOverlay = true;

  var dot = document.createElement('div');
  dot.style.cssText = [
    'position:fixed', 'width:14px', 'height:14px', 'border-radius:50%',
    'background:rgba(220,50,50,0.9)', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%)',
    'transition:left 0.04s,top 0.04s', 'box-shadow:0 0 0 3px rgba(220,50,50,0.35)'
  ].join(';');
  document.body.appendChild(dot);

  document.addEventListener('mousemove', function (e) {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  }, true);

  var style = document.createElement('style');
  style.textContent = (
    '@keyframes sv-ripple{' +
    '0%{transform:translate(-50%,-50%) scale(1);opacity:0.8}' +
    '100%{transform:translate(-50%,-50%) scale(2.8);opacity:0}}'
  );
  document.head.appendChild(style);

  document.addEventListener('click', function (e) {
    var r = document.createElement('div');
    r.style.cssText = [
      'position:fixed', 'width:36px', 'height:36px', 'border-radius:50%',
      'border:2px solid rgba(220,50,50,0.7)', 'pointer-events:none',
      'z-index:2147483646',
      'left:' + e.clientX + 'px', 'top:' + e.clientY + 'px',
      'animation:sv-ripple 0.38s ease-out forwards'
    ].join(';');
    document.body.appendChild(r);
    setTimeout(function () { r.parentNode && r.parentNode.removeChild(r); }, 420);
  }, true);
}());
"""


def _log(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def run_script(
    *,
    script: str,
    base_url: str,
    output_dir: Path,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    video_dir = output_dir / "video_tmp"
    video_dir.mkdir(parents=True, exist_ok=True)


    ns: Dict[str, Any] = {}
    try:
        exec(compile(script, "<generated_demo>", "exec"), ns)              
    except SyntaxError as e:
        _log("script_runner.syntax_error", {"error": str(e)})
        return {"success": False, "webm_path": None, "error": f"syntax_error: {e}"}

    run_demo = ns.get("run_demo")
    if not callable(run_demo):
        return {
            "success": False,
            "webm_path": None,
            "error": "script did not define run_demo(page, context)",
        }


    video_path: Optional[str] = None
    error_str: Optional[str] = None
    success = False

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.add_init_script(_CURSOR_RIPPLE_JS)


            ns["base_url"] = base_url
            ns["output_dir"] = str(output_dir)
            ns["page"] = page
            ns["context"] = context

            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                # SPA hydration can lag networkidle/DCL; wait fonts + React handlers.
                try:
                    page.evaluate(
                        """async () => {
                            if (document.fonts && document.fonts.ready) {
                                await document.fonts.ready;
                            }
                            await new Promise((r) =>
                                requestAnimationFrame(() => requestAnimationFrame(r))
                            );
                            const root = document.getElementById('root')
                                || document.getElementById('__next')
                                || document.getElementById('app')
                                || document.body;
                            if (!root) return;
                            const deadline = Date.now() + 4000;
                            while (Date.now() < deadline) {
                                const interactive = root.querySelector(
                                    'button, a[href], input, [role="button"], [data-testid]'
                                );
                                if (interactive && (root.textContent || '').trim()) {
                                    return;
                                }
                                await new Promise((r) => setTimeout(r, 50));
                            }
                        }"""
                    )
                except Exception:
                    time.sleep(0.25)
                _log("script_runner.started", {"base_url": base_url})


                run_demo(page, context)

                success = True
                _log("script_runner.completed", {"success": True})
            except Exception as e:
                error_str = f"{type(e).__name__}: {e}"
                _log("script_runner.execution_error", {"error": error_str})
            finally:

                try:
                    video_path = page.video.path()
                except Exception:
                    pass
                context.close()
                browser.close()

    except Exception as e:
        error_str = f"playwright_setup_error: {type(e).__name__}: {e}"
        _log("script_runner.setup_error", {"error": error_str})
        traceback.print_exc()
        return {"success": False, "webm_path": None, "error": error_str}


    if video_path and Path(video_path).exists():
        _log("script_runner.video_ready", {"path": video_path, "success": success})
        if success:
            return {"success": True, "webm_path": video_path, "error": None}

        return {"success": False, "webm_path": video_path, "error": error_str}


    webm_files = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_size, reverse=True)
    if webm_files:
        video_path = str(webm_files[0])
        _log("script_runner.video_found_in_dir", {"path": video_path})
        if success:
            return {"success": True, "webm_path": video_path, "error": None}
        return {"success": False, "webm_path": video_path, "error": error_str}

    return {
        "success": False,
        "webm_path": None,
        "error": error_str or "no_video_produced",
    }
