# Status of the 2026-11-01 Playwright investigation

Original findings and the implemented fixes (see git history for details):

| # | Finding | Status |
|---|---------|--------|
| P0-1 | Second preview click used the preview clip's time as `start_sec` -> no captions | Fixed: preview plays in its own player, `mainVideo` stays on the source; start defaults to the first lyric; warns when the window has no lyrics |
| P0-2 | Lo-Fi preview audio + audio reactivity came from 0s of the song | Fixed (`lyrics_sync_ui.py` now analyses/muxes the cut clip's own audio; verified by cross-correlation) |
| P1-3 | 83 MB / ~90 s per 10 s Lo-Fi preview | Fixed: sequential reads, frames piped to ffmpeg (no mp4v intermediate), cheaper noise, CRF 23 + 8 Mbps cap -> ~10 MB / ~25 s |
| P1-4 | Red error text never reset, endless polling, stacked jobs | Fixed |
| P2-5 | `ruff` red with a fresh ruff; CI didn't install the deps the tests import | Fixed: rules pinned in `pyproject.toml`, CI installs `.[dev]` (CI itself not run from here) |
| P2-6 | favicon 404; preview files pile up | Fixed |
| P2-6 | Duplicate "rudio blanco"/"ruido blanco" entries | Not changed: the cause is the misspelled file `lyrics/rudio blanco.txt` outside the repo; rename it to `ruido blanco.txt` |
| P2-6 | "Empty" `\kf` tags in ASS | Not a bug: libass accumulates consecutive karaoke tags, so the lead-in gap is correct |
| P2-6 | `-copyts` in `render(... preview=...)` | Not investigated |

New features (planned in `~/.claude/plans/update-the-plan-so-crispy-lantern.md`):
- Editable lyric lines (text / start / end, add / delete, auto-save with `.bak`).
- Spec-driven FX knobs with per-effect sliders and an audio-reactivity block, a single-frame FX preview, and `{style, aesthetics}` presets with built-in FX looks.

## Follow-up round (Playwright review of the PR)

| # | Finding | Status |
|---|---------|--------|
| R-1 | Cancelling a render left `lyrics_clean.mp4` behind and could overwrite the previous export | Fixed: renders go to `*.part.mp4` scratch files, replaced atomically on success, removed in `finally` (tested) |
| R-2 | `noise_strength = 0` still produced grain (fixed base term of 10) | Fixed: base grain scales with the slider; default output unchanged (tested) |
| R-3 | Song without timings showed a stale "64 lines" | Fixed |
| R-4 | Y position label read "Grid" | Fixed: Top / Middle / Bottom |
| N-1 | No way to shift all captions in time | Added: global offset slider (per song, not part of presets) |
| N-2 | Words could be cut mid-word when the column is narrow | Fixed: widen line, then shrink font, then hyphenate (`-`) as a last resort; live preview mirrors it |
| N-3 | More configuration per effect | Added ~20 knobs: wash black crush + per-channel tint, optional Color Grade card (brightness/contrast/saturation/faded blacks), zoom constant + anchor, fog clear-centre radius + haze, chromatic vertical split + bass pulse, glitch slices/height/colour-swap, scanline thickness, grain size; two new looks ("FX · Faded Film", "FX · VHS Tape"). All defaults reproduce the previous output |
| N-4 | Wash naming | The wash is now simply "Color Wash" (field `color_wash`) everywhere. Saved presets that still contain the old toggle key fall back to the default (on) |
