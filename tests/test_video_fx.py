"""Unit tests for the Lo-Fi audio-reactive video_fx module."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import fields

from video_fx import (
    AESTHETIC_SPECS,
    AestheticConfig,
    aesthetic_schema,
    analyze_audio_bands,
    apply_chromatic_aberration,
    apply_color_wash,
    apply_fuzzed_glitch,
    apply_fx_frame,
    apply_sidechain_zoom,
    build_fx_context,
    build_radial_mask,
    build_scanline_mask,
    find_ffmpeg,
    process_video_lofi,
    shape_envelope,
)


class TestVideoFX(unittest.TestCase):
    def test_config_defaults(self):
        cfg = AestheticConfig()
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.color_wash)
        self.assertTrue(cfg.framerate_decimate)
        self.assertEqual(cfg.target_fps, 15)
        self.assertTrue(cfg.sidechain_pump)
        self.assertTrue(cfg.iso_noise)

        d = cfg.to_dict()
        cfg2 = AestheticConfig.from_dict(d)
        self.assertEqual(cfg2.target_fps, 15)

    def test_masks_shapes(self):
        h, w = 120, 80
        radial = build_radial_mask(h, w)
        self.assertEqual(radial.shape, (h, w, 1))
        # Center should be 0 (clear), outer corner near 1
        self.assertAlmostEqual(radial[h // 2, w // 2, 0], 0.0, places=1)
        self.assertGreater(radial[0, 0, 0], 0.7)

        scan = build_scanline_mask(h, w)
        self.assertEqual(scan.shape, (h, 1, 1))

    def test_image_transforms(self):
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)

        # 1. Color wash
        washed = apply_color_wash(frame)
        self.assertEqual(washed.shape, frame.shape)
        self.assertEqual(washed.dtype, np.uint8)

        # 2. Chromatic aberration
        aberrated = apply_chromatic_aberration(frame, shift_px=2)
        self.assertEqual(aberrated.shape, frame.shape)

        # 3. Zoom
        zoomed = apply_sidechain_zoom(frame, scale=1.05)
        self.assertEqual(zoomed.shape, frame.shape)

    def test_audio_analysis_synthetic(self):
        sr = 22050
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        # 60 Hz kick + 1000 Hz synth + 8000 Hz hi-hat
        signal = 0.5 * np.sin(2 * np.pi * 60 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = Path(tf.name)

        try:
            sf.write(str(wav_path), signal, sr)
            low, mid, high = analyze_audio_bands(wav_path, total_frames=15, duration=duration)
            self.assertEqual(len(low), 15)
            self.assertEqual(len(mid), 15)
            self.assertEqual(len(high), 15)
            self.assertGreaterEqual(np.max(low), 0.0)
        finally:
            wav_path.unlink(missing_ok=True)


def _dominant_freq(wav_path: Path) -> float:
    data, sr = sf.read(str(wav_path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(data))
    return float(np.fft.rfftfreq(len(data), 1.0 / sr)[int(np.argmax(spectrum))])


class TestLofiAudioSource(unittest.TestCase):
    """Regression: a pre-cut preview clip must keep *its own* audio.

    The preview used to pass the full-length source as ``audio_source`` which made the
    output carry audio from 0s of the song instead of the previewed window.
    """

    def test_output_audio_comes_from_input_clip(self):
        ffmpeg = find_ffmpeg()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            clip = td / "clip.mp4"
            out = td / "out.mp4"
            out_wav = td / "out.wav"
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=128x192:rate=15:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=22050:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(clip),
                ],
                check=True,
            )
            cfg = AestheticConfig(framerate_decimate=False)
            process_video_lofi(clip, out, config=cfg)
            subprocess.run(
                [ffmpeg, "-hide_banner", "-v", "error", "-y", "-i", str(out),
                 "-vn", "-ac", "1", "-ar", "22050", str(out_wav)],
                check=True,
            )
            self.assertAlmostEqual(_dominant_freq(out_wav), 880.0, delta=30.0)


def _frame(h=96, w=64, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


class TestConfigKnobs(unittest.TestCase):
    def test_from_dict_ignores_unknown_clamps_and_coerces(self):
        cfg = AestheticConfig.from_dict({
            "bogus": 1,
            "zoom_amount": 5,            # above max -> clamped
            "ca_shift_px": "4",          # str -> int
            "glitch_max_shift": 7.6,     # float -> rounded int
            "noise_gain": "nan",         # not finite -> default kept
            "wash_amount": "abc",        # not numeric -> default kept
            "color_wash": "false",    # str bool
        })
        self.assertEqual(cfg.zoom_amount, AESTHETIC_SPECS["zoom_amount"]["max"])
        self.assertEqual(cfg.ca_shift_px, 4)
        self.assertIsInstance(cfg.ca_shift_px, int)
        self.assertEqual(cfg.glitch_max_shift, 8)
        self.assertEqual(cfg.noise_gain, 1.0)
        self.assertEqual(cfg.wash_amount, 1.0)
        self.assertFalse(cfg.color_wash)

    def test_percentiles_stay_ordered(self):
        cfg = AestheticConfig.from_dict({"low_floor": 50, "low_ceiling": 50})
        self.assertLess(cfg.low_floor, cfg.low_ceiling)

    def test_every_numeric_field_has_a_spec_and_defaults_are_in_range(self):
        for f in fields(AestheticConfig):
            if isinstance(f.default, bool):
                continue
            self.assertIn(f.name, AESTHETIC_SPECS, f"{f.name} needs an AESTHETIC_SPECS entry")
            spec = AESTHETIC_SPECS[f.name]
            self.assertTrue(spec["min"] <= f.default <= spec["max"], f.name)
        schema = aesthetic_schema()
        self.assertEqual(schema["defaults"], AestheticConfig().to_dict())
        for spec in schema["fields"].values():
            self.assertIn(spec["group"], [g["id"] for g in schema["groups"]])

    def test_defaults_round_trip(self):
        self.assertEqual(AestheticConfig.from_dict(AestheticConfig().to_dict()), AestheticConfig())


class TestFxFrame(unittest.TestCase):
    def test_every_knob_at_zero_is_identity(self):
        cfg = AestheticConfig(
            wash_amount=0, zoom_amount=0, fog_amount=0, ca_shift_px=0,
            glitch_max_shift=0, scanline_strength=0, noise_gain=0,
        )
        frame = _frame()
        ctx = build_fx_context(cfg, *frame.shape[:2])
        out = apply_fx_frame(frame, ctx, low=1.0, mid=1.0, high=1.0)
        np.testing.assert_array_equal(out, frame)

    def test_disabled_is_identity(self):
        frame = _frame()
        ctx = build_fx_context(AestheticConfig(enabled=False), *frame.shape[:2])
        np.testing.assert_array_equal(apply_fx_frame(frame, ctx, 1, 1, 1), frame)

    def test_default_matches_legacy_pipeline(self):
        """Defaults must reproduce the previous hard-coded stack (deterministic stages)."""
        h, w = 96, 64
        frame = _frame(h, w)
        low = 0.8
        cfg = AestheticConfig(fuzzed_glitch=False, iso_noise=False)
        got = apply_fx_frame(frame, build_fx_context(cfg, h, w), low=low)

        f = apply_sidechain_zoom(frame, 1.0 + float(low ** 1.6) * 0.025)
        f = apply_color_wash(f)
        blurred = cv2.GaussianBlur(f, (27, 27), sigmaX=9)
        mask = np.clip(build_radial_mask(h, w) * (1.0 + low * 0.25), 0.0, 1.0)
        f = (f.astype(np.float32) * (1.0 - mask) + blurred.astype(np.float32) * mask).astype(np.uint8)
        f = apply_chromatic_aberration(f, shift_px=3)
        scan = np.ones((h, 1, 1), dtype=np.float32)
        scan[::3] = 0.91
        f = (f.astype(np.float32) * scan).astype(np.uint8)
        np.testing.assert_array_equal(got, f)

    def test_each_knob_changes_the_picture(self):
        frame = _frame()
        base = AestheticConfig(seed=3)
        ref = apply_fx_frame(frame, build_fx_context(base, 96, 64), low=1.0, mid=1.0, high=1.0)
        for change in (
            {"wash_amount": 0.3}, {"wash_tint": 2.0}, {"zoom_amount": 0.08}, {"fog_amount": 0.2},
            {"fog_blur": 20}, {"ca_shift_px": 8}, {"scanline_strength": 0.4}, {"noise_gain": 2.0},
            {"scanline_spacing": 6},
        ):
            cfg = AestheticConfig.from_dict({**base.to_dict(), **change})
            out = apply_fx_frame(frame, build_fx_context(cfg, 96, 64), low=1.0, mid=1.0, high=1.0)
            self.assertFalse(np.array_equal(out, ref), f"{change} had no effect")

    def test_chromatic_aberration_zero_shift_returns_frame(self):
        frame = _frame()
        np.testing.assert_array_equal(apply_chromatic_aberration(frame, 0), frame)

    def test_glitch_gates(self):
        frame = _frame(120, 80)
        rng = np.random.default_rng(1)
        np.testing.assert_array_equal(apply_fuzzed_glitch(frame, 0.2, rng), frame)  # below threshold
        always = apply_fuzzed_glitch(frame, 1.0, np.random.default_rng(1), probability=1.0)
        self.assertFalse(np.array_equal(always, frame))
        np.testing.assert_array_equal(
            apply_fuzzed_glitch(frame, 1.0, np.random.default_rng(1), probability=1.0, max_shift=0), frame
        )

    def test_seed_makes_noise_and_glitch_reproducible(self):
        frame = _frame(120, 80)
        cfg = AestheticConfig(seed=42)
        a = apply_fx_frame(frame, build_fx_context(cfg, 120, 80), 0.5, 1.0, 0.7)
        b = apply_fx_frame(frame, build_fx_context(cfg, 120, 80), 0.5, 1.0, 0.7)
        np.testing.assert_array_equal(a, b)


class TestShapeEnvelope(unittest.TestCase):
    def test_defaults_leave_envelope_unchanged(self):
        env = np.linspace(0, 1, 20, dtype=np.float32)
        np.testing.assert_allclose(shape_envelope(env), env)

    def test_gain_clips_to_unit_range(self):
        out = shape_envelope(np.array([0.2, 0.6, 1.0], dtype=np.float32), gain=2.0)
        np.testing.assert_allclose(out, [0.4, 1.0, 1.0])

    def test_release_decays_gradually_and_attack_is_instant_by_default(self):
        env = np.array([0, 1, 0, 0, 0, 0], dtype=np.float32)
        out = shape_envelope(env, release_ms=300, fps=15)
        self.assertEqual(out[1], 1.0)  # attack 0 -> jumps straight up
        self.assertTrue(np.all(np.diff(out[1:]) < 0))  # then decays monotonically
        self.assertGreater(out[2], 0.0)

    def test_attack_ramps_up(self):
        out = shape_envelope(np.ones(8, dtype=np.float32), attack_ms=300, fps=15)
        self.assertLess(out[0], 1.0)
        self.assertTrue(np.all(np.diff(out) >= 0))

    def test_band_percentiles_change_normalisation(self):
        sr, dur = 22050, 2.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        signal = (0.2 + 0.8 * np.abs(np.sin(2 * np.pi * 1.5 * t))) * np.sin(2 * np.pi * 60 * t)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            sf.write(str(wav), signal, sr)
            default_low, _, _ = analyze_audio_bands(wav, 30, dur)
            wide_low, _, _ = analyze_audio_bands(wav, 30, dur, percentiles={"low": (0, 100)})
        self.assertFalse(np.allclose(default_low, wide_low))
        self.assertLess(wide_low.max(), 1.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
