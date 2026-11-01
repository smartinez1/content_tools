# content_tools

Modern media toolkit for lyrics alignment, karaoke captions burn-in, typography & subtitle styling, and video rendering.

## Features

- **Automated Lyrics Alignment**: Forced-alignment with Whisper and `stable-ts` to produce word-level karaoke timings.
- **Interactive Web Studio**:
  - Live video preview with draggable subtitle overlay.
  - Typography controls with **Helvetica as default**, plus system font detection.
  - Colors, outline stroke, drop shadow, text blur, pop scaling, letter spacing.
  - 3x3 alignment grid and absolute coordinate positioning.
  - 10-second exact `ffmpeg` libass preview render.
  - Full video render with live progress monitoring and cancellation.
- **CLI Utilities**:
  - `lyrics_sync.py`: Fast CLI for batch alignment and rendering.
  - `mov_to_mp4.py`: High-quality MOV to MP4 (H.264/AAC) conversion.

## Quickstart

### 1. Install dependencies

```bash
cd scripts
pip install -e ".[dev]"
```

*Note: Requires `ffmpeg`. If not installed via brew, `imageio-ffmpeg` is automatically used as fallback.*

### 2. Launch the Web Studio

```bash
python3 lyrics_sync_ui.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser.

If you are running the tools on a different asset folder, pass `--project-root`:

```bash
python3 lyrics_sync_ui.py --project-root /path/to/media_assets
```

### 3. CLI Usage

Auto-discover song assets in `lyrics/`, `wav_files/`, and `sm_content/`:

```bash
python3 lyrics_sync.py --song "espacio tiempo (2am)"
```

Or pass explicit video and lyrics paths:

```bash
python3 lyrics_sync.py path/to/video.mp4 path/to/lyrics.txt
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
