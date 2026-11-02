# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"                      # install (ffmpeg optional; imageio-ffmpeg is the fallback)
python3 lyrics_sync_ui.py                    # web studio at http://127.0.0.1:8765
python3 lyrics_sync_ui.py --project-root /path/to/assets
python3 lyrics_sync.py --song "<name>"       # CLI: auto-discover assets, align, burn captions
python3 lyrics_sync.py video.mp4 lyrics.txt  # CLI with explicit paths

ruff check .                                 # lint (CI runs this; rules are pinned in pyproject.toml)
python -m unittest discover -s tests -v      # tests (CI uses unittest, on Python 3.10–3.12)
python -m unittest tests.test_video_fx.<Class>.<test_name>   # single test
```

Flat layout: top-level modules (`lyrics_sync`, `lyrics_sync_ui`, `video_fx`, `mov_to_mp4`) are listed in `pyproject.toml` `py-modules`; there is no package directory. Python >= 3.10.

## Architecture

- **`lyrics_sync.py`** – core pipeline and CLI. Parses sectioned lyrics `.txt` (`[Verse-1]` headers plus a `structure:` list that expands repeated sections), force-aligns with stable-ts/Whisper to word timings, builds an ASS karaoke subtitle file (`build_ass`, styled by the `CaptionStyle` dataclass), and burns it in via ffmpeg (`render`, supports `preview=(start, dur)`, progress and cancel callbacks). Also defines the asset layout constants.
- **`video_fx.py`** – "Lo-Fi Emo-Trap" audio-reactive post-processing. `process_video_lofi` reads frames sequentially with OpenCV, analyses audio bands with librosa (`analyze_audio_bands` → `shape_envelope` for gain/attack/release), runs `apply_fx_frame` per frame (zoom, wash, fog, chromatic aberration, glitch, scanlines, noise) and pipes the frames straight into ffmpeg together with the audio. `render_fx_frame` runs the same stack on a single frame for fast tuning. Configured by `AestheticConfig`.
- **FX knobs are spec-driven**: every numeric knob has an entry in `AESTHETIC_SPECS` (range/step/label/group) and every effect a group in `AESTHETIC_GROUPS`; defaults equal the old hard-coded constants (a test pins the default output to the legacy pipeline). `GET /api/aesthetics` serves `aesthetic_schema()` and `web/app.js` builds the sliders from it, so a new knob = a dataclass field + a spec entry + use it in `apply_fx_frame` (a test fails if a numeric field has no spec). `AestheticConfig.from_dict` coerces and clamps untrusted input.
- **`lyrics_sync_ui.py`** – FastAPI app serving `web/` (vanilla JS, no build step) plus `/api/*`. Long tasks (`/api/align`, `/api/preview`, `/api/render`) run on a 2-worker `ThreadPoolExecutor`, tracked in the in-memory `jobs` dict; the frontend polls `/api/jobs/{id}` and can POST `/cancel` (cooperative via `cancel_check`). Preview and render are two-stage: burn subtitles with ffmpeg, then optionally run `process_video_lofi` over the result.
- **Lyric editing**: the line list in `web/app.js` edits text/start/end and auto-saves with `PUT /api/timings/{song}`, which runs `normalise_timings` (`lyrics_sync.py`: sort, min length, clamp overlaps, rebuild karaoke `words` — same word count keeps timings, otherwise redistributed by length) and writes the exact file preview/render read, keeping a one-time `.bak`. Preview/render read timings from disk, so the UI flushes pending saves first. Re-align overwrites edits.
- **Presets** are `{style, aesthetics}` (either half may be `null`); old flat style-only preset files are still read. Built-in FX looks live in `FX_LOOK_PRESETS`.
- **`web/app.js`** – the studio UI. The live player uses a CSS approximation of the effects (scanlines, fog, wash, chromatic shift); zoom, glitch, noise and frame rate only appear in rendered previews or `POST /api/fx-frame` (single frame), so changes to an effect in `video_fx.py` may need a matching tweak in `updateLofiPreviewCSS`. The rendered 10s preview plays in a modal (`#previewPanel` / `<video id="previewVideo">`) and the FX frame in a floating panel (`#fxFramePanel`); both are `position: fixed` on purpose, because the stage is a fixed-height, non-scrolling flex column where anything added inline can only get space by squeezing the video. `mainVideo` always stays on the source so `currentTime` is song time. The caption overlay is positioned in pixels from the video's on-screen box, so it is refreshed by a `ResizeObserver` (`setupOverlayTracking`), not just `timeupdate`.
- **`CaptionStyle` / `AestheticConfig`** are the contract between the UI (JSON payloads, presets) and the backend (`from_dict`); adding a setting means touching the dataclass, the `index.html` control, `app.js`, and usually `build_ass` or `video_fx`.

## Asset conventions

`PROJECT_ROOT` comes from the `LYRICS_PROJECT_ROOT` env var (set by `--project-root`), defaulting to the parent of this directory (not this directory). Under it:
- `lyrics/<song>.txt`, `wav_files/<song>.wav`, `sm_content/<song> - Compose.mp4`
- outputs: `sm_content/<song> - lyrics.mp4` and alignment cache `sm_content/<song> - lyrics.json`; previews in `sm_content/previews/`; presets in `.lyrics_sync/presets.json`

Preview clips (`<job id>.mp4/.ass`) accumulate in `sm_content/previews/`; `prune_previews` keeps the newest 12.

Song/file lookups go through `find_closest` / `normalise_name` (fuzzy match, with `exclude` suffixes to skip prior outputs), so don't assume exact filenames. Importing `lyrics_sync_ui` creates the preview and config directories as a side effect.

## Notes

- README mentions `cd scripts` before install; this repo has no `scripts/` dir (it was extracted from one), so install from the repo root. Docstrings in `lyrics_sync.py` have the same stale `scripts/` paths.
- The `agent.md` file referenced in the `/init` request does not exist in the repo.
- CI installs `.[dev]` (the tests import cv2/librosa/fastapi). API tests use `fastapi.testclient` and patch `lyrics_sync_ui.VIDEO_DIR` / `PRESETS_FILE`; they skip if fastapi isn't installed.
