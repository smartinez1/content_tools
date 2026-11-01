#!/usr/bin/env python3
"""Audio-reactive Lo-Fi / Emo-Trap aesthetic video processing pipeline.

Visual Identity:
- "El Cocuy" Wash: Crushed blacks, cool violet/slate tint, heavy desaturation on warm tones.
- "Ruido Blanco": Audio-reactive ISO noise on high-frequencies, 12-15fps decimation, chromatic aberration.
- "Fogged Glass": Radial Gaussian blur condensation vignette and faint CRT scanlines.
- "Emo-Trap Bounce": Sidechain pumping zoom on 808/kicks and mid-range fuzzed chord glitches.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import cv2
import librosa
import numpy as np


@dataclass
class AestheticConfig:
    enabled: bool = True
    el_cocuy_wash: bool = True           # Crushed blacks & cold violet/slate tint
    framerate_decimate: bool = True      # Choppy 15 fps camcorder feel
    target_fps: int = 15                 # Frame rate (12 to 15)
    iso_noise: bool = True               # Dynamic noise mapped to audio highs
    noise_strength: float = 0.35         # Base noise level
    chromatic_aberration: bool = True   # Radial R/B channel shift (2-4px)
    fogged_glass_vignette: bool = True   # Radial condensation blur on edges
    crt_scanlines: bool = True          # Faint glowing CRT horizontal scanlines
    sidechain_pump: bool = True          # 808s envelope zoom bounce
    fuzzed_glitch: bool = True           # Mid-range frequency horizontal glitch

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AestheticConfig":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg not found")


def extract_audio_from_video(video_path: Path, output_wav: Path) -> Path:
    """Extract audio from video to a 22.05kHz mono WAV for fast librosa analysis."""
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-hide_banner", "-y", "-v", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "22050",
        str(output_wav),
    ]
    subprocess.run(cmd, check=True)
    return output_wav


def analyze_audio_bands(
    audio_path: Path,
    total_frames: int,
    duration: float,
    start_sec: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Analyze audio frequencies using librosa STFT and return normalized frame-aligned envelopes:
    - low_env (20 - 180 Hz: 808s and kick drum)
    - mid_env (350 - 2800 Hz: guitars, synths, chords)
    - high_env (5000 - 11000 Hz: hi-hats, breathiness, noise)
    """
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

    def norm_env(arr: np.ndarray) -> np.ndarray:
        if len(arr) == 0 or np.max(arr) == 0:
            return np.zeros_like(arr, dtype=np.float32)
        p10, p95 = np.percentile(arr, 10), np.percentile(arr, 95)
        if p95 <= p10:
            return np.zeros_like(arr, dtype=np.float32)
        res = np.clip((arr - p10) / (p95 - p10), 0.0, 1.0)
        return res.astype(np.float32)

    low_norm = norm_env(low_energy)
    mid_norm = norm_env(mid_energy)
    high_norm = norm_env(high_energy)

    # Resample to match exact video frame count
    frame_times = np.linspace(0, duration, total_frames, endpoint=False)
    audio_times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=hop_length)

    low_curve = np.interp(frame_times, audio_times, low_norm)
    mid_curve = np.interp(frame_times, audio_times, mid_norm)
    high_curve = np.interp(frame_times, audio_times, high_norm)

    return low_curve, mid_curve, high_curve


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


def build_scanline_mask(height: int, width: int) -> np.ndarray:
    """Precompute subtle CRT monitor horizontal scanlines."""
    scanlines = np.ones((height, 1, 1), dtype=np.float32)
    scanlines[::3] = 0.91  # Drop every 3rd row by 9%
    return scanlines


def apply_el_cocuy_wash(frame: np.ndarray) -> np.ndarray:
    """Crushed blacks, cold violet/slate shadows, and 65% desaturation on warm colors."""
    # 1. Crushed blacks via power curve
    f = frame.astype(np.float32) / 255.0
    f = np.clip(np.power(f, 1.22) * 1.05 - 0.04, 0.0, 1.0)

    # 2. Desaturation of warm channels in HSV space
    hsv = cv2.cvtColor((f * 255.0).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # Warm hues in OpenCV HSV (0-180): Reds (0-18 and 165-180), Yellows (18-38), Greens (38-85)
    warm_mask = (h < 38) | (h > 165)
    s[warm_mask] = s[warm_mask] * 0.35  # Desaturate by 65%
    # Slight global desaturation on remaining
    s[~warm_mask] = s[~warm_mask] * 0.70

    hsv[:, :, 1] = np.clip(s, 0, 255)
    washed = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)

    # 3. Tint shadows and midtones toward cold violet/indigo/slate (BGR: add Blue + Red, lower Green)
    lum = (0.114 * washed[:, :, 0] + 0.587 * washed[:, :, 1] + 0.299 * washed[:, :, 2]) / 255.0
    shadow_factor = np.clip(1.0 - lum, 0.0, 1.0)[:, :, np.newaxis]

    # Violet/slate tint vector: [Blue=+16, Green=-6, Red=+10]
    tint = np.array([16.0, -6.0, 10.0], dtype=np.float32)
    washed = np.clip(washed + tint * shadow_factor, 0, 255).astype(np.uint8)

    return washed


def apply_chromatic_aberration(frame: np.ndarray, shift_px: int = 3) -> np.ndarray:
    """Shift Red channel left and Blue channel right for cheap lens distortion."""
    h, w = frame.shape[:2]
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


def apply_fuzzed_glitch(frame: np.ndarray, intensity: float) -> np.ndarray:
    """Horizontal pixel displacement slices representing breakcore distortion."""
    if intensity < 0.45 or np.random.random() > 0.4:
        return frame

    res = frame.copy()
    h, w = frame.shape[:2]
    num_slices = np.random.randint(2, 6)

    for _ in range(num_slices):
        y1 = np.random.randint(0, h - 30)
        y2 = min(h, y1 + np.random.randint(8, 28))
        shift = int(np.random.randint(-18, 19) * intensity)
        if shift != 0:
            slice_data = res[y1:y2, :]
            res[y1:y2, :] = np.roll(slice_data, shift, axis=1)
            # Occasional color channel inversion on glitch slice
            if np.random.random() < 0.2:
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


def process_video_lofi(
    input_video: Path,
    output_video: Path,
    audio_source: Path | None = None,
    config: AestheticConfig | None = None,
    preview_range: tuple[float, float] | None = None,
    on_progress=None,
    cancel_check=None,
) -> Path:
    """Run the complete Lo-Fi Emo-Trap aesthetic video pipeline."""
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

    # Temporary directory for intermediate files
    temp_dir = Path(tempfile.mkdtemp(prefix="lofi_fx_"))
    temp_wav = temp_dir / "audio.wav"
    temp_raw_avi = temp_dir / "processed.mp4"

    try:
        # Extract audio for analysis
        audio_file = audio_source if (audio_source and audio_source.exists()) else input_video
        extract_audio_from_video(audio_file, temp_wav)

        # Analyze audio frequency envelopes
        low_curve, mid_curve, high_curve = analyze_audio_bands(
            temp_wav,
            total_frames=out_total_frames,
            duration=duration_sec,
            start_sec=start_sec,
        )

        # Precompute static masks
        radial_mask = build_radial_mask(height, width)
        scanlines = build_scanline_mask(height, width)

        # Video writer for visual pass (H.264 mp4)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(temp_raw_avi), fourcc, out_fps, (width, height))

        # Seek to start
        cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000.0)

        # Frame step to achieve target fps decimation
        frame_step = src_fps / out_fps

        t0 = time.time()
        for i in range(out_total_frames):
            if cancel_check and cancel_check():
                out.release()
                cap.release()
                raise RuntimeError("Process cancelled by user")

            target_src_frame = int(round(start_sec * src_fps + i * frame_step))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_src_frame)
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            # 1. Audio-reactive Sidechain Zoom
            if cfg.sidechain_pump and cfg.enabled:
                low_val = low_curve[i] if i < len(low_curve) else 0.0
                scale = 1.0 + float(low_val ** 1.6) * 0.025
                frame = apply_sidechain_zoom(frame, scale)

            # 2. El Cocuy Wash (Crushed blacks & cold tint)
            if cfg.el_cocuy_wash and cfg.enabled:
                frame = apply_el_cocuy_wash(frame)

            # 3. Fogged Glass (Radial condensation blur)
            if cfg.fogged_glass_vignette and cfg.enabled:
                blurred = cv2.GaussianBlur(frame, (27, 27), sigmaX=9)
                low_pulse = (low_curve[i] * 0.25) if (cfg.sidechain_pump and i < len(low_curve)) else 0.0
                effective_mask = np.clip(radial_mask * (1.0 + low_pulse), 0.0, 1.0)
                frame = (frame.astype(np.float32) * (1.0 - effective_mask) + blurred.astype(np.float32) * effective_mask).astype(np.uint8)

            # 4. Subtle Chromatic Aberration
            if cfg.chromatic_aberration and cfg.enabled:
                frame = apply_chromatic_aberration(frame, shift_px=3)

            # 5. Fuzzed Chord Glitch
            if cfg.fuzzed_glitch and cfg.enabled:
                mid_val = mid_curve[i] if i < len(mid_curve) else 0.0
                frame = apply_fuzzed_glitch(frame, mid_val)

            # 6. CRT Scanlines
            if cfg.crt_scanlines and cfg.enabled:
                frame = (frame.astype(np.float32) * scanlines).astype(np.uint8)

            # 7. Dynamic ISO Noise
            if cfg.iso_noise and cfg.enabled:
                high_val = high_curve[i] if i < len(high_curve) else 0.0
                sigma = 10.0 + 26.0 * (cfg.noise_strength + high_val * 0.7)
                noise = np.random.normal(0, sigma, (height, width, 1)).astype(np.float32)
                frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)

            out.write(frame)

            if on_progress and (i % 5 == 0 or i == out_total_frames - 1):
                pct = ((i + 1) / out_total_frames) * 100
                on_progress(pct, f"Applying Lo-Fi Emo-Trap FX: frame {i+1}/{out_total_frames} ({pct:.0f}%)")

        out.release()
        cap.release()

        # Final muxing: remux original audio cleanly onto the 15fps visualizer
        output_video.parent.mkdir(parents=True, exist_ok=True)
        mux_cmd = [
            ffmpeg, "-hide_banner", "-y",
            "-i", str(temp_raw_avi),
            "-ss", str(start_sec), "-t", str(duration_sec),
            "-i", str(audio_file),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_video),
        ]
        if on_progress:
            on_progress(96.0, "Muxing audio & finalizing video...")

        subprocess.run(mux_cmd, check=True, capture_output=True)
        if on_progress:
            on_progress(100.0, "Lo-Fi render complete!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_video
