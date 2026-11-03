#!/usr/bin/env python3
"""Local FastAPI web UI for lyrics synchronization, subtitle styling, and video rendering."""

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import cv2
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lyrics_sync import (
    AUDIO_DIR,
    LYRICS_DIR,
    PROJECT_ROOT,
    VIDEO_DIR,
    CaptionStyle,
    align_lyrics,
    build_ass,
    find_closest,
    find_ffmpeg,
    get_duration,
    has_audio,
    normalise_name,
    normalise_timings,
    parse_lyrics,
    render,
)
from snippets import (
    MAX_SNIPPET_SECONDS,
    TRANSITION_LABELS,
    TRANSITIONS,
    SnippetConfig,
    render_snippet,
    render_snippet_frame,
    sync_to_beats,
)
from video_fx import (
    AestheticConfig,
    aesthetic_schema,
    process_video_lofi,
    render_fx_clip,
    render_fx_frame,
)

app = FastAPI(title="content_tools - Lyrics & Subtitles Studio")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = ThreadPoolExecutor(max_workers=2)

# In-memory jobs tracking
jobs_lock = threading.Lock()
jobs = {}

PREVIEWS_DIR = VIDEO_DIR / "previews"
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = PROJECT_ROOT / ".lyrics_sync"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
PRESETS_FILE = CONFIG_DIR / "presets.json"
# End-card logo for snippets (override with --logo)
LOGO_FILE = PROJECT_ROOT / "gemini_gens" / "Gemini_Generated_Image_e6vvqpe6vvqpe6vv.jpeg"


def get_project_root() -> Path:
    return PROJECT_ROOT


def list_system_fonts() -> list[str]:
    """Discover installed system font families or return common fallbacks."""
    fonts = [
        "Helvetica",
        "Arial",
        "Futura",
        "Avenir",
        "Impact",
        "Trebuchet MS",
        "Verdana",
        "Georgia",
        "Times New Roman",
        "Courier New",
    ]
    try:
        res = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            found = set()
            for line in res.stdout.splitlines():
                for part in line.split(","):
                    p = part.strip()
                    if p and not p.startswith("."):
                        found.add(p)
            if found:
                for f in sorted(found):
                    if f not in fonts:
                        fonts.append(f)
    except Exception:
        pass
    return fonts


def range_streaming_response(request: Request, file_path: Path, media_type: str = "video/mp4"):
    """Handle HTTP range requests for smooth HTML5 video scrubbing and seeking."""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    if not range_header:
        return FileResponse(file_path, media_type=media_type)

    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        headers = {"Content-Range": f"bytes */{file_size}"}
        return Response(status_code=416, headers=headers)

    chunk_length = end - start + 1

    def iterfile():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_length
            chunk_size = 1024 * 512
            while remaining > 0:
                read_len = min(remaining, chunk_size)
                data = f.read(read_len)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_length),
        "Content-Type": media_type,
    }
    return StreamingResponse(iterfile(), status_code=206, headers=headers)


@app.get("/api/songs")
def get_songs():
    """Scan the project directories and return discoverable songs and their asset states."""
    known = set()
    for d in (LYRICS_DIR, AUDIO_DIR, VIDEO_DIR):
        if d.is_dir():
            for p in d.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    if " - lyrics" not in p.name and "_lyrics" not in p.name:
                        stem = p.stem.replace(" - Compose", "")
                        known.add(stem)

    songs = []
    for name in sorted(known):
        lyrics = find_closest(LYRICS_DIR, name, {".txt"}) or find_closest(PROJECT_ROOT, name, {".txt"})
        video = (
            find_closest(VIDEO_DIR, name, {".mp4", ".mov", ".m4v"}, exclude=(" - lyrics", "_lyrics"))
            or find_closest(PROJECT_ROOT, name, {".mp4", ".mov", ".m4v"}, exclude=(" - lyrics", "_lyrics"))
        )
        wav = find_closest(AUDIO_DIR, name, {".wav"})
        rendered = find_closest(VIDEO_DIR, f"{name} - lyrics", {".mp4"})
        timings = None
        if video:
            candidate = VIDEO_DIR / f"{name} - lyrics.json"
            if candidate.exists():
                timings = candidate

        songs.append(
            {
                "id": normalise_name(name),
                "name": name,
                "lyrics_path": str(lyrics) if lyrics else None,
                "video_path": str(video) if video else None,
                "wav_path": str(wav) if wav else None,
                "rendered_path": str(rendered) if rendered else None,
                "has_timings": timings is not None,
                "ready_to_preview": (video is not None and lyrics is not None),
            }
        )

    return {"songs": songs}


@app.get("/api/fonts")
def get_fonts():
    return {"fonts": list_system_fonts(), "default": "Helvetica"}


@app.get("/api/timings/{song_name}")
def get_timings(song_name: str):
    """Retrieve existing aligned timings JSON or return 404."""
    candidate = VIDEO_DIR / f"{song_name} - lyrics.json"
    if not candidate.exists():
        # Try finding via fuzzy lookup
        video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
        if video:
            c2 = VIDEO_DIR / f"{video.stem} - lyrics.json"
            if c2.exists():
                candidate = c2

    if candidate.exists():
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return {"timings": data, "path": str(candidate)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read timings: {e}")

    raise HTTPException(status_code=404, detail="No timings found for song")


@app.put("/api/timings/{song_name}")
def put_timings(song_name: str, payload: dict = Body(...)):
    """Persist user-edited line text/start/end and return the normalised result.

    Writes the exact file that /api/preview and /api/render read. The first save keeps the
    original alignment as ``<file>.bak`` so a bad edit (or a re-align) can be undone.
    """
    try:
        timings = normalise_timings(payload.get("timings"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not timings:
        raise HTTPException(status_code=400, detail="At least one non-empty line is required")

    # Only allow songs that exist (get_timings does the same fuzzy lookup), so a crafted
    # name can't create arbitrary files.
    video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")
    target = VIDEO_DIR / f"{song_name} - lyrics.json"
    if target.resolve().parent != VIDEO_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid song name")

    backup = target.with_name(target.name + ".bak")
    if target.exists() and not backup.exists():
        shutil.copyfile(target, backup)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return {"timings": timings, "path": str(target)}


@app.get("/api/video/source/{song_name}")
def stream_source_video(song_name: str, request: Request):
    video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov", ".m4v"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")
    return range_streaming_response(request, video)


@app.get("/api/video/preview/{preview_id}")
def stream_preview_video(preview_id: str, request: Request):
    clip = PREVIEWS_DIR / f"{preview_id}.mp4"
    if not clip.exists():
        raise HTTPException(status_code=404, detail="Preview clip not found")
    return range_streaming_response(request, clip)


@app.get("/api/video/rendered/{song_name}")
def stream_rendered_video(song_name: str, request: Request):
    clip = VIDEO_DIR / f"{song_name} - lyrics.mp4"
    if not clip.exists():
        raise HTTPException(status_code=404, detail="Rendered video not found")
    return range_streaming_response(request, clip)


@app.get("/api/aesthetics")
def get_aesthetics():
    """Defaults plus the knob schema (groups, labels, ranges) the UI builds its controls from."""
    return aesthetic_schema()


_FX_FRAME_SLOTS = threading.BoundedSemaphore(2)


@app.post("/api/fx-frame")
def fx_frame(payload: dict = Body(...)):
    """Render one source frame with the Lo-Fi stack applied (JPEG) for fast knob tuning."""
    song_name = payload.get("song_name")
    video = find_closest(VIDEO_DIR, song_name or "", {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")
    try:
        time_sec = float(payload.get("time_sec", 0.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_sec must be a number")
    cfg = AestheticConfig.from_dict(payload.get("aesthetics") or {})
    try:
        # Sync endpoints run on a big thread pool; cap concurrent renders so slider spam can't
        # spawn a pile of ffmpeg/librosa jobs.
        with _FX_FRAME_SLOTS:
            frame = render_fx_frame(video, time_sec, cfg)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode frame")
    return Response(content=buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/fx-clip")
def fx_clip(payload: dict = Body(...)):
    """A short, low-resolution animation (WebP, or GIF as a fallback) of the Lo-Fi stack so audio-reactive effects are visible."""
    song_name = payload.get("song_name")
    video = find_closest(VIDEO_DIR, song_name or "", {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(
            status_code=404,
            detail=f"No source video for '{song_name}'. Put '<song> - Compose.mp4' in {VIDEO_DIR}.",
        )
    try:
        start = float(payload.get("time_sec", 0.0))
        duration = float(payload.get("duration", 1.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_sec and duration must be numbers")
    cfg = AestheticConfig.from_dict(payload.get("aesthetics") or {})
    try:
        with _FX_FRAME_SLOTS:
            frames = render_fx_clip(video, start, min(max(duration, 0.2), 3.0), cfg)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    from PIL import Image, features

    images = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
    frame_ms = max(20, int(round(1000.0 * min(max(duration, 0.2), 3.0) / len(frames))))
    buf = io.BytesIO()
    # Animated WebP is ~10x smaller than GIF for grainy footage and every current browser plays it
    # in an <img>; fall back to GIF only if this Pillow build lacks WebP.
    if features.check("webp"):
        fmt, mime, opts = "WEBP", "image/webp", {"quality": 60}
    else:
        fmt, mime, opts = "GIF", "image/gif", {"optimize": False}
    images[0].save(buf, format=fmt, save_all=True, append_images=images[1:], duration=frame_ms, loop=0, **opts)
    return Response(content=buf.getvalue(), media_type=mime, headers={"Cache-Control": "no-store"})


def _normalise_preset(raw) -> dict:
    """Presets are ``{"style": {...} | None, "aesthetics": {...} | None}``.

    A ``None`` half means "leave that part of the studio as it is" when the preset is applied.
    Older preset files stored a bare caption-style dict; those become style-only presets.
    """
    if not isinstance(raw, dict):
        return {"style": None, "aesthetics": None}
    if "style" in raw or "aesthetics" in raw:
        style, fx = raw.get("style"), raw.get("aesthetics")
    else:
        style, fx = raw, None
    if isinstance(style, dict):
        style = CaptionStyle.from_dict(style).to_dict()
        style.pop("time_offset", None)  # per-song timing, not part of a look
    else:
        style = None
    return {
        "style": style,
        "aesthetics": AestheticConfig.from_dict(fx).to_dict() if isinstance(fx, dict) else None,
    }


# Built-in FX looks (style left untouched). Values are overrides on top of AestheticConfig defaults.
FX_LOOK_PRESETS = {
    "FX · Original": {},
    "FX · Subtle": {
        "wash_amount": 0.5, "zoom_amount": 0.012, "fog_amount": 0.6, "ca_shift_px": 2,
        "glitch_probability": 0.2, "scanline_strength": 0.05, "noise_gain": 0.6, "target_fps": 24,
    },
    "FX · Faded Film": {
        "color_grade": True, "grade_fade": 28, "grade_contrast": 0.92, "grade_saturation": 0.8,
        "wash_amount": 0.4, "wash_tint_b": 4, "wash_tint_g": 6, "wash_tint_r": 14,
        "fog_amount": 0.5, "fog_haze": 0.25, "scanline_strength": 0.0, "noise_size": 2,
        "noise_strength": 0.45, "ca_shift_px": 1, "glitch_probability": 0.0, "target_fps": 24,
    },
    "FX · VHS Tape": {
        "ca_shift_px": 4, "ca_vertical_px": 2, "ca_pulse": 4, "scanline_strength": 0.18,
        "scanline_thickness": 2, "scanline_spacing": 5, "glitch_threshold": 0.3,
        "glitch_probability": 0.6, "glitch_max_slices": 9, "glitch_slice_height": 60,
        "glitch_swap_chance": 0.05, "noise_size": 2, "fog_haze": 0.15, "target_fps": 20,
    },
    "FX · Heavy Lo-Fi": {
        "wash_tint": 1.5, "zoom_amount": 0.05, "fog_amount": 1.2, "fog_blur": 14, "ca_shift_px": 6,
        "glitch_threshold": 0.35, "glitch_probability": 0.7, "glitch_max_shift": 40,
        "scanline_strength": 0.2, "noise_gain": 1.5, "target_fps": 12, "low_release": 120,
    },
}


def _builtin_presets() -> dict:
    """Presets that ship with the app: always present, never editable or deletable."""
    presets = {name: _normalise_preset(p) for name, p in _default_style_presets().items()}
    for name, overrides in FX_LOOK_PRESETS.items():
        presets[name] = {"style": None, "aesthetics": AestheticConfig.from_dict(overrides).to_dict()}
    return presets


def _read_user_presets() -> dict:
    """The presets saved by the user (built-in names in an old file are ignored)."""
    if not PRESETS_FILE.exists():
        return {}
    try:
        stored = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(stored, dict):
        return {}
    builtin = _builtin_presets()
    return {name: _normalise_preset(p) for name, p in stored.items() if name not in builtin}


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _all_presets() -> dict:
    presets = {name: {**p, "builtin": True} for name, p in _builtin_presets().items()}
    presets.update({name: {**p, "builtin": False} for name, p in _read_user_presets().items()})
    return presets


def _clean_preset_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=400, detail="Preset name must be 1-80 characters")
    return name


@app.get("/api/presets")
def get_presets():
    return _all_presets()


def _default_style_presets() -> dict:
    default_presets = {
        "Lo-Fi Emo-Trap (Default)": CaptionStyle(
            font_name="Helvetica",
            font_size=48,
            box_width=680,
            lowercase=True,
            text_opacity=0.88,
            opacity_trailing=True,
            fade_in_ms=180,
            fade_out_ms=350,
        ).to_dict(),
        "Narrow Blocky Column": CaptionStyle(
            font_name="Helvetica",
            font_size=44,
            box_width=480,
            lowercase=True,
            text_opacity=0.85,
            opacity_trailing=True,
            margin_v=780,
        ).to_dict(),
        "Helvetica Modern Clean": CaptionStyle(
            font_name="Helvetica",
            font_size=48,
            box_width=800,
            lowercase=False,
            text_opacity=1.0,
            opacity_trailing=False,
        ).to_dict(),
        "Minimal Ghost Text": CaptionStyle(
            font_name="Helvetica",
            font_size=42,
            box_width=620,
            primary_color="#FFFFFF",
            secondary_color="#B0B0B0",
            text_opacity=0.75,
            outline=2.0,
            shadow=1.0,
            fade_in_ms=220,
            fade_out_ms=400,
            lowercase=True,
        ).to_dict(),
        "Bold Impact Pop": CaptionStyle(
            font_name="Impact",
            font_size=56,
            primary_color="#00FFAA",
            secondary_color="#FFFFFF",
            outline=5.0,
            shadow=3.0,
            pop_scale=20.0,
            lowercase=False,
        ).to_dict(),
    }
    return default_presets


@app.post("/api/presets/{preset_name}")
def save_preset(preset_name: str, data: dict = Body(...)):
    """Save ``{"style": {...}, "aesthetics": {...}}`` (a bare style dict is still accepted).
    Saving under an existing user preset's name overwrites it; built-in names are protected."""
    name = _clean_preset_name(preset_name)
    if name in _builtin_presets():
        raise HTTPException(status_code=400, detail="That is a built-in preset; choose another name")
    user = _read_user_presets()
    user[name] = _normalise_preset(data)
    _write_json_atomic(PRESETS_FILE, user)
    return {"status": "ok", "presets": _all_presets()}


@app.delete("/api/presets/{preset_name}")
def delete_preset(preset_name: str):
    if preset_name in _builtin_presets():
        raise HTTPException(status_code=400, detail="Built-in presets cannot be deleted")
    user = _read_user_presets()
    if preset_name not in user:
        raise HTTPException(status_code=404, detail="Preset not found")
    del user[preset_name]
    _write_json_atomic(PRESETS_FILE, user)
    return {"status": "ok", "presets": _all_presets()}


@app.post("/api/presets/{preset_name}/rename")
def rename_preset(preset_name: str, data: dict = Body(...)):
    new_name = _clean_preset_name(data.get("new_name", ""))
    builtin = _builtin_presets()
    if preset_name in builtin or new_name in builtin:
        raise HTTPException(status_code=400, detail="Built-in presets cannot be renamed or replaced")
    user = _read_user_presets()
    if preset_name not in user:
        raise HTTPException(status_code=404, detail="Preset not found")
    if new_name != preset_name and new_name in user:
        raise HTTPException(status_code=409, detail="A preset with that name already exists")
    # keep the list order: rebuild with the new key in the old slot
    user = {(new_name if k == preset_name else k): v for k, v in user.items()}
    _write_json_atomic(PRESETS_FILE, user)
    return {"status": "ok", "presets": _all_presets()}


# Per-song settings that are not part of a look (currently the global subtitle offset).
def _song_settings_file() -> Path:
    return PRESETS_FILE.parent / "song_settings.json"


def _read_song_settings() -> dict:
    try:
        data = json.loads(_song_settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.get("/api/song-settings/{song_name}")
def get_song_settings(song_name: str):
    entry = _read_song_settings().get(song_name)
    offset = entry.get("time_offset", 0.0) if isinstance(entry, dict) else 0.0
    return {"time_offset": offset if isinstance(offset, (int, float)) else 0.0}


@app.put("/api/song-settings/{song_name}")
def put_song_settings(song_name: str, data: dict = Body(...)):
    try:
        offset = float(data.get("time_offset", 0.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_offset must be a number")
    if not math.isfinite(offset):
        raise HTTPException(status_code=400, detail="time_offset must be a number")
    offset = round(max(-5.0, min(5.0, offset)), 2)
    settings = _read_song_settings()
    if offset:
        settings[song_name] = {"time_offset": offset}
    else:
        settings.pop(song_name, None)
    _write_json_atomic(_song_settings_file(), settings)
    return {"time_offset": offset}


class Job:
    def __init__(self, job_type: str, song_name: str):
        self.id = uuid.uuid4().hex[:10]
        self.type = job_type
        self.song_name = song_name
        self.status = "queued"          # queued, running, completed, failed, cancelled
        self.progress = 0.0
        self.message = "Initializing..."
        self.output_url = None
        self.error = None
        self.cancel_requested = False


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        j = jobs[job_id]
        return {
            "id": j.id,
            "type": j.type,
            "song_name": j.song_name,
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "output_url": j.output_url,
            "error": j.error,
        }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        jobs[job_id].cancel_requested = True
        return {"status": "cancelling"}


@app.post("/api/align")
def start_align(payload: dict = Body(...)):
    song_name = payload.get("song_name")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name required")

    # Check everything up front so the user gets a precise message instead of a failed job.
    video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    lyrics_path = find_closest(LYRICS_DIR, song_name, {".txt"}) or find_closest(PROJECT_ROOT, song_name, {".txt"})
    wav = find_closest(AUDIO_DIR, song_name, {".wav"})
    problems = []
    if not lyrics_path:
        problems.append(f"lyrics file not found (expected '{song_name}.txt' in {LYRICS_DIR})")
    if not video:
        problems.append(f"source video not found (expected '{song_name} - Compose.mp4' in {VIDEO_DIR})")
    if not video and not wav:
        problems.append(f"no audio to align either (expected '{song_name}.wav' in {AUDIO_DIR})")
    if problems:
        raise HTTPException(status_code=422, detail="Cannot align '" + song_name + "': " + "; ".join(problems) + ".")

    job = Job("align", song_name)
    with jobs_lock:
        jobs[job.id] = job

    def run():
        job.status = "running"
        job.message = "Loading audio & Whisper model..."
        try:
            lines = parse_lyrics(lyrics_path)
            if not lines:
                raise RuntimeError(
                    f"'{lyrics_path.name}' has no lyric lines. Lines must be under a [Section] header."
                )
            audio = video
            if not video or not has_audio(video):
                if not wav:
                    raise RuntimeError("Video has no audio and no WAV master found.")
                audio = wav

            job.message = "Aligning words with Whisper..."
            job.progress = 25.0
            aligned = align_lyrics(audio, lines, model_size="small", language="es")
            timings_path = VIDEO_DIR / f"{song_name} - lyrics.json"
            timings_path.write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
            job.status = "completed"
            job.progress = 100.0
            job.message = "Alignment complete."
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.message = f"Failed: {e}"

    executor.submit(run)
    return {"job_id": job.id}


MAX_PREVIEW_CLIPS = 12
_JOB_STEM = re.compile(r"^[0-9a-f]{10}(_sub)?$")


def prune_previews(keep: int = MAX_PREVIEW_CLIPS) -> None:
    """Delete the oldest generated preview clips (and their .ass files), keeping ``keep``.

    Only files named like job ids are touched, so anything else a user drops in the folder is safe.
    """
    try:
        clips = [p for p in PREVIEWS_DIR.glob("*.mp4") if _JOB_STEM.match(p.stem)]
        clips.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in clips[keep:]:
            for leftover in PREVIEWS_DIR.glob(f"{old.stem}.*"):
                leftover.unlink(missing_ok=True)
    except OSError:
        pass  # pruning is best-effort; never block a preview on it


@app.post("/api/preview")
def start_preview(payload: dict = Body(...)):
    song_name = payload.get("song_name")
    style_dict = payload.get("style", {})
    aesthetics_dict = payload.get("aesthetics", {})
    apply_lofi = payload.get("apply_lofi", True)
    start_sec = float(payload.get("start_sec", 24.0))
    duration_sec = float(payload.get("duration_sec", 10.0))

    video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    timings_path = VIDEO_DIR / f"{song_name} - lyrics.json"
    if not timings_path.exists():
        raise HTTPException(status_code=400, detail="Timings not found. Run alignment first.")

    prune_previews()
    job = Job("preview", song_name)
    with jobs_lock:
        jobs[job.id] = job

    def run():
        job.status = "running"
        job.message = "Generating subtitle file..."
        temp_sub_clip = None
        try:
            style = CaptionStyle.from_dict(style_dict)
            aligned = json.loads(timings_path.read_text(encoding="utf-8"))
            total_dur = get_duration(video)
            ass_text = build_ass(aligned, total_dur, style=style)

            temp_ass = PREVIEWS_DIR / f"{job.id}.ass"
            temp_ass.write_text(ass_text, encoding="utf-8")

            out_clip = PREVIEWS_DIR / f"{job.id}.mp4"
            ffmpeg = find_ffmpeg()

            if apply_lofi:
                temp_sub_clip = PREVIEWS_DIR / f"{job.id}_sub.mp4"
                target_burn = temp_sub_clip
            else:
                target_burn = out_clip

            def on_progress(k, v):
                if k == "out_time_us":
                    cur_s = float(v) / 1_000_000
                    scale = 30.0 if apply_lofi else 100.0
                    job.progress = min(scale - 1.0, max(5.0, (cur_s / duration_sec) * scale))
                    job.message = f"Encoding subtitles ({job.progress:.0f}%)..."

            render(
                video,
                temp_ass,
                target_burn,
                ffmpeg,
                preview=(start_sec, duration_sec),
                on_progress=on_progress,
                cancel_check=lambda: job.cancel_requested,
            )

            # Stage 2: Audio-reactive Lo-Fi pipeline if enabled
            if apply_lofi:
                job.message = "Applying Lo-Fi Emo-Trap audio reactivity..."
                job.progress = 30.0
                cfg = AestheticConfig.from_dict(aesthetics_dict)

                def on_lofi_progress(pct, msg):
                    job.progress = 30.0 + (pct * 0.70)
                    job.message = msg

                # temp_sub_clip is already cut to [start_sec, start_sec + duration_sec] and
                # keeps its audio track, so analyse/mux audio from the clip itself. Using the
                # full source here read the audio from 0s of the song instead of the window.
                process_video_lofi(
                    temp_sub_clip,
                    out_clip,
                    config=cfg,
                    on_progress=on_lofi_progress,
                    cancel_check=lambda: job.cancel_requested,
                )

            job.status = "completed"
            job.progress = 100.0
            job.message = "Preview ready."
            job.output_url = f"/api/video/preview/{job.id}"
        except Exception as e:
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.error = str(e)
            job.message = f"Error: {e}"
        finally:
            if temp_sub_clip and temp_sub_clip.exists():
                try:
                    temp_sub_clip.unlink()
                except Exception:
                    pass

    executor.submit(run)
    return {"job_id": job.id}


@app.post("/api/render")
def start_render(payload: dict = Body(...)):
    song_name = payload.get("song_name")
    style_dict = payload.get("style", {})
    aesthetics_dict = payload.get("aesthetics", {})
    lofi_mode = payload.get("lofi_mode", True)

    video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    timings_path = VIDEO_DIR / f"{song_name} - lyrics.json"
    if not timings_path.exists():
        raise HTTPException(status_code=400, detail="Timings not found. Run alignment first.")

    job = Job("render", song_name)
    with jobs_lock:
        jobs[job.id] = job

    def run():
        job.status = "running"
        job.message = "Preparing video export..."
        # Everything is written to scratch files first and only moved over the real output once
        # the whole job succeeded, so a cancelled/failed render can't clobber an earlier export.
        final_out = VIDEO_DIR / f"{song_name} - lyrics.mp4"
        scratch_clean = VIDEO_DIR / f"{song_name} - lyrics.clean.part.mp4"
        scratch_out = VIDEO_DIR / f"{song_name} - lyrics.part.mp4"
        try:
            style = CaptionStyle.from_dict(style_dict)
            aligned = json.loads(timings_path.read_text(encoding="utf-8"))
            total_dur = get_duration(video)
            ass_text = build_ass(aligned, total_dur, style=style)

            final_ass = VIDEO_DIR / f"{song_name} - lyrics.ass"
            final_ass.write_text(ass_text, encoding="utf-8")

            ffmpeg = find_ffmpeg()
            target_burn = scratch_clean if lofi_mode else scratch_out

            def on_progress(k, v):
                if k == "out_time_us":
                    cur_s = float(v) / 1_000_000
                    scale = 25.0 if lofi_mode else 100.0
                    job.progress = min(scale - 1.0, max(2.0, (cur_s / total_dur) * scale))
                    job.message = f"Rendering subtitles ({job.progress:.1f}%)..."

            render(
                video,
                final_ass,
                target_burn,
                ffmpeg,
                on_progress=on_progress,
                cancel_check=lambda: job.cancel_requested,
            )

            # Stage 2: Full Lo-Fi Audio-Reactive processing pass
            if lofi_mode:
                job.message = "Starting Lo-Fi Emo-Trap processing pass..."
                job.progress = 25.0
                cfg = AestheticConfig.from_dict(aesthetics_dict)

                def on_lofi_progress(pct, msg):
                    job.progress = 25.0 + (pct * 0.75)
                    job.message = msg

                process_video_lofi(
                    scratch_clean,
                    scratch_out,
                    audio_source=video,
                    config=cfg,
                    on_progress=on_lofi_progress,
                    cancel_check=lambda: job.cancel_requested,
                )

            os.replace(scratch_out, final_out)
            job.status = "completed"
            job.progress = 100.0
            job.message = "Export completed!"
            job.output_url = f"/api/video/rendered/{song_name}"
        except Exception as e:
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.error = str(e)
            job.message = f"Render failed: {e}"
        finally:
            for scratch in (scratch_clean, scratch_out):
                scratch.unlink(missing_ok=True)

    executor.submit(run)
    return {"job_id": job.id}


# Mount web assets
WEB_DIR = Path(__file__).resolve().parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# Snippets: short promo cuts of the rendered "<song> - lyrics.mp4" with a transition + end card
# ---------------------------------------------------------------------------

SNIPPET_SUFFIX = " - lyrics.mp4"


def _snippets_dir() -> Path:
    return VIDEO_DIR / "snippets"


def _snippet_source(song_name: str) -> Path | None:
    """The rendered video for ``song_name``: exact file name, no fuzzy matching (it is picked
    from the list /api/snippet-sources returned)."""
    if not song_name or "/" in song_name or "\\" in song_name:
        return None
    path = VIDEO_DIR / f"{song_name}{SNIPPET_SUFFIX}"
    return path if path.is_file() else None


def _fmt_stamp(t: float) -> str:
    return f"{int(t // 60):02d}m{t % 60:05.2f}s"


def _snippet_request(payload: dict) -> tuple[Path, SnippetConfig, float]:
    """Validate a snippet request up front, so problems come back as a precise 4xx message."""
    song = payload.get("song_name") or ""
    video = _snippet_source(song)
    if not video:
        raise HTTPException(
            status_code=404,
            detail=f"No rendered video for '{song}'. Render it first (expected '{song}{SNIPPET_SUFFIX}' "
                   f"in {VIDEO_DIR}).",
        )
    cfg = SnippetConfig.from_dict(payload.get("config") or {})
    duration = get_duration(video)
    if cfg.end <= cfg.start:
        raise HTTPException(status_code=422, detail="The end time must be after the start time.")
    if cfg.start >= duration:
        raise HTTPException(
            status_code=422, detail=f"Start {cfg.start:.2f}s is past the end of the video ({duration:.2f}s)."
        )
    if cfg.end > duration + 0.05:
        raise HTTPException(
            status_code=422, detail=f"End {cfg.end:.2f}s is past the end of the video ({duration:.2f}s)."
        )
    if cfg.clip_duration > MAX_SNIPPET_SECONDS:
        raise HTTPException(
            status_code=422, detail=f"Snippets are limited to {MAX_SNIPPET_SECONDS:.0f}s; this one is "
                                    f"{cfg.clip_duration:.1f}s."
        )
    if cfg.transition_duration > cfg.clip_duration:
        cfg.transition_duration = max(0.2, cfg.clip_duration)
    return video, cfg, duration


def _logo() -> Path | None:
    return LOGO_FILE if LOGO_FILE and Path(LOGO_FILE).is_file() else None


@app.get("/api/snippet-sources")
def snippet_sources():
    songs = []
    if VIDEO_DIR.is_dir():
        for p in sorted(VIDEO_DIR.iterdir()):
            if p.is_file() and p.name.endswith(SNIPPET_SUFFIX) and not p.name.startswith("."):
                if ".part" in p.name:
                    continue
                songs.append({"name": p.name[: -len(SNIPPET_SUFFIX)], "duration": get_duration(p)})
    return {
        "songs": songs,
        "transitions": [{"id": t, "label": TRANSITION_LABELS[t]} for t in TRANSITIONS],
        "defaults": SnippetConfig().to_dict(),
        "logo": str(_logo()) if _logo() else None,
    }


@app.get("/api/video/snippet-source/{song_name}")
def stream_snippet_source(song_name: str, request: Request):
    video = _snippet_source(song_name)
    if not video:
        raise HTTPException(status_code=404, detail="Rendered video not found")
    return range_streaming_response(request, video)


@app.post("/api/snippet-frame")
def snippet_frame(payload: dict = Body(...)):
    """One frame of the snippet timeline (clip, transition or end card) as a JPEG."""
    video, cfg, _ = _snippet_request(payload)
    try:
        t = float(payload.get("time_sec", cfg.clip_duration + cfg.card_duration / 2))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_sec must be a number")
    try:
        with _FX_FRAME_SLOTS:
            frame = render_snippet_frame(video, t, cfg, _logo())
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode frame")
    return Response(content=buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _snippet_job(kind: str, song: str, video: Path, cfg: SnippetConfig, out: Path, **render_kw):
    job = Job(kind, song)
    with jobs_lock:
        jobs[job.id] = job

    def run():
        job.status = "running"
        job.message = "Preparing snippet..."

        def progress(pct, msg):
            job.progress = pct
            job.message = msg

        try:
            render_snippet(
                video, out, cfg, logo_path=_logo(), on_progress=progress,
                cancel_check=lambda: job.cancel_requested, **render_kw,
            )
            job.status = "completed"
            job.progress = 100.0
            job.message = "Snippet ready." if kind == "snippet" else "Transition preview ready."
            job.output_url = (
                f"/api/video/snippet/{quote(out.name)}" if kind == "snippet"
                else f"/api/video/preview/{out.stem}"
            )
        except Exception as e:
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.error = str(e)
            job.message = f"Error: {e}"

    executor.submit(run)
    return {"job_id": job.id}


@app.post("/api/snippet")
def start_snippet(payload: dict = Body(...)):
    video, cfg, _ = _snippet_request(payload)
    song = payload["song_name"]
    out = _snippets_dir() / f"{song} - snippet {_fmt_stamp(cfg.start)}-{_fmt_stamp(cfg.end)}.mp4"
    return _snippet_job("snippet", song, video, cfg, out)


@app.post("/api/snippet-preview")
def start_snippet_preview(payload: dict = Body(...)):
    """Fast, low-res render of just the ending (a little clip, the transition and the card)."""
    video, cfg, _ = _snippet_request(payload)
    prune_previews()
    # snap to the beat grid first: the window below must be measured on the final timeline
    cfg = replace(sync_to_beats(video, cfg), beat_sync=False)
    lead = min(cfg.clip_duration, cfg.transition_duration + 1.0)
    window = (cfg.clip_duration - lead, cfg.total_duration)
    out = PREVIEWS_DIR / f"{uuid.uuid4().hex[:10]}.mp4"
    return _snippet_job(
        "snippet-preview", payload["song_name"], video, cfg, out,
        window=window, max_width=540, preset="veryfast", crf=24,
    )


@app.get("/api/snippets")
def list_snippets():
    folder = _snippets_dir()
    items = []
    if folder.is_dir():
        files = [p for p in folder.glob("*.mp4") if ".part" not in p.name]
        for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            items.append({
                "name": p.name,
                "url": f"/api/video/snippet/{quote(p.name)}",
                "size_mb": round(p.stat().st_size / 1e6, 1),
            })
    return {"snippets": items}


@app.get("/api/video/snippet/{name}")
def stream_snippet(name: str, request: Request):
    folder = _snippets_dir().resolve()
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix != ".mp4" or not path.is_file():
        raise HTTPException(status_code=404, detail="Snippet not found")
    return range_streaming_response(request, path)


@app.get("/")
def index():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>content_tools Web UI: static files missing.</h2>")


def main():
    parser = argparse.ArgumentParser(description="Run the lyrics & subtitle web studio.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    parser.add_argument("--project-root", type=Path, help="Override project root directory")
    parser.add_argument("--logo", type=Path, help="Image used on the snippet end card")
    args = parser.parse_args()

    global PROJECT_ROOT, LYRICS_DIR, AUDIO_DIR, VIDEO_DIR, PREVIEWS_DIR, CONFIG_DIR, PRESETS_FILE, LOGO_FILE
    if args.project_root:
        PROJECT_ROOT = args.project_root.expanduser().resolve()
        LYRICS_DIR = PROJECT_ROOT / "lyrics"
        AUDIO_DIR = PROJECT_ROOT / "wav_files"
        VIDEO_DIR = PROJECT_ROOT / "sm_content"
        PREVIEWS_DIR = VIDEO_DIR / "previews"
        PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR = PROJECT_ROOT / ".lyrics_sync"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PRESETS_FILE = CONFIG_DIR / "presets.json"
        LOGO_FILE = PROJECT_ROOT / "gemini_gens" / "Gemini_Generated_Image_e6vvqpe6vvqpe6vv.jpeg"
    if args.logo:
        LOGO_FILE = args.logo.expanduser().resolve()

    print(f"Starting content_tools studio on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
