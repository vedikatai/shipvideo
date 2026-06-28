from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.product.pipeline import run_link_to_video, video_path_for_job


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data" / "jobs"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


def _job_dir(job_id: str) -> Path:
    d = DATA_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist(job: Dict[str, Any]) -> None:
    path = _job_dir(job["id"]) / "job.json"
    path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    path = DATA_DIR / job_id / "job.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_jobs(limit: int = 20) -> list[Dict[str, Any]]:
    items = []
    for p in sorted(DATA_DIR.glob("*/job.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def create_job(url: str, *, max_steps: int = 10, use_azure_subtitles: bool = True) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "url": url.strip(),
        "status": "queued",
        "stage": "queued",
        "max_steps": max_steps,
        "use_azure_subtitles": use_azure_subtitles,
        "created_at": time.time(),
        "updated_at": time.time(),
        "error": None,
        "result": None,
        "log": [],
        "headless": True,
    }
    with _lock:
        _jobs[job_id] = job
    _persist(job)

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return dict(job)


def _update(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()
        snap = dict(job)
    _persist(snap)


def _append_log(job_id: str, stage: str, extra: Dict[str, Any]) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.setdefault("log", []).append({"stage": stage, **extra, "ts": time.time()})
        job["stage"] = stage
        job["updated_at"] = time.time()
        snap = dict(job)
    _persist(snap)


def _run_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    _update(job_id, status="running", stage="starting", headless=True)

    def on_progress(stage: str, extra: Dict[str, Any]) -> None:
        _append_log(job_id, stage, extra)

    try:
        result = run_link_to_video(
            job["url"],
            _job_dir(job_id),
            job_id=job_id,
            max_steps=int(job.get("max_steps") or 10),
            use_azure_subtitles=bool(job.get("use_azure_subtitles", True)),
            on_progress=on_progress,
        )
        if not result.get("ok"):
            _update(
                job_id,
                status="failed",
                stage="failed",
                error=result.get("error") or "unknown",
                result=result,
            )
            return
        result["video_url"] = f"/api/jobs/{job_id}/video"
        result["srt_url"] = f"/api/jobs/{job_id}/srt"
        result["video_path"] = str(video_path_for_job(_job_dir(job_id), job_id))
        _update(job_id, status="done", stage="done", result=result, error=None, headless=True)
    except Exception as e:
        _update(
            job_id,
            status="failed",
            stage="failed",
            error=f"{type(e).__name__}: {e}",
        )
