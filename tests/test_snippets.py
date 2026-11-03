"""Snippets: transitions, end card, render pipeline and API validation."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snippets import (
    TRANSITIONS,
    SnippetConfig,
    apply_transitions,
    build_end_card,
    crop_logo,
    render_snippet,
)
from video_fx import find_ffmpeg

H, W = 192, 108


def frame():
    rng = np.random.default_rng(0)
    return rng.integers(30, 220, size=(H, W, 3), dtype=np.uint8)


def make_clip(path: Path, seconds=4, size="108x192"):
    subprocess.run(
        [
            find_ffmpeg(), "-hide_banner", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=22050:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


def probe(path: Path) -> dict:
    out = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    ).stderr
    import re

    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return {"duration": dur, "audio": "Audio:" in out, "video": "Video:" in out}


class TestTransitions(unittest.TestCase):
    def test_zero_progress_is_identity(self):
        f = frame()
        for name in TRANSITIONS:
            out = apply_transitions(f.copy(), [name], 0.0, np.random.default_rng(1))
            np.testing.assert_array_equal(out, f, err_msg=name)

    def test_full_progress_blacks_out_for_crt_and_fade(self):
        for name in ("crt_off", "fade"):
            out = apply_transitions(frame(), [name], 1.0, np.random.default_rng(1))
            self.assertEqual(int(out.max()), 0, name)

    def test_every_transition_changes_the_picture_midway(self):
        f = frame()
        for name in TRANSITIONS:
            out = apply_transitions(f.copy(), [name], 0.5, np.random.default_rng(1))
            self.assertGreater(np.abs(out.astype(int) - f.astype(int)).mean(), 2.0, name)

    def test_combinations_are_deterministic_with_a_seed(self):
        a = apply_transitions(frame(), TRANSITIONS, 0.6, np.random.default_rng(5))
        b = apply_transitions(frame(), list(reversed(TRANSITIONS)), 0.6, np.random.default_rng(5))
        np.testing.assert_array_equal(a, b)  # fixed order, whatever order they were picked in


class TestEndCard(unittest.TestCase):
    def test_crop_finds_the_bright_lettering(self):
        img = np.full((100, 400, 3), 12, dtype=np.uint8)
        img[40:60, 250:350] = 240
        crop = crop_logo(img)
        self.assertLess(crop.shape[1], 140)
        self.assertLess(crop.shape[0], 40)
        self.assertGreater(crop.mean(), img.mean())

    def test_card_has_logo_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            logo = Path(tmp) / "logo.png"
            img = np.full((100, 400, 3), 20, dtype=np.uint8)
            cv2.putText(img, "logo", (230, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (240, 240, 240), 5)
            cv2.imwrite(str(logo), img)
            card = build_end_card(540, 960, logo, "20.11.2026")
        self.assertEqual(card.shape, (960, 540, 3))
        bright = card.max(axis=2) > 120
        rows = np.nonzero(bright.any(axis=1))[0]
        self.assertGreater(len(rows), 20)
        # the logo's own dark background is keyed out: corners stay black
        self.assertLess(int(card[:40, :40].max()), 10)

    def test_card_without_logo_still_shows_text(self):
        card = build_end_card(540, 960, None, "20.11.2026")
        self.assertGreater(int(card.max()), 150)


class TestConfig(unittest.TestCase):
    def test_from_dict_clamps_and_filters(self):
        cfg = SnippetConfig.from_dict({
            "start": "3", "end": 9, "transitions": ["glitch", "nope", "fade"],
            "transition_duration": 50, "card_duration": 0, "release_text": "  x  ",
            "fade_in": 0,
        })
        self.assertEqual((cfg.start, cfg.end), (3.0, 9.0))
        self.assertEqual(cfg.transitions, ["glitch", "fade"])
        self.assertEqual(cfg.transition_duration, 2.0)
        self.assertEqual(cfg.card_duration, 1.0)
        self.assertEqual(cfg.release_text, "x")
        self.assertFalse(cfg.fade_in)

    def test_defaults(self):
        cfg = SnippetConfig()
        self.assertEqual(cfg.transitions, ["glitch"])
        self.assertEqual(cfg.release_text, "20.11.2026")


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.src = cls.dir / "song - lyrics.mp4"
        make_clip(cls.src)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_duration_is_clip_plus_card_with_audio(self):
        out = self.dir / "out.mp4"
        cfg = SnippetConfig(start=1.0, end=3.0, card_duration=1.5, transitions=list(TRANSITIONS))
        render_snippet(self.src, out, cfg)
        info = probe(out)
        self.assertAlmostEqual(info["duration"], 3.5, delta=0.2)
        self.assertTrue(info["audio"])
        self.assertFalse(any(".part" in p.name for p in self.dir.iterdir()))

    def test_partial_window_and_downscale(self):
        out = self.dir / "win.mp4"
        cfg = SnippetConfig(start=0.5, end=3.0, card_duration=1.0)
        render_snippet(self.src, out, cfg, window=(1.5, 3.5), max_width=54)
        self.assertAlmostEqual(probe(out)["duration"], 2.0, delta=0.2)
        cap = cv2.VideoCapture(str(out))
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), 54)
        cap.release()

    def test_cancel_keeps_previous_export_and_leaves_no_scratch(self):
        out = self.dir / "keep.mp4"
        out.write_bytes(b"PREVIOUS")
        with self.assertRaises(RuntimeError):
            render_snippet(self.src, out, SnippetConfig(start=0, end=3), cancel_check=lambda: True)
        self.assertEqual(out.read_bytes(), b"PREVIOUS")
        self.assertFalse(any(".part" in p.name for p in self.dir.iterdir()))

    def test_bad_range_is_rejected(self):
        with self.assertRaises(RuntimeError):
            render_snippet(self.src, self.dir / "x.mp4", SnippetConfig(start=3, end=2))


try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
except Exception:  # fastapi/httpx not installed
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestSnippetApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        make_clip(cls.dir / "song - lyrics.mp4")
        (cls.dir / "song - Compose.mp4").write_bytes(b"not listed")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        class Inline:
            @staticmethod
            def submit(fn):
                fn()

        for target, value in {
            "VIDEO_DIR": self.dir, "executor": Inline(), "PREVIEWS_DIR": self.dir / "previews",
            "LOGO_FILE": self.dir / "missing-logo.png",
        }.items():
            p = mock.patch.object(lyrics_sync_ui, target, value)
            p.start()
            self.addCleanup(p.stop)
        (self.dir / "previews").mkdir(exist_ok=True)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_sources_list_only_rendered_videos(self):
        data = self.client.get("/api/snippet-sources").json()
        self.assertEqual([s["name"] for s in data["songs"]], ["song"])
        self.assertAlmostEqual(data["songs"][0]["duration"], 4.0, delta=0.2)
        self.assertEqual({t["id"] for t in data["transitions"]}, set(TRANSITIONS))
        self.assertIsNone(data["logo"])

    def test_validation_messages(self):
        res = self.client.post("/api/snippet", json={"song_name": "nope", "config": {}})
        self.assertEqual(res.status_code, 404)
        self.assertIn("nope - lyrics.mp4", res.json()["detail"])
        res = self.client.post("/api/snippet", json={"song_name": "song", "config": {"start": 3, "end": 2}})
        self.assertEqual(res.status_code, 422)
        self.assertIn("after the start", res.json()["detail"])
        res = self.client.post("/api/snippet", json={"song_name": "song", "config": {"start": 1, "end": 30}})
        self.assertEqual(res.status_code, 422)
        self.assertIn("past the end", res.json()["detail"])
        res = self.client.post("/api/snippet", json={"song_name": "../song", "config": {}})
        self.assertEqual(res.status_code, 404)

    def test_frame_is_a_jpeg(self):
        res = self.client.post(
            "/api/snippet-frame",
            json={"song_name": "song", "config": {"start": 0, "end": 2}, "time_sec": 2.5},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-type"], "image/jpeg")

    def test_export_lists_and_streams(self):
        res = self.client.post(
            "/api/snippet", json={"song_name": "song", "config": {"start": 0.5, "end": 2.0, "card_duration": 1}}
        )
        self.assertEqual(res.status_code, 200, res.text)
        job = lyrics_sync_ui.jobs[res.json()["job_id"]]
        self.assertEqual(job.status, "completed", job.message)
        items = self.client.get("/api/snippets").json()["snippets"]
        self.assertEqual(len(items), 1)
        self.assertIn("snippet 00m00.50s-00m02.00s", items[0]["name"])
        self.assertEqual(self.client.get(items[0]["url"]).status_code, 200)
        self.assertEqual(self.client.get("/api/video/snippet/..%2Fsong%20-%20lyrics.mp4").status_code, 404)

    def test_transition_preview_goes_to_previews(self):
        res = self.client.post("/api/snippet-preview", json={"song_name": "song", "config": {"start": 0, "end": 3}})
        job = lyrics_sync_ui.jobs[res.json()["job_id"]]
        self.assertEqual(job.status, "completed", job.message)
        self.assertTrue(job.output_url.startswith("/api/video/preview/"))
        self.assertEqual(self.client.get(job.output_url).status_code, 200)


if __name__ == "__main__":
    unittest.main()
