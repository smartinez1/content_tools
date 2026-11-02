#!/usr/bin/env python3
"""Audio-reactive Lo-Fi / Emo-Trap aesthetic video processing pipeline.

Visual Identity:
- "El Cocuy" Wash: Crushed blacks, cool violet/slate tint, heavy desaturation on warm tones.
- "Ruido Blanco": Audio-reactive ISO noise on high-frequencies, 12-15fps decimation, chromatic aberration.
- "Fogged Glass": Radial Gaussian blur condensation vignette and faint CRT scanlines.
- "Emo-Trap Bounce": Sidechain pumping zoom on 808/kicks and mid-range fuzzed chord glitches.

Every effect is tunable through ``AestheticConfig``. The defaults of the numeric knobs are the
values that used to be hard-coded, so an untouched config renders exactly as before.
``AESTHETIC_GROUPS`` / ``AESTHETIC_SPECS`` describe the knobs (label, range, step) and are served
to the web UI, which builds its controls from them.
"""

import math
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import cv2
import librosa
import numpy as np

BANDS = ("low", "mid", "high")


@dataclass
class AestheticConfig:
    enabled: bool = True

    # --- El Cocuy wash: crushed blacks, cold tint, desaturated warm tones
    el_cocuy_wash: bool = True
    wash_amount: float = 1.0        # blend original -> washed
    wash_desat: float = 1.0         # 1.0 = today's 65% warm / 30% other desaturation
    wash_tint: float = 1.0          # strength of the violet shadow tint

    # --- 808 sidechain zoom (low band)
    sidechain_pump: bool = True
    zoom_amount: float = 0.025      # max zoom at full bass (2.5%)
    zoom_curve: float = 1.6         # response exponent (higher = only the loudest hits)

    # --- Fogged glass radial blur
    fogged_glass_vignette: bool = True
    fog_amount: float = 1.0
    fog_blur: float = 9.0           # gaussian sigma
    fog_pulse: float = 0.25         # how much bass widens the fog (needs sidechain_pump)

    # --- Chromatic aberration
    chromatic_aberration: bool = True
    ca_shift_px: int = 3

    # --- Fuzzed glitch (mid band)
    fuzzed_glitch: bool = True
    glitch_threshold: float = 0.45  # mid-band level needed to glitch
    glitch_probability: float = 0.4 # chance per frame once above the threshold
    glitch_max_shift: int = 18      # max horizontal slice shift (px)

    # --- CRT scanlines
    crt_scanlines: bool = True
    scanline_strength: float = 0.09
    scanline_spacing: int = 3

    # --- Dynamic ISO noise (high band)
    iso_noise: bool = True
    noise_strength: float = 0.35    # base level
    noise_audio: float = 0.7        # extra noise driven by the high band
    noise_gain: float = 1.0         # overall multiplier (0 = silent)

    # --- Frame rate decimation
    framerate_decimate: bool = True
    target_fps: int = 15

    # --- Audio reactivity: per band normalisation (percentiles), gain and smoothing
    low_gain: float = 1.0
    low_floor: float = 10.0
    low_ceiling: float = 95.0
    low_attack: float = 0.0
    low_release: float = 0.0
    mid_gain: float = 1.0
    mid_floor: float = 10.0
    mid_ceiling: float = 95.0
    mid_attack: float = 0.0
    mid_release: float = 0.0
    high_gain: float = 1.0
    high_floor: float = 10.0
    high_ceiling: float = 95.0
    high_attack: float = 0.0
    high_release: float = 0.0

    seed: int = -1                  # >= 0 makes glitch/noise reproducible

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AestheticConfig":
        """Build a config from untrusted input: unknown keys are ignored, values are
        coerced to the field type and clamped to the range declared in AESTHETIC_SPECS."""
        kwargs = {}
        for f in fields(cls):
            if not isinstance(data, dict) or f.name not in data:
                continue
            value = data[f.name]
            if isinstance(f.default, bool):
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "yes", "on")
                kwargs[f.name] = bool(value)
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(num):
                continue
            lo, hi = _RANGES[f.name]
            num = min(max(num, lo), hi)
            kwargs[f.name] = int(round(num)) if isinstance(f.default, int) else num
        # percentile pairs must stay ordered
        for band in BANDS:
            floor_key, ceil_key = f"{band}_floor", f"{band}_ceiling"
            floor = kwargs.get(floor_key, getattr(cls, floor_key))
            ceiling = kwargs.get(ceil_key, getattr(cls, ceil_key))
            if ceiling <= floor + 1.0:
                kwargs[floor_key] = max(0.0, ceiling - 1.0)
        return cls(**kwargs)


# (min, max, step) for every numeric field, plus UI metadata.
# group ids refer to AESTHETIC_GROUPS; ui=False keeps a field API-only.
def _spec(group, label, lo, hi, step, ui=True, hint=""):
    return {"group": group, "label": label, "min": lo, "max": hi, "step": step, "ui": ui, "hint": hint}


AESTHETIC_SPECS: dict[str, dict] = {
    "wash_amount": _spec("wash", "Amount", 0, 1, 0.01, hint="Blend between the original and the washed look"),
    "wash_desat": _spec("wash", "Desaturation", 0, 1.5, 0.01, hint="1.0 = original strength"),
    "wash_tint": _spec("wash", "Violet tint", 0, 2, 0.05),
    "zoom_amount": _spec("zoom", "Max zoom", 0, 0.1, 0.005, hint="Zoom at full bass level"),
    "zoom_curve": _spec("zoom", "Response curve", 0.5, 4, 0.1, hint="Higher = only the loudest hits bounce"),
    "fog_amount": _spec("fog", "Amount", 0, 1.5, 0.05),
    "fog_blur": _spec("fog", "Blur", 1, 30, 1),
    "fog_pulse": _spec("fog", "Bass pulse", 0, 1, 0.05, hint="Needs the sidechain zoom effect enabled"),
    "ca_shift_px": _spec("chroma", "Shift (px)", 0, 12, 1),
    "glitch_threshold": _spec("glitch", "Threshold", 0, 1, 0.01, hint="Mid-band level needed to glitch"),
    "glitch_probability": _spec("glitch", "Probability", 0, 1, 0.05),
    "glitch_max_shift": _spec("glitch", "Max shift (px)", 0, 80, 1),
    "scanline_strength": _spec("scanlines", "Strength", 0, 0.5, 0.01),
    "scanline_spacing": _spec("scanlines", "Spacing (rows)", 2, 12, 1),
    "noise_strength": _spec("noise", "Base level", 0, 1, 0.01),
    "noise_audio": _spec("noise", "Audio reactivity", 0, 2, 0.05, hint="Extra grain driven by hi-hats"),
    "noise_gain": _spec("noise", "Overall gain", 0, 2, 0.05),
    "target_fps": _spec("fps", "Frame rate", 6, 30, 1),
    "seed": _spec("fps", "Seed", -1, 2**31 - 1, 1, ui=False),
}
for _band in BANDS:
    _name = _band.capitalize()
    AESTHETIC_SPECS[f"{_band}_gain"] = _spec("audio", f"{_name} gain", 0, 3, 0.05)
    AESTHETIC_SPECS[f"{_band}_floor"] = _spec("audio", f"{_name} floor (percentile)", 0, 50, 1,
                                              hint="Levels below this count as silence")
    AESTHETIC_SPECS[f"{_band}_ceiling"] = _spec("audio", f"{_name} ceiling (percentile)", 50, 100, 1,
                                                hint="Levels above this count as full scale")
    AESTHETIC_SPECS[f"{_band}_attack"] = _spec("audio", f"{_name} attack (ms)", 0, 500, 5,
                                               hint="Smoothing when the level rises")
    AESTHETIC_SPECS[f"{_band}_release"] = _spec("audio", f"{_name} release (ms)", 0, 1000, 10,
                                                hint="Smoothing when the level falls")

_RANGES = {k: (v["min"], v["max"]) for k, v in AESTHETIC_SPECS.items()}

AESTHETIC_GROUPS = [
    {"id": "wash", "label": '"El Cocuy" Wash', "toggle": "el_cocuy_wash",
     "hint": "Crushed blacks, violet shadows, desaturated warm tones"},
    {"id": "zoom", "label": "808 Sidechain Zoom", "toggle": "sidechain_pump",
     "hint": "Bass-driven zoom bounce (low band)"},
    {"id": "fog", "label": "Fogged Glass", "toggle": "fogged_glass_vignette",
     "hint": "Radial condensation blur on the edges"},
    {"id": "chroma", "label": "Chromatic Aberration", "toggle": "chromatic_aberration",
     "hint": "Red/blue channel split"},
    {"id": "glitch", "label": "Fuzzed Chord Glitch", "toggle": "fuzzed_glitch",
     "hint": "Mid-band horizontal slice displacement"},
    {"id": "scanlines", "label": "CRT Scanlines", "toggle": "crt_scanlines",
     "hint": "Faint horizontal monitor lines"},
    {"id": "noise", "label": "ISO Noise", "toggle": "iso_noise",
     "hint": "Film grain that follows the hi-hats (high band)"},
    {"id": "fps", "label": "Frame Rate", "toggle": "framerate_decimate",
     "hint": "Choppy camcorder stutter"},
    {"id": "audio", "label": "Audio Reactivity", "toggle": None,
     "hint": "How the low / mid / high bands are normalised and smoothed"},
]


def aesthetic_schema() -> dict:
    """Everything the UI needs to build its controls (served by /api/aesthetics)."""
    defaults = AestheticConfig().to_dict()
    return {
        "defaults": defaults,
        "groups": AESTHETIC_GROUPS,
        "fields": {
            name: {**spec, "default": defaults[name], "kind": "int" if isinstance(defaults[name], int) else "float"}
            for name, spec in AESTHETIC_SPECS.items()
        },
    }


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg not found")


def extract_audio_from_video(
    video_path: Path,
    output_wav: Path,
    start_sec: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Extract audio (optionally only a window) to a 22.05kHz mono WAV for librosa analysis."""
    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-y", "-v", "error"]
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    cmd += ["-i", str(video_path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "22050", str(output_wav)]
    subprocess.run(cmd, check=True)
    return output_wav


def analyze_audio_bands(
    audio_path: Path,
    total_frames: int,
    duration: float,
    start_sec: float = 0.0,
    percentiles: dict[str, tuple[float, float]] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Analyze audio frequencies using librosa STFT and return normalized frame-aligned envelopes:
    - low_env (20 - 180 Hz: 808s and kick drum)
    - mid_env (350 - 2800 Hz: guitars, synths, chords)
    - high_env (5000 - 11000 Hz: hi-hats, breathiness, noise)

    ``percentiles`` maps a band name to the (floor, ceiling) percentiles used to normalise it to
    [0, 1]; the default (10, 95) is the original behaviour.
    """
    pct = {band: (10.0, 95.0) for band in BANDS}
    pct.update(percentiles or {})
    sr = 22050
    # Load corresponding duration
    y, _ = librosa.load(str(audio_path), sr=sr, offset=start_sec, duration=duration, mono=True)
    if len(y) == 0:
        zeros = np.zeros(total_frames, dtype=np.float32)
        return zeros, zeros, zeros

    hop_length = 512
    n_fft = 2048
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    # Frequency masks
    low_mask = (freqs >= 20) & (freqs <= 180)
    mid_mask = (freqs >= 350) & (freqs <= 2800)
    high_mask = (freqs >= 5000) & (freqs <= 11000)

    # Energy calculations
    low_energy = np.mean(S[low_mask, :], axis=0) if np.any(low_mask) else np.zeros(S.shape[1])
    mid_energy = np.mean(S[mid_mask, :], axis=0) if np.any(mid_mask) else np.zeros(S.shape[1])
    high_energy = np.mean(S[high_mask, :], axis=0) if np.any(high_mask) else np.zeros(S.shape[1])

    def norm_env(arr: np.ndarray, floor_pct: float, ceil_pct: float) -> np.ndarray:
        if len(arr) == 0 or np.max(arr) == 0:
            return np.zeros_like(arr, dtype=np.float32)
        p_lo, p_hi = np.percentile(arr, floor_pct), np.percentile(arr, ceil_pct)
        if p_hi <= p_lo:
            return np.zeros_like(arr, dtype=np.float32)
        res = np.clip((arr - p_lo) / (p_hi - p_lo), 0.0, 1.0)
        return res.astype(np.float32)

    low_norm = norm_env(low_energy, *pct["low"])
    mid_norm = norm_env(mid_energy, *pct["mid"])
    high_norm = norm_env(high_energy, *pct["high"])

    # Resample to match exact video frame count
    frame_times = np.linspace(0, duration, total_frames, endpoint=False)
    audio_times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=hop_length)

    low_curve = np.interp(frame_times, audio_times, low_norm)
    mid_curve = np.interp(frame_times, audio_times, mid_norm)
    high_curve = np.interp(frame_times, audio_times, high_norm)

    return low_curve, mid_curve, high_curve


def shape_envelope(
    env: np.ndarray,
    gain: float = 1.0,
    attack_ms: float = 0.0,
    release_ms: float = 0.0,
    fps: float = 15.0,
) -> np.ndarray:
    """Apply gain and attack/release smoothing to a 0..1 envelope sampled at ``fps``.

    With the defaults (gain 1, no smoothing) the envelope is returned unchanged.
    """
    out = np.clip(np.asarray(env, dtype=np.float32) * gain, 0.0, 1.0)
    if attack_ms <= 0 and release_ms <= 0:
        return out
    attack = math.exp(-1000.0 / (attack_ms * fps)) if attack_ms > 0 else 0.0
    release = math.exp(-1000.0 / (release_ms * fps)) if release_ms > 0 else 0.0
    smoothed = np.empty_like(out)
    prev = 0.0
    for i, x in enumerate(out):
        coef = attack if x > prev else release
        prev = coef * prev + (1.0 - coef) * float(x)
        smoothed[i] = prev
    return smoothed


def audio_curves(
    audio_file: Path,
    start_sec: float,
    duration: float,
    total_frames: int,
    cfg: AestheticConfig,
    fps: float,
    workdir: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Band envelopes for [start_sec, start_sec + duration] of ``audio_file``, shaped by ``cfg``.

    A source without an audio track yields flat (zero) envelopes instead of failing.
    """
    wav = workdir / "audio.wav"
    try:
        extract_audio_from_video(audio_file, wav, start_sec=start_sec, duration=duration)
        raw = analyze_audio_bands(
            wav,
            total_frames=total_frames,
            duration=duration,
            percentiles={b: (getattr(cfg, f"{b}_floor"), getattr(cfg, f"{b}_ceiling")) for b in BANDS},
        )
    except subprocess.CalledProcessError:
        zeros = np.zeros(total_frames, dtype=np.float32)
        raw = (zeros, zeros, zeros)
    return tuple(
        shape_envelope(
            curve,
            gain=getattr(cfg, f"{band}_gain"),
            attack_ms=getattr(cfg, f"{band}_attack"),
            release_ms=getattr(cfg, f"{band}_release"),
            fps=fps,
        )
        for band, curve in zip(BANDS, raw)
    )


def build_radial_mask(height: int, width: int) -> np.ndarray:
    """Precompute a smooth radial vignette mask where center is 0 (clear) and edges are 1 (fogged)."""
    y, x = np.ogrid[:height, :width]
    center_y, center_x = height / 2.0, width / 2.0
    # Normalized radial distance [0, 1]
    norm_dist = np.sqrt(((x - center_x) / center_x) ** 2 + ((y - center_y) / center_y) ** 2)
    # Threshold at 0.35, ramping up to 1.0 at outer edge
    mask = np.clip((norm_dist - 0.35) / 0.65, 0.0, 1.0)
    # Smooth cosine curve
    mask = 0.5 - 0.5 * np.cos(mask * np.pi)
    return mask.astype(np.float32)[:, :, np.newaxis]


def build_scanline_mask(height: int, width: int, strength: float = 0.09, spacing: int = 3) -> np.ndarray:
    """Precompute subtle CRT monitor horizontal scanlines (every ``spacing``th row darkened)."""
    scanlines = np.ones((height, 1, 1), dtype=np.float32)
    scanlines[::max(1, int(spacing))] = 1.0 - strength
    return scanlines


def apply_el_cocuy_wash(
    frame: np.ndarray,
    amount: float = 1.0,
    desat: float = 1.0,
    tint: float = 1.0,
) -> np.ndarray:
    """Crushed blacks, cold violet/slate shadows, and desaturation of warm colors.

    ``amount`` blends between the original and the washed frame; ``desat`` and ``tint`` scale the
    desaturation (1.0 = 65% on warm hues, 30% elsewhere) and the violet shadow tint.
    """
    if amount <= 0:
        return frame
    # 1. Crushed blacks via power curve
    f = frame.astype(np.float32) / 255.0
    f = np.clip(np.power(f, 1.22) * 1.05 - 0.04, 0.0, 1.0)

    # 2. Desaturation of warm channels in HSV space
    hsv = cv2.cvtColor((f * 255.0).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
    h, s = hsv[:, :, 0], hsv[:, :, 1]

    if desat == 1.0:
        warm_keep, other_keep = 0.35, 0.70
    else:
        warm_keep = float(np.clip(1.0 - 0.65 * desat, 0.0, 1.0))
        other_keep = float(np.clip(1.0 - 0.30 * desat, 0.0, 1.0))

    # Warm hues in OpenCV HSV (0-180): Reds (0-18 and 165-180), Yellows (18-38), Greens (38-85)
    warm_mask = (h < 38) | (h > 165)
    s[warm_mask] = s[warm_mask] * warm_keep
    # Slight global desaturation on remaining
    s[~warm_mask] = s[~warm_mask] * other_keep

    hsv[:, :, 1] = np.clip(s, 0, 255)
    washed = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)

    # 3. Tint shadows and midtones toward cold violet/indigo/slate (BGR: add Blue + Red, lower Green)
    lum = (0.114 * washed[:, :, 0] + 0.587 * washed[:, :, 1] + 0.299 * washed[:, :, 2]) / 255.0
    shadow_factor = np.clip(1.0 - lum, 0.0, 1.0)[:, :, np.newaxis]

    # Violet/slate tint vector: [Blue=+16, Green=-6, Red=+10]
    tint_vec = np.array([16.0, -6.0, 10.0], dtype=np.float32) * tint
    washed = np.clip(washed + tint_vec * shadow_factor, 0, 255).astype(np.uint8)

    if amount >= 1.0:
        return washed
    blended = frame.astype(np.float32) * (1.0 - amount) + washed.astype(np.float32) * amount
    return np.clip(blended, 0, 255).astype(np.uint8)


def apply_chromatic_aberration(frame: np.ndarray, shift_px: int = 3) -> np.ndarray:
    """Shift Red channel left and Blue channel right for cheap lens distortion."""
    shift_px = int(shift_px)
    if shift_px <= 0:
        return frame
    b, g, r = cv2.split(frame)

    # Shift Blue right
    b_shifted = np.zeros_like(b)
    b_shifted[:, shift_px:] = b[:, :-shift_px]
    b_shifted[:, :shift_px] = b[:, :1]

    # Shift Red left
    r_shifted = np.zeros_like(r)
    r_shifted[:, :-shift_px] = r[:, shift_px:]
    r_shifted[:, -shift_px:] = r[:, -1:]

    return cv2.merge([b_shifted, g, r_shifted])


def apply_fuzzed_glitch(
    frame: np.ndarray,
    intensity: float,
    rng: np.random.Generator | None = None,
    threshold: float = 0.45,
    probability: float = 0.4,
    max_shift: int = 18,
) -> np.ndarray:
    """Horizontal pixel displacement slices representing breakcore distortion."""
    h, w = frame.shape[:2]
    if h < 40 or max_shift <= 0 or intensity < threshold:
        return frame
    rng = rng if rng is not None else np.random.default_rng()
    if rng.random() > probability:
        return frame

    res = frame.copy()
    num_slices = int(rng.integers(2, 6))

    for _ in range(num_slices):
        y1 = int(rng.integers(0, h - 30))
        y2 = min(h, y1 + int(rng.integers(8, 28)))
        shift = int(int(rng.integers(-max_shift, max_shift + 1)) * intensity)
        if shift != 0:
            slice_data = res[y1:y2, :]
            res[y1:y2, :] = np.roll(slice_data, shift, axis=1)
            # Occasional color channel inversion on glitch slice
            if rng.random() < 0.2:
                res[y1:y2, :, 0], res[y1:y2, :, 2] = res[y1:y2, :, 2], res[y1:y2, :, 0]

    return res


def apply_sidechain_zoom(frame: np.ndarray, scale: float) -> np.ndarray:
    """Zoom in frame slightly around the center to simulate pumping."""
    if scale <= 1.002:
        return frame
    h, w = frame.shape[:2]
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    x1 = (new_w - w) // 2
    y1 = (new_h - h) // 2
    return resized[y1:y1 + h, x1:x1 + w]


_NOISE_POOL = 4
_NOISE_PAD = 64


@dataclass
class FxContext:
    """Per-resolution state shared by every frame of a render (masks, noise tiles, RNG)."""

    cfg: AestheticConfig
    height: int
    width: int
    rng: np.random.Generator
    radial: np.ndarray | None
    scanlines: np.ndarray | None
    blur_ksize: int
    noise_pool: np.ndarray | None


def build_fx_context(
    cfg: AestheticConfig,
    height: int,
    width: int,
    rng: np.random.Generator | None = None,
) -> FxContext:
    """Precompute everything that does not change from frame to frame."""
    if rng is None:
        rng = np.random.default_rng(cfg.seed if cfg.seed >= 0 else None)
    active = cfg.enabled
    radial = build_radial_mask(height, width) * cfg.fog_amount if (
        active and cfg.fogged_glass_vignette and cfg.fog_amount > 0
    ) else None
    scanlines = build_scanline_mask(height, width, cfg.scanline_strength, cfg.scanline_spacing) if (
        active and cfg.crt_scanlines and cfg.scanline_strength > 0
    ) else None
    ksize = max(3, int(round(3 * cfg.fog_blur)) | 1)
    # A few unit-variance noise tiles, larger than the frame; each frame crops a random window
    # (a view, no copy) and scales it. Much cheaper than drawing h*w normals per frame.
    noise_pool = rng.standard_normal(
        (_NOISE_POOL, height + _NOISE_PAD, width + _NOISE_PAD), dtype=np.float32
    ) if (active and cfg.iso_noise and cfg.noise_gain > 0) else None
    return FxContext(cfg, height, width, rng, radial, scanlines, ksize, noise_pool)


def apply_fx_frame(
    frame: np.ndarray,
    ctx: FxContext,
    low: float = 0.0,
    mid: float = 0.0,
    high: float = 0.0,
) -> np.ndarray:
    """Run the whole effect stack on one BGR frame given the band levels (0..1) for that frame."""
    cfg = ctx.cfg
    if not cfg.enabled:
        return frame
    low, mid, high = float(low), float(mid), float(high)

    # 1. Audio-reactive sidechain zoom
    if cfg.sidechain_pump and cfg.zoom_amount > 0:
        frame = apply_sidechain_zoom(frame, 1.0 + (low ** cfg.zoom_curve) * cfg.zoom_amount)

    # 2. El Cocuy wash (crushed blacks & cold tint)
    if cfg.el_cocuy_wash and cfg.wash_amount > 0:
        frame = apply_el_cocuy_wash(frame, cfg.wash_amount, cfg.wash_desat, cfg.wash_tint)

    # 3. Fogged glass (radial condensation blur)
    if ctx.radial is not None:
        blurred = cv2.GaussianBlur(frame, (ctx.blur_ksize, ctx.blur_ksize), sigmaX=cfg.fog_blur)
        pulse = low * cfg.fog_pulse if cfg.sidechain_pump else 0.0
        mask = np.clip(ctx.radial * (1.0 + pulse), 0.0, 1.0)
        frame = (frame.astype(np.float32) * (1.0 - mask) + blurred.astype(np.float32) * mask).astype(np.uint8)

    # 4. Chromatic aberration
    if cfg.chromatic_aberration and cfg.ca_shift_px > 0:
        frame = apply_chromatic_aberration(frame, shift_px=cfg.ca_shift_px)

    # 5. Fuzzed chord glitch
    if cfg.fuzzed_glitch:
        frame = apply_fuzzed_glitch(
            frame, mid, ctx.rng, cfg.glitch_threshold, cfg.glitch_probability, cfg.glitch_max_shift
        )

    # 6. CRT scanlines
    if ctx.scanlines is not None:
        frame = (frame.astype(np.float32) * ctx.scanlines).astype(np.uint8)

    # 7. Dynamic ISO noise
    if ctx.noise_pool is not None:
        sigma = cfg.noise_gain * (10.0 + 26.0 * (cfg.noise_strength + high * cfg.noise_audio))
        k = int(ctx.rng.integers(0, _NOISE_POOL))
        oy = int(ctx.rng.integers(0, _NOISE_PAD + 1))
        ox = int(ctx.rng.integers(0, _NOISE_PAD + 1))
        tile = ctx.noise_pool[k, oy:oy + ctx.height, ox:ox + ctx.width, np.newaxis]
        frame = np.clip(frame.astype(np.float32) + tile * sigma, 0, 255).astype(np.uint8)

    return frame


def process_video_lofi(
    input_video: Path,
    output_video: Path,
    audio_source: Path | None = None,
    config: AestheticConfig | None = None,
    preview_range: tuple[float, float] | None = None,
    on_progress=None,
    cancel_check=None,
) -> Path:
    """Run the complete Lo-Fi Emo-Trap aesthetic video pipeline.

    Frames are read sequentially, processed, and piped straight into ffmpeg (libx264) together
    with the audio, so there is no lossy intermediate file.
    """
    cfg = config or AestheticConfig()
    ffmpeg = find_ffmpeg()

    cap = cv2.VideoCapture(str(input_video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {input_video}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_duration = src_total_frames / src_fps

    start_sec = 0.0
    duration_sec = total_duration
    if preview_range:
        start_sec, duration_sec = preview_range
        duration_sec = min(duration_sec, total_duration - start_sec)

    out_fps = cfg.target_fps if cfg.framerate_decimate else src_fps
    out_total_frames = max(1, int(duration_sec * out_fps))
    frame_step = src_fps / out_fps
    start_frame = int(round(start_sec * src_fps))

    temp_dir = Path(tempfile.mkdtemp(prefix="lofi_fx_"))
    proc = None
    ok = False
    try:
        audio_file = audio_source if (audio_source and audio_source.exists()) else input_video
        low_curve, mid_curve, high_curve = audio_curves(
            audio_file, start_sec, duration_sec, out_total_frames, cfg, out_fps, temp_dir
        )
        ctx = build_fx_context(cfg, height, width)

        output_video.parent.mkdir(parents=True, exist_ok=True)
        stderr_path = temp_dir / "ffmpeg.log"
        cmd = [
            ffmpeg, "-hide_banner", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-framerate", f"{out_fps}", "-i", "-",
            "-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}", "-i", str(audio_file),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            # The grain is incompressible; cap the bitrate so files stay a sensible size.
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart",
            str(output_video),
        ]
        with open(stderr_path, "wb") as stderr_file:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=stderr_file)

            # Sequential read: seek once, then decode forward, skipping frames to decimate.
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            cursor = start_frame
            frame = None
            for i in range(out_total_frames):
                if cancel_check and cancel_check():
                    raise RuntimeError("Process cancelled by user")

                target = int(round(start_frame + i * frame_step))
                while cursor < target and cap.grab():
                    cursor += 1
                if target >= cursor or frame is None:
                    ret, new_frame = cap.read()
                    if not ret or new_frame is None:
                        break
                    cursor += 1
                    frame = new_frame
                # else: output rate above the source rate -> repeat the previous frame

                fx = apply_fx_frame(
                    frame, ctx,
                    low_curve[i] if i < len(low_curve) else 0.0,
                    mid_curve[i] if i < len(mid_curve) else 0.0,
                    high_curve[i] if i < len(high_curve) else 0.0,
                )
                try:
                    proc.stdin.write(fx.tobytes())
                except BrokenPipeError:
                    break  # ffmpeg exited early; its error is reported below

                if on_progress and (i % 5 == 0 or i == out_total_frames - 1):
                    pct = ((i + 1) / out_total_frames) * 100
                    on_progress(pct, f"Applying Lo-Fi Emo-Trap FX: frame {i+1}/{out_total_frames} ({pct:.0f}%)")

            if on_progress:
                on_progress(96.0, "Encoding video & muxing audio...")
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
            proc.wait()

        if proc.returncode != 0:
            detail = stderr_path.read_text(errors="replace").strip()[-800:]
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {detail}")
        ok = True
        if on_progress:
            on_progress(100.0, "Lo-Fi render complete!")
    finally:
        cap.release()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if not ok:
            output_video.unlink(missing_ok=True)
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_video


def render_fx_frame(
    video: Path,
    time_sec: float,
    config: AestheticConfig | None = None,
    window: float = 10.0,
) -> np.ndarray:
    """Return one source frame with the effect stack applied, for quick tuning.

    The audio envelopes come from a ``window``-second slice centred on ``time_sec`` (the same kind
    of analysis a 10 s preview does). Glitch/noise use a fixed seed unless the config has one, so
    changing a knob changes the picture predictably.
    """
    cfg = config or AestheticConfig()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video}")
    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = (int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1) / src_fps
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        time_sec = min(max(float(time_sec), 0.0), max(total - 1.0 / src_fps, 0.0))
        cap.set(cv2.CAP_PROP_POS_MSEC, time_sec * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"Cannot read a frame at {time_sec:.2f}s")
    finally:
        cap.release()

    out_fps = cfg.target_fps if cfg.framerate_decimate else src_fps
    start = max(0.0, time_sec - window / 2.0)
    duration = max(min(window, total - start), 1.0 / out_fps)
    n_frames = max(1, int(duration * out_fps))
    with tempfile.TemporaryDirectory(prefix="lofi_frame_") as tmp:
        low, mid, high = audio_curves(video, start, duration, n_frames, cfg, out_fps, Path(tmp))
    idx = min(n_frames - 1, max(0, int(round((time_sec - start) * out_fps))))

    rng = np.random.default_rng(cfg.seed if cfg.seed >= 0 else 0)
    ctx = build_fx_context(cfg, height, width, rng)
    return apply_fx_frame(frame, ctx, low[idx], mid[idx], high[idx])
