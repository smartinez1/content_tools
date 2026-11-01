# content_tools

Modern media toolkit for lyrics alignment, karaoke captions burn-in, typography & subtitle styling, and video rendering.

## Features

- **Automated Lyrics Alignment**: Forced-alignment with Whisper and `stable-ts` to produce word-level karaoke timings.
- **Interactive Web Studio**:
  - Live video preview with draggable subtitle overlay.
  - Subtitle box width slider for narrow, blocky lyrics column wrapping.
  - Typography controls with **Helvetica as default**, grotesque lowercase mode, and semi-transparent opacity trailing.
  - Colors, outline stroke, drop shadow, text blur, pop scaling, letter spacing.
  - 3x3 alignment grid and absolute coordinate positioning.
  - **Lo-Fi Emo-Trap Audio-Reactive Engine**:
    - "El Cocuy" wash (crushed blacks, cold violet/slate tint, 65% warm desaturation).
    - 15fps frame-rate decimation for hand-held camcorder feel.
    - Dynamic ISO noise layer mapped to audio high-frequencies (hi-hats/breathiness).
    - Subtle radial chromatic aberration (3px R/B shift).
    - "Fogged glass" variable Gaussian blur vignette and CRT monitor scanlines.
    - 808s sidechain pumping zoom bounce and mid-range fuzzed chord glitching.
  - Real-time CSS approximation in the web player.
  - 10-second exact preview render with audio-reactive FX.
  - Full video exports: both Lo-Fi audio-reactive and clean fast versions.
  - Live progress monitoring and job cancellation.
- **CLI Utilities**:
  - `lyrics_sync.py`: Fast CLI for batch alignment, styling, and rendering.
  - `video_fx.py`: Audio-reactive video post-processing engine.
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
