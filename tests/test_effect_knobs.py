"""The per-effect knobs: each one must change the picture, and neutral values must change nothing."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_fx import (
    AestheticConfig,
    apply_chromatic_aberration,
    apply_color_grade,
    apply_color_wash,
    apply_fx_frame,
    apply_sidechain_zoom,
    build_fx_context,
    build_radial_mask,
    build_scanline_mask,
)

H, W = 96, 128


def textured_frame():
    rng = np.random.default_rng(1)
    base = rng.integers(20, 235, size=(H, W, 3), dtype=np.uint8)
    # smooth structure so zoom/fog/shifts are visible, plus per-channel differences for the CA tests
    base[:, :, 0] = np.linspace(0, 255, W, dtype=np.uint8)[None, :]
    base[:, :, 2] = np.linspace(255, 0, H, dtype=np.uint8)[:, None]
    return base


def render(low=0.0, mid=0.0, high=0.0, frame=None, **cfg):
    config = AestheticConfig(seed=7, **cfg)
    ctx = build_fx_context(config, H, W)
    return apply_fx_frame((textured_frame() if frame is None else frame).copy(), ctx, low, mid, high)


def differs(a, b):
    return not np.array_equal(a, b)


class TestNewKnobsChangeTheImage(unittest.TestCase):
    def test_wash_crush_and_tint_channels(self):
        base = render()
        self.assertTrue(differs(base, render(wash_crush=0.0)))
        self.assertTrue(differs(base, render(wash_tint_r=-30.0)))
        self.assertTrue(differs(base, render(wash_tint_b=-30.0)))
        self.assertTrue(differs(base, render(wash_tint_g=30.0)))

    def test_color_grade_is_off_by_default_and_neutral_values_are_identity(self):
        base = render()
        self.assertTrue(np.array_equal(base, render(color_grade=True)))  # neutral grade
        self.assertTrue(np.array_equal(base, render(grade_contrast=1.6)))  # toggle off -> ignored
        for knob in ({"grade_brightness": 30.0}, {"grade_contrast": 1.5},
                     {"grade_saturation": 0.0}, {"grade_fade": 40.0}):
            self.assertTrue(differs(base, render(color_grade=True, **knob)), knob)

    def test_color_grade_directions(self):
        f = textured_frame()
        self.assertGreater(apply_color_grade(f, brightness=40).mean(), f.mean())
        self.assertLess(apply_color_grade(f, brightness=-40).mean(), f.mean())
        faded = apply_color_grade(f, fade=60)
        self.assertGreaterEqual(int(faded.min()), 60 - 1)  # blacks are lifted
        grey = apply_color_grade(f, saturation=0.0)
        self.assertLess(int(np.abs(grey[:, :, 0].astype(int) - grey[:, :, 2].astype(int)).max()), 4)
        self.assertIs(apply_color_grade(f), f)

    def test_zoom_base_and_anchor(self):
        base = render(low=0.0)
        zoomed = render(low=0.0, zoom_base=0.06)
        self.assertTrue(differs(base, zoomed))
        left = render(low=0.0, zoom_base=0.06, zoom_origin_x=0.0)
        right = render(low=0.0, zoom_base=0.06, zoom_origin_x=1.0)
        self.assertTrue(differs(left, right))
        flat = textured_frame()
        # anchored at the left edge the first column survives the zoom
        out = apply_sidechain_zoom(flat, 1.2, (0.0, 0.0))
        self.assertTrue(np.array_equal(out[0, 0], flat[0, 0]))
        self.assertIs(apply_sidechain_zoom(flat, 1.0), flat)

    def test_fog_radius_and_haze(self):
        near = build_radial_mask(H, W, 0.1)
        far = build_radial_mask(H, W, 0.8)
        self.assertGreater(float(near.mean()), float(far.mean()))
        base = render()
        self.assertTrue(differs(base, render(fog_radius=0.0)))
        hazy = render(fog_haze=0.8)
        corner = (slice(0, 8), slice(0, 8))
        self.assertGreater(hazy[corner].mean(), base[corner].mean())

    def test_chromatic_vertical_and_bass_pulse(self):
        base = render()
        self.assertTrue(differs(base, render(ca_vertical_px=4)))
        self.assertTrue(np.array_equal(render(low=0.0, ca_pulse=6.0), render(low=0.0, ca_pulse=0.0)))
        self.assertTrue(differs(render(low=1.0, ca_pulse=6.0), render(low=1.0, ca_pulse=0.0)))
        f = textured_frame()
        out = apply_chromatic_aberration(f, shift_px=0, vertical_px=3)
        self.assertTrue(differs(out, f))
        self.assertTrue(np.array_equal(out[:, :, 1], f[:, :, 1]))  # green never moves
        self.assertIs(apply_chromatic_aberration(f, 0, 0), f)

    def test_glitch_shape_knobs(self):
        common = dict(mid=1.0, glitch_probability=1.0, glitch_threshold=0.1, glitch_max_shift=30)
        base = render(**common)
        self.assertTrue(differs(base, render(glitch_max_slices=12, **common)))
        self.assertTrue(differs(base, render(glitch_slice_height=90, **common)))
        self.assertTrue(differs(render(glitch_swap_chance=0.0, **common),
                                render(glitch_swap_chance=1.0, **common)))

    def test_scanline_thickness(self):
        thin = build_scanline_mask(12, 4, strength=0.5, spacing=4, thickness=1)[:, 0, 0]
        thick = build_scanline_mask(12, 4, strength=0.5, spacing=4, thickness=2)[:, 0, 0]
        self.assertEqual(int((thin < 1).sum()), 3)
        self.assertEqual(int((thick < 1).sum()), 6)
        # thickness can never black out the whole frame
        every = build_scanline_mask(12, 4, strength=0.5, spacing=3, thickness=9)[:, 0, 0]
        self.assertTrue((every == 1.0).any())

    def test_noise_size_makes_chunkier_grain(self):
        flat = np.full((H, W, 3), 128, dtype=np.uint8)
        only_noise = dict(
            color_wash=False, fogged_glass_vignette=False, chromatic_aberration=False,
            fuzzed_glitch=False, crt_scanlines=False, sidechain_pump=False, framerate_decimate=False,
        )

        def equal_neighbours(size):
            out = render(frame=flat, noise_size=size, noise_strength=0.5, **only_noise)[:, :, 0].astype(int)
            return float((out[:, 1:] == out[:, :-1]).mean())

        self.assertLess(equal_neighbours(1), 0.35)
        self.assertGreater(equal_neighbours(4), 0.6)


class TestNewKnobConfig(unittest.TestCase):
    def test_defaults_are_neutral(self):
        cfg = AestheticConfig()
        self.assertFalse(cfg.color_grade)
        self.assertEqual((cfg.zoom_base, cfg.fog_haze, cfg.ca_vertical_px, cfg.ca_pulse), (0, 0, 0, 0))
        self.assertEqual((cfg.wash_tint_b, cfg.wash_tint_g, cfg.wash_tint_r), (16.0, -6.0, 10.0))

    def test_from_dict_clamps_new_fields(self):
        cfg = AestheticConfig.from_dict({
            "grade_contrast": 99, "zoom_origin_x": -4, "glitch_max_slices": 100,
            "noise_size": 0, "scanline_thickness": "3", "wash_tint_r": "nope",
        })
        self.assertEqual(cfg.grade_contrast, 1.8)
        self.assertEqual(cfg.zoom_origin_x, 0.0)
        self.assertEqual(cfg.glitch_max_slices, 12)
        self.assertEqual(cfg.noise_size, 1)
        self.assertEqual(cfg.scanline_thickness, 3)
        self.assertEqual(cfg.wash_tint_r, 10.0)  # bad value ignored -> default

    def test_wash_is_a_blend_with_crush_zero_still_valid(self):
        f = textured_frame()
        self.assertEqual(apply_color_wash(f, crush=0.0).shape, f.shape)
        self.assertIs(apply_color_wash(f, amount=0.0), f)


if __name__ == "__main__":
    unittest.main()
