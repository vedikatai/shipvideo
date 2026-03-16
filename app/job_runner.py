"""
Job runner: thin entrypoint that delegates to the pipeline.

Preserves backward compatibility for callers that import run_pipeline from job_runner.
All orchestration lives in app.steps.pipeline.
"""
from __future__ import annotations

from pathlib import Path

from app.steps.pipeline import run_pipeline

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

__all__ = ["run_pipeline", "APP_DIR", "REPO_ROOT"]
