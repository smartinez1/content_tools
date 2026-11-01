"""Unit tests for the Lo-Fi audio-reactive video_fx module."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_fx import (
    AestheticConfig,
    analyze_audio_bands,
    apply_chromatic_aberration,
    apply_el_cocuy_wash,
    apply_sidechain_zoom,
    build_radial_mask,
    build_scanline_mask,
)


class TestVideoFX(unittest.TestCase):
    def test_config_defaults(self):
        cfg = AestheticConfig()
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.el_cocuy_wash)
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

        # 1. El Cocuy Wash
        washed = apply_el_cocuy_wash(frame)
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


if __name__ == "__main__":
    unittest.main()
