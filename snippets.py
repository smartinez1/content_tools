"""Promo snippets: cut a rendered video, close it with an on-brand transition and an end card.

Timeline of a snippet (all in seconds, relative to the snippet start)::

    [0 ......................... clip][card .............................]
                     [transition T  ][intro 0.4s, reversed][hold]

The clip is ``end - start`` seconds of the source. The selected transitions run over its last
``transition_duration`` seconds (progress p 0 -> 1); the end card then plays the same transitions
in reverse (p 1 -> 0) over its first ``CARD_INTRO`` seconds so the cut reads as one move.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from video_fx import (
    apply_chromatic_aberration,
    apply_fuzzed_glitch,
    build_scanline_mask,
    extract_audio_from_video,
    find_ffmpeg,
)

TRANSITIONS = ("static", "glitch", "crt_off", "fade")  # also the order they are applied in
TRANSITION_LABELS = {
    "glitch": "VHS glitch",
    "fade": "Fade to black",
    "crt_off": "CRT power-off",
    "static": "Static wipe",
}
DEFAULT_TRANSITIONS = ("glitch",)
CARD_INTRO = 0.4
MAX_SNIPPET_SECONDS = 90.0
FADE_IN_SECONDS = 0.3
MUSIC_FADE_OUT = 1.5
LOWPASS_OPEN, LOWPASS_CLOSED = 18000.0, 350.0  # the fade-out also closes a low-pass filter


@dataclass
class SnippetConfig:
    start: float = 0.0
    end: float = 15.0
    transitions: list[str] = field(default_factory=lambda: list(DEFAULT_TRANSITIONS))
    transition_duration: float = 0.6
    fade_in: bool = True
    card_duration: float = 3.0
    release_text: str = "20.11.2026"
    music_under_card: bool = True
    beat_sync: bool = True
    seed: int = 7
    # Fade lengths; ``sync_to_beats`` rewrites them to whole beats.
    fade_in_seconds: float = FADE_IN_SECONDS
    music_fade_out: float = MUSIC_FADE_OUT

    @property
    def clip_duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def total_duration(self) -> float:
        return self.clip_duration + self.card_duration

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> SnippetConfig:
        """Coerce and clamp untrusted input; bad values fall back to the defaults."""
        cfg = cls()
        data = data if isinstance(data, dict) else {}

        def num(key, lo, hi):
            try:
                value = float(data[key])
            except (KeyError, TypeError, ValueError):
                return getattr(cfg, key)
            if not np.isfinite(value):
                return getattr(cfg, key)
            return min(hi, max(lo, value))

        cfg.start = num("start", 0.0, 24 * 3600.0)
        cfg.end = num("end", 0.0, 24 * 3600.0)
        cfg.transition_duration = num("transition_duration", 0.2, 2.0)
        cfg.card_duration = num("card_duration", 1.0, 6.0)
        if "transitions" in data:
            raw = data["transitions"] if isinstance(data["transitions"], (list, tuple, set)) else []
            chosen = {str(t) for t in raw}
            cfg.transitions = [t for t in TRANSITIONS if t in chosen]
        for key in ("fade_in", "music_under_card", "beat_sync"):
            if key in data:
                setattr(cfg, key, bool(data[key]))
        if isinstance(data.get("release_text"), str):
            cfg.release_text = data["release_text"].strip()[:40]
        try:
            cfg.seed = int(data.get("seed", cfg.seed))
        except (TypeError, ValueError):
            pass
        return cfg


# ---------------------------------------------------------------------------
# Transitions: (frame, p, rng) -> frame, p in [0, 1]; p == 0 returns the frame untouched.
# ---------------------------------------------------------------------------

def _ease(p: float) -> float:
    return p * p * (3.0 - 2.0 * p)


def transition_glitch(frame: np.ndarray, p: float, rng: np.random.Generator) -> np.ndarray:
    """VHS tear: growing RGB split, slice displacement and a burst of grain."""
    if p <= 0:
        return frame
    h, w = frame.shape[:2]
    scale = w / 1080.0
    out = apply_chromatic_aberration(
        frame, shift_px=int(round((2 + 26 * p) * scale)), vertical_px=int(round(6 * p * scale))
    )
    out = apply_fuzzed_glitch(
        out, intensity=1.0, rng=rng, threshold=0.0, probability=min(1.0, 0.35 + p),
        max_shift=int(20 + 160 * p * scale), max_slices=int(3 + 9 * p),
        slice_height=int(max(10, 70 * scale)), swap_chance=0.15 + 0.5 * p,
    )
    sigma = 40.0 * p
    noise = rng.normal(0.0, sigma, size=(h // 2 + 1, w // 2 + 1)).astype(np.float32)
    noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_NEAREST)[:, :, None]
    return np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def transition_static(frame: np.ndarray, p: float, rng: np.random.Generator) -> np.ndarray:
    """Dissolve into grey TV static with rolling scanlines."""
    if p <= 0:
        return frame
    h, w = frame.shape[:2]
    small = rng.integers(0, 256, size=(h // 3 + 1, w // 3 + 1), dtype=np.uint8)
    snow = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    snow = cv2.cvtColor(snow, cv2.COLOR_GRAY2BGR).astype(np.float32)
    snow *= build_scanline_mask(h, w, strength=0.35, spacing=4, thickness=2)
    a = _ease(p)
    return (frame.astype(np.float32) * (1.0 - a) + snow * a).astype(np.uint8)


def transition_crt_off(frame: np.ndarray, p: float, rng: np.random.Generator) -> np.ndarray:
    """Old TV switching off: squash to a bright line, then to a dot, then black."""
    if p <= 0:
        return frame
    h, w = frame.shape[:2]
    out = np.zeros_like(frame)
    if p >= 1.0:
        return out
    if p < 0.6:  # collapse vertically, brightening
        q = _ease(p / 0.6)
        nh = max(2, int(round(h * (1.0 - q) + 2 * q)))
        # brighten what is there; the flat white lift only kicks in once it is nearly a line,
        # so blacks stay black while the picture collapses
        bright = np.clip(frame.astype(np.float32) * (1.0 + 2.5 * q) + 200.0 * q ** 4, 0, 255).astype(np.uint8)
        squashed = cv2.resize(bright, (w, nh), interpolation=cv2.INTER_AREA)
        y = (h - nh) // 2
        out[y:y + nh] = squashed
    else:  # the line shrinks to a dot and fades
        q = _ease((p - 0.6) / 0.4)
        nw = max(2, int(round(w * (1.0 - q))))
        x = (w - nw) // 2
        y = h // 2 - 1
        level = int(255 * (1.0 - 0.7 * q))
        out[y:y + 3, x:x + nw] = level
    glow = cv2.GaussianBlur(out, (0, 0), sigmaX=max(2.0, w / 120.0))
    return cv2.max(out, glow)


def transition_fade(frame: np.ndarray, p: float, rng: np.random.Generator) -> np.ndarray:
    if p <= 0:
        return frame
    return (frame.astype(np.float32) * (1.0 - _ease(p))).astype(np.uint8)


TRANSITION_FUNCS = {
    "static": transition_static,
    "glitch": transition_glitch,
    "crt_off": transition_crt_off,
    "fade": transition_fade,
}


def apply_transitions(frame: np.ndarray, names, p: float, rng: np.random.Generator) -> np.ndarray:
    """Apply the selected transitions in the fixed order of ``TRANSITIONS``."""
    p = float(min(1.0, max(0.0, p)))
    chosen = set(names)
    for name in TRANSITIONS:
        if name in chosen:
            frame = TRANSITION_FUNCS[name](frame, p, rng)
    return frame


# ---------------------------------------------------------------------------
# End card
# ---------------------------------------------------------------------------

def crop_logo(img: np.ndarray, threshold: int = 70, pad_ratio: float = 0.04) -> np.ndarray:
    """Crop to the bright content (the lettering), dropping the empty dark field around it."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = (grey > threshold).astype(np.uint8)
    # ignore isolated bright specks (film grain) so they don't stretch the box
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return img
    h, w = grey.shape
    pad = int(round(pad_ratio * max(xs.max() - xs.min(), ys.max() - ys.min())))
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    return img[y0:y1, x0:x1]


def key_logo_background(img: np.ndarray) -> np.ndarray:
    """Map the logo's own dark (textured, not pure black) background to black, so the logo sits on
    the card without a visible rectangle. Levels: the background's typical brightness -> 0."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = float(np.percentile(grey, 60))  # most of the crop is background
    if bg <= 1:
        return img
    out = (img.astype(np.float32) - bg * 1.15) * (255.0 / max(1.0, 255.0 - bg * 1.15))
    return np.clip(out, 0, 255).astype(np.uint8)


def _date_font(size: int):
    from PIL import ImageFont

    from lyrics_sync import _reference_font

    font = _reference_font("Helvetica", True, False)
    if font is not None:
        try:
            return font.font_variant(size=size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_text_layer(width: int, height: int, text: str, size: int, spacing: float) -> np.ndarray:
    """White letter-spaced text centred on a black (width x height) BGR canvas."""
    from PIL import Image, ImageDraw

    font = _date_font(size)
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * max(0, len(text) - 1)
    x = (width - total) / 2.0
    bbox = draw.textbbox((0, 0), text or " ", font=font)
    y = (height - (bbox[3] - bbox[1])) / 2.0 - bbox[1]
    for ch, cw in zip(text, widths):
        draw.text((x, y), ch, fill=235, font=font)
        x += cw + spacing
    grey = np.asarray(canvas)
    return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)


def build_end_card(width: int, height: int, logo_path: Path | None, text: str) -> np.ndarray:
    """The static end card: the cropped logo with the release text under it, in the logo's
    CRT style (glow, RGB fringe, scanlines). Per-frame grain/flicker is added by ``card_frame``."""
    card = np.zeros((height, width, 3), dtype=np.uint8)
    logo_bottom = int(height * 0.5)
    text_size = int(width * 0.05)
    if logo_path is not None and Path(logo_path).exists():
        logo = cv2.imread(str(logo_path), cv2.IMREAD_COLOR)
        if logo is not None:
            logo = key_logo_background(crop_logo(logo))
            lw = int(width * 0.8)
            lh = max(1, int(round(logo.shape[0] * lw / logo.shape[1])))
            logo = cv2.resize(logo, (lw, lh), interpolation=cv2.INTER_AREA)
            x = (width - lw) // 2
            y = int(height * 0.46) - lh // 2
            card[y:y + lh, x:x + lw] = logo
            logo_bottom = y + lh
            text_size = max(12, int(lh * 0.22))
    if text:
        band_h = int(text_size * 2.2)
        layer = render_text_layer(width, band_h, text, text_size, spacing=text_size * 0.35)
        glow = cv2.GaussianBlur(layer, (0, 0), sigmaX=text_size * 0.18)
        layer = cv2.max(layer, (glow.astype(np.float32) * 0.9).astype(np.uint8))
        layer = apply_chromatic_aberration(layer, shift_px=max(1, int(round(width / 700))))
        y = logo_bottom + int(text_size * 0.6)
        y = min(y, height - band_h)
        card[y:y + band_h] = cv2.max(card[y:y + band_h], layer)
    scan = build_scanline_mask(height, width, strength=0.12, spacing=3)
    return (card.astype(np.float32) * scan).astype(np.uint8)


def card_frame(card: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The end card with light moving grain and a faint brightness flicker so it doesn't freeze."""
    h, w = card.shape[:2]
    # mean pulled down a little so clipping at 0 doesn't lift the blacks to grey
    noise = rng.normal(-2.5, 7.0, size=(h // 2 + 1, w // 2 + 1)).astype(np.float32)
    noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_NEAREST)[:, :, None]
    flicker = 1.0 + float(rng.normal(0.0, 0.025))
    return np.clip(card.astype(np.float32) * flicker + noise, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _transition_progress(t: float, cfg: SnippetConfig) -> float:
    """Progress (0..1) of the closing transition at snippet time ``t`` (clip part only)."""
    begin = cfg.clip_duration - cfg.transition_duration
    if t <= begin or not cfg.transitions:
        return 0.0
    return min(1.0, (t - begin) / cfg.transition_duration)


def compose_frame(t: float, source_frame, card: np.ndarray, cfg: SnippetConfig, rng) -> np.ndarray:
    """The output frame at snippet time ``t``; ``source_frame`` is only used inside the clip."""
    if t < cfg.clip_duration and source_frame is not None:
        frame = apply_transitions(source_frame, cfg.transitions, _transition_progress(t, cfg), rng)
        if cfg.fade_in and t < cfg.fade_in_seconds:  # picture rises from black with the audio
            frame = (frame * _ease(t / cfg.fade_in_seconds)).astype(np.uint8)
        return frame
    frame = card_frame(card, rng)
    into = t - cfg.clip_duration
    if cfg.transitions and into < CARD_INTRO:
        frame = apply_transitions(frame, cfg.transitions, 1.0 - into / CARD_INTRO, rng)
    return frame


def _probe(video: Path) -> tuple[float, int, int, float]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = (int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1) / fps
    finally:
        cap.release()
    return fps, w, h, total


def detect_beats(video: Path, t0: float, t1: float) -> np.ndarray:
    """Beat times (seconds in the source video) between about ``t0`` and ``t1``; empty if unclear."""
    try:
        stat = video.stat()
        return _detect_beats(str(video), stat.st_mtime_ns, round(t0, 1), round(t1, 1))
    except Exception:  # no audio track, librosa trouble...: just don't sync
        return np.zeros(0)


@functools.lru_cache(maxsize=16)
def _detect_beats(path: str, _mtime: int, t0: float, t1: float) -> np.ndarray:
    import librosa

    lo = max(0.0, t0 - 4.0)  # context either side makes the tempo estimate steadier
    tmp = Path(tempfile.mkdtemp(prefix="beats_"))
    try:
        wav = extract_audio_from_video(Path(path), tmp / "a.wav", lo, (t1 + 4.0) - lo)
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        if len(y) < sr * 3:
            return np.zeros(0)
        _tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
        return librosa.frames_to_time(frames, sr=sr) + lo
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sync_to_beats(video: Path, cfg: SnippetConfig, beats: np.ndarray | None = None) -> SnippetConfig:
    """A copy of ``cfg`` whose cut points and fades sit on the song's beat grid.

    The start and end snap to the nearest beat; the closing transition, the fade-in and the
    music fade-out become whole numbers of beats. Without a clear beat the config is unchanged.
    """
    if not cfg.beat_sync:
        return cfg
    if beats is None:
        beats = detect_beats(video, cfg.start, cfg.end)
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 4:
        return cfg
    period = float(np.median(np.diff(beats)))
    if not 0.25 <= period <= 1.5:  # implausible tempo (double/half-time errors etc.)
        return cfg

    def snap(x):
        return float(beats[np.argmin(np.abs(beats - x))])

    start, end = snap(cfg.start), snap(cfg.end)
    if end - start < period:  # keep at least one beat
        return cfg
    n_trans = max(1, int(round(cfg.transition_duration / period)))
    while n_trans > 1 and n_trans * period > 2.0:
        n_trans -= 1
    return replace(
        cfg,
        start=start,
        end=end,
        transition_duration=min(2.0, max(0.2, n_trans * period)),
        fade_in_seconds=period,
        music_fade_out=max(1, int(np.ceil(MUSIC_FADE_OUT / period))) * period,
    )


def _lowpass_stages(start: float, length: float, stages: int = 5) -> str:
    """Cascade of low-pass filters switched on one after another through the fade-out, so the
    sound gets duller as it fades (a plain volume ramp feels abrupt on a bassy mix)."""
    out = []
    for i in range(1, stages + 1):
        frac = i / stages
        freq = LOWPASS_OPEN * (LOWPASS_CLOSED / LOWPASS_OPEN) ** frac
        at = start + length * (i - 1) / stages
        out.append(f"lowpass=f={freq:.0f}:enable='gte(t,{at:.3f})'")
    return ",".join(out)


def _audio_filter(cfg: SnippetConfig, total: float) -> str:
    """Soft audio ending: the fade-out is curved and also closes a low-pass (the music "sinks")."""
    parts = []
    if cfg.fade_in:
        parts.append(f"afade=t=in:st=0:d={cfg.fade_in_seconds:.3f}:curve=qsin")
    if cfg.music_under_card:
        d = min(cfg.music_fade_out, total)
        st = max(0.0, total - d)
    else:
        d = cfg.transition_duration if cfg.transitions else 0.3
        st = max(0.0, cfg.clip_duration - d)
    parts.append(_lowpass_stages(st, d))
    parts.append(f"afade=t=out:st={st:.3f}:d={d:.3f}:curve=qsin")
    # keep the audio exactly as long as the video (silence under the card if the source ends)
    parts.append(f"apad=whole_dur={total:.3f}")
    return ",".join(parts)


def render_snippet(
    video: Path,
    output: Path,
    cfg: SnippetConfig,
    logo_path: Path | None = None,
    on_progress=None,
    cancel_check=None,
    window: tuple[float, float] | None = None,
    max_width: int | None = None,
    preset: str = "fast",
) -> Path:
    """Render the snippet (clip + transition + end card) to ``output``.

    ``window`` = (from, to) in snippet time renders only that part (used by the quick transition
    preview); ``max_width`` downsizes it. The output is written to a scratch file and only moved
    onto ``output`` when ffmpeg succeeds.
    """
    ffmpeg = find_ffmpeg()
    fps, src_w, src_h, src_total = _probe(video)
    if cfg.end <= cfg.start:
        raise RuntimeError("The end time must be after the start time")
    if cfg.start >= src_total:
        raise RuntimeError(f"Start {cfg.start:.2f}s is past the end of the video ({src_total:.2f}s)")
    synced = sync_to_beats(video, cfg)
    if synced.end <= src_total:
        cfg = synced

    total = cfg.total_duration
    w0, w1 = window if window else (0.0, total)
    w0, w1 = max(0.0, w0), min(total, w1)
    width, height = src_w, src_h
    if max_width and src_w > max_width:
        width = max_width - max_width % 2
        height = int(round(src_h * width / src_w))
        height -= height % 2
    card = build_end_card(width, height, logo_path, cfg.release_text)
    rng = np.random.default_rng(cfg.seed)

    n_frames = max(1, int(round((w1 - w0) * fps)))
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = output.with_name(output.stem + ".part" + output.suffix)
    tmp = Path(tempfile.mkdtemp(prefix="snippet_"))
    log_path = tmp / "ffmpeg.log"
    # The fades are defined on the whole snippet timeline; for a partial window read the audio
    # from the snippet start and trim afterwards so they land at the same moments.
    afilter = _audio_filter(cfg, total)
    if w0 > 0:
        afilter += f",atrim=start={w0:.3f},asetpts=PTS-STARTPTS"
    cmd = [
        ffmpeg, "-hide_banner", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-framerate", f"{fps}",
        "-i", "-",
        "-ss", f"{cfg.start:.3f}", "-t", f"{w1:.3f}", "-i", str(video),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-af", afilter,
        "-c:v", "libx264", "-preset", preset, "-crf", "21", "-maxrate", "10M", "-bufsize", "20M",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{w1 - w0:.3f}", "-movflags", "+faststart", "-f", "mp4",
        str(scratch),
    ]
    cap = cv2.VideoCapture(str(video))
    proc = None
    ok = False
    try:
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
            if w0 < cfg.clip_duration:
                cap.set(cv2.CAP_PROP_POS_MSEC, (cfg.start + w0) * 1000.0)
            last = None
            for i in range(n_frames):
                if cancel_check and cancel_check():
                    raise RuntimeError("Snippet render cancelled by user")
                t = w0 + i / fps
                src = None
                if t < cfg.clip_duration:
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        last = frame
                    src = last
                    if src is not None and (src.shape[1], src.shape[0]) != (width, height):
                        src = cv2.resize(src, (width, height), interpolation=cv2.INTER_AREA)
                    if src is None:  # the source ran out early: go straight to the card
                        src = np.zeros((height, width, 3), dtype=np.uint8)
                out = compose_frame(t, src, card, cfg, rng)
                try:
                    proc.stdin.write(np.ascontiguousarray(out).tobytes())
                except BrokenPipeError:
                    break
                if on_progress and (i % 10 == 0 or i == n_frames - 1):
                    pct = (i + 1) / n_frames * 95.0
                    on_progress(pct, f"Rendering snippet: frame {i + 1}/{n_frames}")
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
            proc.wait()
        if proc.returncode != 0:
            detail = log_path.read_text(errors="replace").strip()[-800:]
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {detail}")
        os.replace(scratch, output)
        ok = True
        if on_progress:
            on_progress(100.0, "Snippet ready.")
    finally:
        cap.release()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        if not ok:
            scratch.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return output


def render_snippet_frame(
    video: Path, t: float, cfg: SnippetConfig, logo_path: Path | None = None
) -> np.ndarray:
    """One output frame at snippet time ``t`` (clip, transition or end card) for quick checks."""
    cfg = sync_to_beats(video, cfg)
    _fps, w, h, _total = _probe(video)
    t = min(max(0.0, float(t)), max(0.0, cfg.total_duration - 1e-3))
    card = build_end_card(w, h, logo_path, cfg.release_text)
    src = None
    if t < cfg.clip_duration:
        cap = cv2.VideoCapture(str(video))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, (cfg.start + t) * 1000.0)
            ret, frame = cap.read()
            src = frame if ret else np.zeros((h, w, 3), dtype=np.uint8)
        finally:
            cap.release()
    return compose_frame(t, src, card, cfg, np.random.default_rng(cfg.seed))
