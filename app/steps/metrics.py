from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RunMetrics:
    run_id: str
    pr_number: int
    started_at: str
    finished_at: str = ""
    success: bool = False
    error_type: str = ""
    error_message: str = ""
    pipeline: str = ""
    capture_browser: str = ""
    preflight_passed: bool = False
    terminal_condition_reached: bool = False
    steps_validated: int = 0
    steps_unvalidated: int = 0
    wrong_clicks: int = 0
    video_usable: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


def new_run_metrics(pr_number: int) -> RunMetrics:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return RunMetrics(
        run_id=f"pr{pr_number}_{ts}",
        pr_number=pr_number,
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def write_run_metrics(metrics: RunMetrics) -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data" / "run_metrics"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{metrics.run_id}.json"
    path.write_text(
        json.dumps(asdict(metrics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path

