from __future__ import annotations

from pathlib import Path

from app.steps.pipeline import run_pipeline

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

__all__ = ["run_pipeline", "APP_DIR", "REPO_ROOT"]
