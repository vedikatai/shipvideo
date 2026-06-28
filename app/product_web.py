"""ShipVideo website: paste a link → journey video with subtitles.

Open-source stack: FastAPI, Playwright, FFmpeg, vanilla HTML/CSS/JS.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.product.jobs import create_job, get_job, list_jobs
from observability import init_tracing

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"
DATA_JOBS = REPO_ROOT / "data" / "jobs"

app = FastAPI(title="ShipVideo", description="Link → journey video with subtitles")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_tracing()
    except Exception:
        pass
    DATA_JOBS.mkdir(parents=True, exist_ok=True)


class CreateJobBody(BaseModel):
    url: str = Field(..., min_length=3, max_length=2000)
    max_steps: int = Field(default=10, ge=3, le=20)
    use_azure_subtitles: bool = Field(default=True)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    index = TEMPLATES_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>ShipVideo</h1><p>UI missing. Check web/templates/index.html</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "shipvideo-product"}


@app.post("/api/jobs")
def api_create_job(body: CreateJobBody) -> JSONResponse:
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "url required")
    job = create_job(
        url,
        max_steps=body.max_steps,
        use_azure_subtitles=body.use_azure_subtitles,
    )
    return JSONResponse(job)


@app.get("/api/jobs")
def api_list_jobs() -> JSONResponse:
    return JSONResponse({"jobs": list_jobs(30)})


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> JSONResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/video")
def api_job_video(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    path = DATA_JOBS / job_id / f"journey_{job_id}.mp4"
    if not path.exists():
        result = (job.get("result") or {})
        alt = result.get("video_path")
        if alt and Path(alt).exists():
            path = Path(alt)
        else:
            # legacy fallback
            legacy = DATA_JOBS / job_id / "journey.mp4"
            if legacy.exists():
                path = legacy
            else:
                raise HTTPException(404, "video not ready")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"journey_{job_id}.mp4",
    )


@app.get("/api/jobs/{job_id}/srt")
def api_job_srt(job_id: str) -> FileResponse:
    path = DATA_JOBS / job_id / f"journey_{job_id}.srt"
    if not path.exists():
        legacy = DATA_JOBS / job_id / "journey.srt"
        if legacy.exists():
            path = legacy
        else:
            raise HTTPException(404, "srt not ready")
    return FileResponse(path, media_type="application/x-subrip", filename=f"journey_{job_id}.srt")


@app.get("/api/jobs/{job_id}/frame/{name}")
def api_job_frame(job_id: str, name: str) -> FileResponse:
    if ".." in name or "/" in name:
        raise HTTPException(400, "invalid frame name")
    path = DATA_JOBS / job_id / "frames" / name
    if not path.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(path, media_type="image/png")


# Also expose legacy webhook app routes optionally via mount is not done here;
# run product site with: uvicorn app.product_web:app --reload --port 8080
