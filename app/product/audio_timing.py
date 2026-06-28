from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SHIPVIDEO_AUDIT_SILENCE_NOISE_DB = -40
SHIPVIDEO_AUDIT_SILENCE_MIN_DURATION = 0.08
SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS = 60.0
SHIPVIDEO_AUDIT_GAP_BETWEEN_CLIPS = 0.35


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def media_has_audio(path: Path) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    out = (proc.stdout or "").strip().lower()
    return "audio" in out


def audio_duration_seconds(path: Path) -> float:
    path = Path(path)
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float((proc.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def synthesize_tts_wav(text: str, wav_path: Path) -> Dict[str, Any]:
    """TTS fallback: piper, espeak-ng, espeak, or macOS say → wav."""
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip() or "Step."
    engines_tried: List[str] = []

    piper = _which("piper")
    if piper:
        engines_tried.append("piper")
        # piper needs a model; if missing, fall through
        model = Path.home() / ".local/share/piper/en_US-lessac-medium.onnx"
        if model.exists():
            proc = subprocess.run(
                [piper, "--model", str(model), "--output_file", str(wav_path)],
                input=text,
                text=True,
                capture_output=True,
                timeout=120,
            )
            if proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 44:
                return {"engine": "piper", "wav": str(wav_path), "engines_tried": engines_tried}

    for eng in ("espeak-ng", "espeak"):
        bin_path = _which(eng)
        if not bin_path:
            continue
        engines_tried.append(eng)
        proc = subprocess.run(
            [bin_path, "-w", str(wav_path), text],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 44:
            return {"engine": eng, "wav": str(wav_path), "engines_tried": engines_tried}

    say = _which("say")
    if say:
        engines_tried.append("say")
        aiff = wav_path.with_suffix(".aiff")
        proc = subprocess.run(
            [say, "-o", str(aiff), text],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and aiff.exists():
            conv = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), str(wav_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            aiff.unlink(missing_ok=True)
            if conv.returncode == 0 and wav_path.exists():
                return {"engine": "say", "wav": str(wav_path), "engines_tried": engines_tried}

    raise RuntimeError(
        "No TTS engine available (tried piper, espeak-ng, espeak, say). "
        f"engines_tried={engines_tried}"
    )


def build_narration_audio(
    texts: Sequence[str],
    work_dir: Path,
    *,
    stem: str = "narration",
    max_total_seconds: float = SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
    gap_seconds: float = SHIPVIDEO_AUDIT_GAP_BETWEEN_CLIPS,
) -> Dict[str, Any]:
    """Synthesize one WAV per line, concat with short gaps, return combined wav + per-line spans."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "tts_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: List[Path] = []
    engines: List[str] = []
    for i, text in enumerate(texts):
        clip = clips_dir / f"line_{i:03d}.wav"
        meta = synthesize_tts_wav(str(text), clip)
        engines.append(str(meta.get("engine")))
        clip_paths.append(clip)

    # Build concat list with optional silence gaps via ffmpeg filter_complex
    # Simpler: apad each clip then concat demuxer with silence wavs
    silence_wav = work_dir / "gap_silence.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
            "-t", str(gap_seconds),
            str(silence_wav),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    concat_list = work_dir / f"{stem}_concat.txt"
    lines_out: List[str] = []
    spans: List[Dict[str, float]] = []
    t = 0.0
    for i, clip in enumerate(clip_paths):
        dur = audio_duration_seconds(clip)
        spans.append({"index": i, "start": t, "end": t + dur, "duration": dur})
        lines_out.append(f"file '{clip.resolve()}'")
        t += dur
        if i < len(clip_paths) - 1 and silence_wav.exists() and gap_seconds > 0:
            lines_out.append(f"file '{silence_wav.resolve()}'")
            t += gap_seconds

    concat_list.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    combined = work_dir / f"{stem}.wav"
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(combined),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # re-encode fallback
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                str(combined),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"concat narration failed: {proc.stderr}")

    total = audio_duration_seconds(combined)
    # Cap by speed-up if over max
    speed_factor = 1.0
    if total > max_total_seconds and total > 0:
        speed_factor = total / max_total_seconds
        sped = work_dir / f"{stem}_capped.wav"
        # atempo accepts 0.5-2.0; chain if needed
        tempo = speed_factor
        filters: List[str] = []
        # We need to speed UP audio so duration shrinks: atempo > 1
        while tempo > 2.0:
            filters.append("atempo=2.0")
            tempo /= 2.0
        if tempo < 0.5:
            tempo = 0.5
        filters.append(f"atempo={tempo:.6f}")
        af = ",".join(filters)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(combined),
                "-filter:a", af,
                str(sped),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        combined = sped
        # scale spans
        inv = 1.0 / speed_factor
        for s in spans:
            s["start"] *= inv
            s["end"] *= inv
            s["duration"] *= inv
        total = audio_duration_seconds(combined)

    return {
        "wav": str(combined),
        "spans": spans,
        "total_duration_sec": total,
        "tts_engines": engines,
        "speed_factor": speed_factor,
        "source": "tts",
    }


_SILENCE_START_RE = re.compile(
    r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)"
)
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*([0-9]+(?:\.[0-9]+)?)"
)


def silencedetect_command(
    audio_path: Path,
    *,
    noise_db: float = SHIPVIDEO_AUDIT_SILENCE_NOISE_DB,
    min_silence: float = SHIPVIDEO_AUDIT_SILENCE_MIN_DURATION,
) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null",
        "-",
    ]


def run_silencedetect(
    audio_path: Path,
    *,
    noise_db: float = SHIPVIDEO_AUDIT_SILENCE_NOISE_DB,
    min_silence: float = SHIPVIDEO_AUDIT_SILENCE_MIN_DURATION,
) -> Dict[str, Any]:
    audio_path = Path(audio_path)
    cmd = silencedetect_command(audio_path, noise_db=noise_db, min_silence=min_silence)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    # silencedetect logs to stderr
    log = (proc.stderr or "") + "\n" + (proc.stdout or "")
    silence_regions: List[Dict[str, float]] = []
    pending_start: Optional[float] = None
    for line in log.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            pending_start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end:
            end = float(m_end.group(1))
            dur = float(m_end.group(2))
            start = pending_start if pending_start is not None else max(0.0, end - dur)
            silence_regions.append({"start": start, "end": end, "duration": dur})
            pending_start = None

    total = audio_duration_seconds(audio_path)
    speech_segments = silence_regions_to_speech(silence_regions, total)
    return {
        "command": cmd,
        "command_str": " ".join(cmd),
        "returncode": proc.returncode,
        "stderr_tail": "\n".join(log.splitlines()[-40:]),
        "silence_regions": silence_regions,
        "speech_segments": speech_segments,
        "audio_duration_sec": total,
        "noise_db": noise_db,
        "min_silence": min_silence,
    }


def silence_regions_to_speech(
    silence_regions: Sequence[Dict[str, float]],
    total_duration: float,
) -> List[Dict[str, float]]:
    """Invert silence regions into speech segments [0, total)."""
    if total_duration <= 0:
        return []
    regions = sorted(silence_regions, key=lambda r: r["start"])
    speech: List[Dict[str, float]] = []
    cursor = 0.0
    for sil in regions:
        s = max(0.0, float(sil["start"]))
        e = min(total_duration, float(sil["end"]))
        if s > cursor + 0.02:
            speech.append({"start": cursor, "end": s, "duration": s - cursor})
        cursor = max(cursor, e)
    if cursor < total_duration - 0.02:
        speech.append(
            {"start": cursor, "end": total_duration, "duration": total_duration - cursor}
        )
    if not speech:
        speech.append({"start": 0.0, "end": total_duration, "duration": total_duration})
    return speech


def align_texts_to_speech_segments(
    texts: Sequence[str],
    speech_segments: Sequence[Dict[str, float]],
    *,
    total_duration: float,
) -> List[Dict[str, Any]]:
    """Map N subtitle lines onto speech segments (merge/split by count)."""
    texts = [str(t or "").strip() for t in texts]
    n = len(texts)
    if n == 0:
        return []
    segs = list(speech_segments)
    if not segs:
        # equal fallback only if no speech detected
        if total_duration <= 0:
            total_duration = float(n)
        per = total_duration / n
        return [
            {
                "index": i + 1,
                "start": i * per,
                "end": (i + 1) * per,
                "text": texts[i],
                "source": "equal_fallback",
            }
            for i in range(n)
        ]

    # If more speech chunks than texts, merge consecutive speech into n buckets by duration weight
    if len(segs) >= n:
        # Greedy: assign each segment to current line until proportional duration filled
        total_speech = sum(float(s["duration"]) for s in segs) or 1.0
        target = total_speech / n
        cues: List[Dict[str, Any]] = []
        seg_i = 0
        for line_i in range(n):
            acc = 0.0
            start = float(segs[seg_i]["start"]) if seg_i < len(segs) else 0.0
            end = start
            while seg_i < len(segs):
                s = segs[seg_i]
                if acc > 0 and acc >= target * 0.85 and line_i < n - 1:
                    break
                end = float(s["end"])
                acc += float(s["duration"])
                seg_i += 1
                if acc >= target and line_i < n - 1:
                    break
            if line_i == n - 1 and segs:
                end = float(segs[-1]["end"])
            cues.append(
                {
                    "index": line_i + 1,
                    "start": start,
                    "end": max(end, start + 0.05),
                    "text": texts[line_i],
                    "source": "silencedetect",
                }
            )
        return cues

    # Fewer speech segments than texts: split each speech segment by text share
    cues = []
    # distribute texts across segments proportionally to segment duration
    total_speech = sum(float(s["duration"]) for s in segs) or 1.0
    # assign text counts per segment
    counts = []
    remaining = n
    for i, s in enumerate(segs):
        if i == len(segs) - 1:
            counts.append(remaining)
        else:
            share = max(1, round(n * float(s["duration"]) / total_speech))
            share = min(share, remaining - (len(segs) - i - 1))
            counts.append(share)
            remaining -= share
    t_i = 0
    for seg, count in zip(segs, counts):
        if count <= 0:
            continue
        span = float(seg["duration"]) / count
        for j in range(count):
            if t_i >= n:
                break
            st = float(seg["start"]) + j * span
            en = st + span
            cues.append(
                {
                    "index": t_i + 1,
                    "start": st,
                    "end": en,
                    "text": texts[t_i],
                    "source": "silencedetect_split",
                }
            )
            t_i += 1
    while t_i < n:
        last_end = cues[-1]["end"] if cues else 0.0
        cues.append(
            {
                "index": t_i + 1,
                "start": last_end,
                "end": last_end + 0.5,
                "text": texts[t_i],
                "source": "tail",
            }
        )
        t_i += 1
    return cues


def cues_from_tts_spans(
    texts: Sequence[str],
    spans: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for i, text in enumerate(texts):
        if i < len(spans):
            sp = spans[i]
            cues.append(
                {
                    "index": i + 1,
                    "start": float(sp["start"]),
                    "end": float(sp["end"]),
                    "text": str(text),
                    "source": "tts_span",
                }
            )
        else:
            prev_end = cues[-1]["end"] if cues else 0.0
            cues.append(
                {
                    "index": i + 1,
                    "start": prev_end,
                    "end": prev_end + 0.5,
                    "text": str(text),
                    "source": "tts_span_tail",
                }
            )
    return cues


def prepare_audio_and_cues(
    texts: Sequence[str],
    work_dir: Path,
    *,
    stem: str,
    existing_media: Optional[Path] = None,
    max_total_seconds: float = SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
) -> Dict[str, Any]:
    """
    Prefer audio on existing_media; else TTS narration.
    Run silencedetect and align subtitle texts to speech segments.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    texts = [str(t or "").strip() or f"Step {i+1}" for i, t in enumerate(texts)]

    audio_path: Optional[Path] = None
    audio_source = "none"
    tts_meta: Dict[str, Any] = {}

    if existing_media and Path(existing_media).exists() and media_has_audio(Path(existing_media)):
        extracted = work_dir / f"{stem}_extracted.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(existing_media),
                "-vn", "-ac", "1", "-ar", "22050",
                str(extracted),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        audio_path = extracted
        audio_source = "captured"
    else:
        tts_meta = build_narration_audio(
            texts, work_dir, stem=stem, max_total_seconds=max_total_seconds
        )
        audio_path = Path(tts_meta["wav"])
        audio_source = "tts"

    assert audio_path is not None
    detect = run_silencedetect(audio_path)
    speech = detect["speech_segments"]

    if audio_source == "tts" and tts_meta.get("spans"):
        # Prefer exact TTS line spans; still attach silencedetect for inspection
        cues = cues_from_tts_spans(texts, tts_meta["spans"])
        # refine ends with speech segments if count matches
        if len(speech) == len(texts):
            cues = align_texts_to_speech_segments(
                texts, speech, total_duration=float(detect["audio_duration_sec"])
            )
    else:
        cues = align_texts_to_speech_segments(
            texts, speech, total_duration=float(detect["audio_duration_sec"])
        )

    return {
        "audio_path": str(audio_path),
        "audio_source": audio_source,
        "tts": tts_meta,
        "silencedetect": detect,
        "speech_segments": speech,
        "cues": cues,
        "audio_duration_sec": detect["audio_duration_sec"],
    }
