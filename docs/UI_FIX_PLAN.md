# Fix plan: result panels crush the video stage ("presets destroyed the page")

> **Status: all of U-1 to U-7 are fixed and verified in the browser.**
>
> | # | Fix |
> |---|---|
> | U-1 | 10 s preview is a modal; FX frame is a floating **non-modal** panel (sliders stay usable). Video container has `min-height`, stage scrolls instead of clipping |
> | U-2 | `ResizeObserver` + resize/loadedmetadata/seeked keep the caption on the video; hidden when the video has no displayed size |
> | U-3 | Lyric card is flexible (`flex: 0 1 230px`); video at 1200x762 went 143x254 -> 205x364 |
> | U-4 | `effectivePos()` / `syncPositionControls()` are the single source for caption position; presets, drag, alignment buttons and sliders all use them. Moving only X or Y now works (the renderer needs both, so the other is seeded) |
> | U-5 | Removed the 12 px font and 160 px box-width floors; preview size now tracks Size / Box Width |
> | U-6 | Style changes no longer re-render the lyric rows (they edit raw text), so caret and focus survive |
> | U-7 | Superseded FX-frame requests are aborted client-side and server renders are capped at 2. A server-side analysis cache was **tried and removed**: measured, warm analysis is only ~33 ms of a ~330 ms request, and the ~1-2 s first-frame delay is one-time librosa warm-up |
>
> Still open / untested: Whisper re-align, full (non-preview) renders, cancelling a live job, other songs
> (no source video), the GitHub CI run, non-Chromium browsers, and the first-frame warm-up (~1-2 s once per server start).

Investigated 2026-11-01 with Playwright against `python3 lyrics_sync_ui.py` and
"espacio tiempo (2am)". All findings below are **reproduced**, not inferred. Every defect is in
code added earlier in this session (the FX/preview panels, the taller lyric editor), except
U-5/U-7 which are pre-existing and were made much more visible.

## Root cause

The studio is a **fixed-height, non-scrolling layout**: `.studio-layout` is a grid with
`overflow: hidden`, and `.stage-pane` is `display: flex; flex-direction: column; overflow: hidden`
(`web/style.css`). Inside the stage pane:

| Flex item | Sizing | Can it shrink? |
|---|---|---|
| `.video-container` | `flex: 1` (⇒ `flex-basis: 0`), child `#mainVideo` is `max-height: 100%` | **yes, all the way to 0** |
| `.transport-controls` | content height | effectively no |
| `#previewPanel`, `#fxFramePanel` (`.preview-panel`) | content height; media is `max-height: 60vh` | no (`min-height: auto` on a flex item) |
| `.lines-card` | `height: 340px; flex-shrink: 0` | no |

So **whenever a result panel opens, the only item that yields is the video**. The subtitle overlay
is positioned in absolute pixels from `mainVideo.getBoundingClientRect()`, and `updateOverlay()` is
only called on `timeupdate` + control changes — never on resize — so the caption is left stranded
at coordinates computed for the old video size.

That is exactly the reported sequence: **click "Preview FX on current frame", then click through
presets** → the FX image (1080×1920 scaled to ~257×457) opens, the video collapses, and the
caption preview is left floating outside the video box. Nothing is permanently corrupted: closing
the panel restores the video, but the overlay stays wrong until the next `timeupdate`.

Measured (1200×762 viewport, paused at 26 s):

| State | `#mainVideo` size | Overlay inside the video box? |
|---|---|---|
| no panels | 143×254 | yes |
| FX panel open, image loaded | **0×0** | no (overlay at x=757, y=−74 — off-screen) |
| 10 s preview panel open | **0×0** | no |
| panel closed again | 143×254 | restored only after the next `timeupdate` |

---

## Findings, worst first

### U-1 (P0) Result panels collapse the video to 0×0 — `web/index.html`, `web/style.css`
Affects both panels I added: `#fxFramePanel` and `#previewPanel`. Empty panel → video 140 px;
with a loaded image/video → **0×0**. Severity scales with the media size.

**Fix (recommended): make both result panels overlays, not layout siblings.** They are transient
review artifacts, so they should not compete for the editing layout:
- move them out of `.stage-pane` flow into a single reusable lightbox: `position: fixed`, centred,
  dimmed backdrop, `max-height: 85vh`, close on backdrop click / `Esc` / the existing Close button;
- keep one `#resultPanel` with a `<video>` and an `<img>` child, and show whichever applies, so
  there is one layout path to maintain instead of two.

**Fix (defensive, do it as well — cheap and prevents the whole class of bug):**
- `.video-container { min-height: 200px; }` so it can never be crushed to nothing;
- `.stage-pane { overflow-y: auto; }` so added content scrolls instead of stealing height;
- `.preview-panel { flex-shrink: 0; min-height: 0; }`.

### U-2 (P0) The caption overlay never re-positions on layout change — `web/app.js`
`updateOverlay()` runs on `timeupdate` (`web/app.js` ~line 871) and from control handlers only.
There is **no `resize` listener** (verified: dispatching `resize` changed nothing) and no
observer on the video box, so any size change leaves the overlay stale. While paused it stays
wrong indefinitely.

**Fix:**
- add a `ResizeObserver` on `#mainVideo` (and `#videoContainer`) plus a `window.resize`
  listener, both debounced with `requestAnimationFrame`, calling `updateOverlay()`;
- also call it on `loadedmetadata` / `loadeddata` (currently the first paint depends on a
  `timeupdate` arriving);
- early-return guard: `updateOverlay()` currently only checks `!mainVideo.videoWidth` (the
  *intrinsic* width, still 1080 when the element is displayed 0×0). Add a displayed-size guard —
  `const vidHeight = mainVideo.clientHeight; if (!vidHeight) { subOverlay.style.display = "none"; return; }` —
  so it hides rather than computing garbage coordinates.

### U-3 (P1) Lyric editor stole 200 px of video height — `web/style.css`
I raised `.lines-card` from `height: 140px` to `340px` to fit the editor rows. Measured cost:
video height **454 px → 254 px** at this viewport. The video stage is now small enough that the
caption preview is unusable (see U-5), and `.video-container`'s `max-height: calc(100vh - 280px)`
no longer matches the real chrome height.

**Fix:** give the lyric card a flexible height instead of a fixed one — e.g.
`.lines-card { flex: 1 1 240px; min-height: 160px; max-height: 45%; }` (it already scrolls
internally via `.lines-list { overflow-y: auto }`), and re-tune or drop
`max-height: calc(100vh - 280px)` on `.video-container`. Optionally make the split draggable later;
not needed for the fix.

### U-4 (P1) Presets leave the Position controls lying — `web/app.js`
`applyStyle()` (`web/app.js` ~line 1174) syncs most controls but **not** `posX`/`posY`/their
labels, nor the `.btn-align.active` state. Reproduced: drag the caption (sliders → 770/860,
labels "774px"/"862px"), then apply any preset. `styleState.pos_x/pos_y` become `null` and the
overlay jumps back to margin-based placement, but the sliders still read 774/862 px. The panel now
shows a position **that will not be rendered**, and nudging an unrelated control can snap the
caption somewhere unexpected.

**Fix:** extract a single `syncStyleControls()` that writes *every* style control from
`styleState` (including `pos_x`/`pos_y` → slider value + "Center"/"Bottom"/px label, and the
alignment button `active` class), then call it from `applyStyle()`, from the drag handler
(`web/app.js` ~line 840), and from the reset path. One writer, no drift.

### U-5 (P2) Caption preview is pinned to the 12 px floor, so font size does nothing
`updateOverlay()` computes `Math.max(12, styleState.font_size * vidHeight / 1920)`. With the video
254 px tall, a 48 px caption scales to 6.3 px and is clamped to 12 px; even at 454 px it is 11.3 px
→ still clamped. Verified: `fontSize` stayed `"12px"` across every preset, including
"Bold Impact Pop" (56 px). So the live preview misrepresents size, weight and wrapping — i.e. the
main thing presets are supposed to show.

**Fix:** drop the clamp to something non-distorting (e.g. `Math.max(4, …)`) so relative sizes stay
faithful, and/or render the overlay at render-resolution scale inside a CSS
`transform: scale(vidHeight/1920)` wrapper so one transform handles font, stroke, shadow and
`box_width` together. Pairs with U-3 (a taller video makes the preview legible).

### U-6 (P2) Style changes re-render the line list and steal the caret mid-edit — `web/app.js`
The lowercase toggle (~line 501) and `applyStyle()` (~line 1205) call `renderLineList()`
unconditionally. Reproduced: typing in a line, then toggling Lowercase → the row node is replaced,
`document.activeElement` is no longer the input, focus is lost mid-word (the typed text survives
because `timings[i].text` was already updated).

**Fix:** the editor rows show **raw** text now (deliberately — editing a lowercased copy would
corrupt the data), so `styleState.lowercase` no longer affects them at all. Simply **remove both
`renderLineList()` calls**. If a style change ever does need a re-render, route it through the
`pendingRender` guard already used by `applyServerTimings()`, which defers while the list has focus.

### U-7 (P3) `/api/fx-frame` has no debounce or cancellation
Each call extracts audio with ffmpeg and runs a librosa STFT over a ~10 s window (~1–2 s measured).
With "Auto-refresh" on, every slider release fires one; the client only ignores stale *responses*
(`fxFrameReq`), it never aborts the request, and FastAPI runs sync endpoints on a large thread pool,
so slider spam can pile up concurrent ffmpeg processes.

**Fix:** abort the previous request with `AbortController` on the client; cache the band curves
server-side per `(video, start, duration, band params)` so repeated knob tweaks at the same
playhead skip re-analysis; consider routing it through the existing `ThreadPoolExecutor` to bound
concurrency.

---

## Not defects (checked, no change needed)
- Switching presets throws **no** JS errors, and `marginV`, font, colour, outline, shadow and
  blur controls all sync correctly.
- Style-only presets leave FX alone and FX looks leave the caption style alone, as designed.
- Saving a preset round-trips `{style, aesthetics}` and appears in the dropdown.
- Transport works: play/pause label and state, timeline scrub (50 % → 01:50 / 03:40), time readout.
- Caption drag updates `styleState.pos_x/pos_y` and both sliders.
- "Cancel" with no running job does nothing; the "Starting pipeline..." text is just the job
  card's placeholder markup, not a stray job.

## Still untested (be explicit about the gaps)
- **Re-align / Whisper** — never run (downloads a model and overwrites timings).
- **Full Lo-Fi and clean renders** end-to-end, and **cancelling** a real job mid-run.
- Any song other than "espacio tiempo (2am)" (the others have no `- Compose.mp4`).
- The GitHub CI workflow change (`pip install -e ".[dev]"`) has never run on CI.
- Only Playwright's Chromium; no Safari/Firefox check.
- Lyric edits save to the real alignment file immediately, with only a single `.bak` as undo. A
  stray test edit was persisted during this investigation (restored from a copy). Consider a
  "Revert to .bak" button before relying on it for real work.

## Suggested order
1. **U-1 + U-2 together** — this is the reported bug; neither fix is complete without the other.
2. **U-3**, then **U-5** (both are "the video stage is too small to preview with").
3. **U-4**, **U-6** — small, self-contained correctness fixes.
4. **U-7** — only matters with Auto-refresh on.

## Verification
Automated (add to `tests/`; these are the regressions worth locking down):
- a DOM test is awkward without a browser harness, so instead assert the CSS invariant in review:
  `.video-container` has a `min-height` and `.stage-pane` scrolls.

Manual, with the Playwright MCP (the exact sequence that broke it):
1. Load the studio, seek to ~26 s, confirm the caption sits inside the video box.
2. Click **Preview FX on current frame**; assert `#mainVideo` keeps a non-zero height (> 200 px)
   and the caption is still inside the video box. Repeat with the 10 s preview panel, and with both
   open at once.
3. With the FX panel open, click through **every** preset; assert no console errors and the caption
   stays inside the video box.
4. Drag the caption, apply a preset, and assert the `posX`/`posY` sliders and labels match the
   position actually used (and the alignment button highlight matches `styleState.alignment`).
5. Change Size between 24 and 72 px and assert the overlay's computed `font-size` actually changes.
6. Type in a lyric line, toggle **Lowercase**, and assert focus stays in the same input.
7. Resize the window while paused; assert the caption follows the video box.
8. `ruff check .` and `python -m unittest discover -s tests -v` stay green (48 tests today).
