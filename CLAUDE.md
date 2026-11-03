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
- **`video_fx.py`** – "Lo-Fi Emo-Trap" audio-reactive post-processing. `process_video_lofi` reads frames sequentially with OpenCV, analyses audio bands with librosa (`analyze_audio_bands` → `shape_envelope` for gain/attack/release), runs `apply_fx_frame` per frame (zoom, color wash, optional color grade, fog, chromatic aberration, glitch, scanlines, noise) and pipes the frames straight into ffmpeg together with the audio. `render_fx_frame` / `render_fx_clip` run the same stack on one frame or ~1 s of frames for fast tuning. Configured by `AestheticConfig`.
- **FX knobs are spec-driven**: every numeric knob has an entry in `AESTHETIC_SPECS` (range/step/label/group) and every effect a group in `AESTHETIC_GROUPS`; defaults equal the old hard-coded constants (a test pins the default output to the legacy pipeline). `GET /api/aesthetics` serves `aesthetic_schema()` and `web/app.js` builds the sliders from it, so a new knob = a dataclass field + a spec entry + use it in `apply_fx_frame` (a test fails if a numeric field has no spec). `AestheticConfig.from_dict` coerces and clamps untrusted input.
- **`snippets.py`** – promo snippets: cut `[start, end]` from a rendered `<song> - lyrics.mp4`, run the selected transitions (`glitch`, `static`, `crt_off`, `fade`; applied in that fixed order; they play in reverse to bring in the end card), then hold an end card built from the logo (`LOGO_FILE`, default `../gemini_gens/Gemini_Generated_Image_e6vvqpe6vvqpe6vv.jpeg`, `--logo` to override; used as given, full width; the card takes the file's own backdrop colour) plus the release text (default `20.11.2026`). `render_snippet` pipes raw frames to ffmpeg like `process_video_lofi`, writes a `.part` scratch file and `os.replace`s on success; `render_snippet_frame` renders a single frame. With `beat_sync` (default) `sync_to_beats` snaps start/end to the nearest librosa beat and makes the transition and fades whole beats long; the audio fade-out is a `qsin` curve plus a stepped low-pass cascade (`asendcmd` crashed ffmpeg here, so don't use it). API: `/api/snippet-sources`, `/api/snippet` (job), `/api/snippet-preview` (job, low-res ending only), `/api/snippet-frame`, `/api/snippets`; exports go to `sm_content/snippets/`. UI: the "Snippets" tab (`web/snippets.js`, separate from `app.js`).
- **`lyrics_sync_ui.py`** – FastAPI app serving `web/` (vanilla JS, no build step) plus `/api/*`. Long tasks (`/api/align`, `/api/preview`, `/api/render`) run on a 2-worker `ThreadPoolExecutor`, tracked in the in-memory `jobs` dict; the frontend polls `/api/jobs/{id}` and can POST `/cancel` (cooperative via `cancel_check`). Preview and render are two-stage: burn subtitles with ffmpeg, then optionally run `process_video_lofi` over the result. Full renders write `<song> - lyrics.part.mp4` / `.clean.part.mp4` scratch files and only `os.replace` onto `<song> - lyrics.mp4` on success, so a cancelled or failed render never destroys the previous export.
- **Lyric editing**: the line list in `web/app.js` edits text/start/end and auto-saves with `PUT /api/timings/{song}`, which runs `normalise_timings` (`lyrics_sync.py`: sort, min length, clamp overlaps, rebuild karaoke `words` — same word count keeps timings, otherwise redistributed by length) and writes the exact file preview/render read, keeping a one-time `.bak`. Preview/render read timings from disk, so the UI flushes pending saves first. Re-align overwrites edits.
- **Global offset**: `CaptionStyle.time_offset` (seconds, UI slider "Subtitle Timing") shifts every line/word in `build_ass` via `shift_timings`; the stored lyric times never change. The offset is per song (`PUT /api/song-settings/{song}` -> `.lyrics_sync/song_settings.json`; offsets an older version kept in `localStorage` are migrated on first load) and deliberately not part of presets (`_normalise_preset` and `applyStyle` drop it). In `app.js`, lyric time = `video.currentTime - lyricOffset()`.
- **Whole words**: `build_ass` calls `plan_line_fit` per line so the renderer never cuts a word mid-way: first widen that line's margins (down to `MIN_SIDE_MARGIN`), then shrink the font (down to `MIN_FIT_SCALE`), and only then split the word with a trailing `-` + `\N`. Widths come from `make_measurer` (Pillow + `fc-match`, falling back to an average-glyph estimate). `planLineFit` in `web/app.js` mirrors this for the live caption; keep the two in sync.
- **Presets** are `{style, aesthetics}` (either half may be `null`); old flat style-only preset files are still read. Built-in FX looks live in `FX_LOOK_PRESETS`. Built-in presets (`_builtin_presets`: style presets + `FX_LOOK_PRESETS`) are always present, flagged `builtin`, and can't be overwritten/renamed/deleted; only user presets are written to `.lyrics_sync/presets.json` (`POST/DELETE /api/presets/{name}`, `POST .../rename`). The UI groups them (Built-in / My presets) and confirms before overwriting.
- **`web/app.js`** – the studio UI. There is no live playback or CSS imitation of the effects: `mainVideo` is a paused source frame chosen with the "Frame" slider (so `currentTime` is song time), the caption overlay sits on it for positioning (it shows the nearest line even in a gap), and `#stageFx` lays a server-rendered result exactly over the video box: `POST /api/fx-frame` (JPEG, exact stack) or `POST /api/fx-clip` (~1 s animated WebP/GIF at 270 px wide, so audio-driven effects are visible). Both re-render on knob release / scrub when Auto-refresh is on. The rendered 10s preview plays in a modal (`#previewPanel` / `<video id="previewVideo">`, `position: fixed`). The overlay is positioned in pixels from the video's on-screen box, so it is refreshed by a `ResizeObserver` (`setupOverlayTracking`), not just `timeupdate`.
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
