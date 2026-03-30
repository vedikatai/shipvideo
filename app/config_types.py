from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CaptureSettings:
    viewport_width: int = 1280
    viewport_height: int = 720
    full_page_screenshots: bool = False
    full_page_debug_screenshots: bool = True


def load_capture_settings() -> CaptureSettings:
    """
    Load CaptureSettings from project_config.json.
    Single source of truth for capture configuration used by both the
    execution layer (step_runner) and the render layer (render.py).
    """
    from app.config import load_config                                                   

    cfg = load_config()
    capture_cfg = cfg.get("capture") or {}
    viewport_cfg = capture_cfg.get("viewport") or {}
    return CaptureSettings(
        viewport_width=int(viewport_cfg.get("width", 1280)),
        viewport_height=int(viewport_cfg.get("height", 720)),
        full_page_screenshots=bool(capture_cfg.get("full_page_screenshots", False)),
        full_page_debug_screenshots=bool(capture_cfg.get("full_page_debug_screenshots", True)),
    )
