#!/usr/bin/env python3
"""Local FastAPI web UI for lyrics synchronization, subtitle styling, and video rendering."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lyrics_sync import (
    AUDIO_DIR,
    CaptionStyle,
    LYRICS_DIR,
    PROJECT_ROOT,
    VIDEO_DIR,
    align_lyrics,
    build_ass,
    find_closest,
    find_ffmpeg,
    get_duration,
    has_audio,
    normalise_name,
    parse_lyrics,
    render,
)
from video_fx import AestheticConfig, process_video_lofi

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
    """Return default aesthetic toggles and config."""
    return AestheticConfig().to_dict()


@app.get("/api/presets")
def get_presets():
    if PRESETS_FILE.exists():
        try:
            return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Default presets
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
def save_preset(preset_name: str, style_data: dict = Body(...)):
    presets = get_presets()
    presets[preset_name] = CaptionStyle.from_dict(style_data).to_dict()
    PRESETS_FILE.write_text(json.dumps(presets, indent=2), encoding="utf-8")
    return {"status": "ok", "presets": presets}


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

    job = Job("align", song_name)
    with jobs_lock:
        jobs[job.id] = job

    def run():
        job.status = "running"
        job.message = "Loading audio & Whisper model..."
        try:
            video = find_closest(VIDEO_DIR, song_name, {".mp4", ".mov"}, exclude=(" - lyrics", "_lyrics"))
            lyrics_path = find_closest(LYRICS_DIR, song_name, {".txt"}) or find_closest(PROJECT_ROOT, song_name, {".txt"})
            if not video or not lyrics_path:
                raise RuntimeError(f"Missing video or lyrics for {song_name}")

            lines = parse_lyrics(lyrics_path)
            audio = video
            # If explicit wav or no audio in video, fallback
            if not has_audio(video):
                wav = find_closest(AUDIO_DIR, song_name, {".wav"})
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

                process_video_lofi(
                    temp_sub_clip,
                    out_clip,
                    audio_source=video,
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
        temp_clean_render = None
        try:
            style = CaptionStyle.from_dict(style_dict)
            aligned = json.loads(timings_path.read_text(encoding="utf-8"))
            total_dur = get_duration(video)
            ass_text = build_ass(aligned, total_dur, style=style)

            final_ass = VIDEO_DIR / f"{song_name} - lyrics.ass"
            final_ass.write_text(ass_text, encoding="utf-8")

            final_out = VIDEO_DIR / f"{song_name} - lyrics.mp4"
            ffmpeg = find_ffmpeg()

            if lofi_mode:
                temp_clean_render = VIDEO_DIR / f"{song_name} - lyrics_clean.mp4"
                target_burn = temp_clean_render
            else:
                target_burn = final_out

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
                    temp_clean_render,
                    final_out,
                    audio_source=video,
                    config=cfg,
                    on_progress=on_lofi_progress,
                    cancel_check=lambda: job.cancel_requested,
                )

            job.status = "completed"
            job.progress = 100.0
            job.message = "Export completed!"
            job.output_url = f"/api/video/rendered/{song_name}"
        except Exception as e:
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.error = str(e)
            job.message = f"Render failed: {e}"

    executor.submit(run)
    return {"job_id": job.id}


# Mount web assets
WEB_DIR = Path(__file__).resolve().parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


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
    args = parser.parse_args()

    global PROJECT_ROOT, LYRICS_DIR, AUDIO_DIR, VIDEO_DIR, PREVIEWS_DIR, CONFIG_DIR, PRESETS_FILE
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

    print(f"Starting content_tools studio on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
