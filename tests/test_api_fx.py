"""API tests for the FX knob schema, style+FX presets and the single-frame FX preview."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cv2
    import numpy as np
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
    from video_fx import AestheticConfig, find_ffmpeg
except Exception:  # fastapi/httpx or heavy deps not installed
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestAestheticsSchema(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(lyrics_sync_ui.app)

    def test_schema_describes_every_field(self):
        data = self.client.get("/api/aesthetics").json()
        self.assertEqual(data["defaults"], AestheticConfig().to_dict())
        group_ids = {g["id"] for g in data["groups"]}
        self.assertTrue(group_ids >= {"wash", "zoom", "fog", "chroma", "glitch", "scanlines", "noise", "fps", "audio"})
        for name, spec in data["fields"].items():
            self.assertIn(spec["group"], group_ids, name)
            self.assertLess(spec["min"], spec["max"], name)
            self.assertTrue(spec["min"] <= spec["default"] <= spec["max"], name)
        # every group toggle is a real boolean field
        for group in data["groups"]:
            if group["toggle"]:
                self.assertIsInstance(data["defaults"][group["toggle"]], bool)


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestPresets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(lyrics_sync_ui, "PRESETS_FILE", Path(self.tmp.name) / "cfg" / "presets.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_defaults_include_style_presets_and_fx_looks(self):
        presets = self.client.get("/api/presets").json()
        self.assertIn("Narrow Blocky Column", presets)
        self.assertIsNone(presets["Narrow Blocky Column"]["aesthetics"])
        look = presets["FX · Heavy Lo-Fi"]
        self.assertIsNone(look["style"])
        self.assertEqual(look["aesthetics"]["target_fps"], 12)

    def test_save_style_and_fx_round_trip(self):
        body = {"style": {"font_size": 51}, "aesthetics": {"zoom_amount": 0.05, "ca_shift_px": 99}}
        self.assertEqual(self.client.post("/api/presets/mine", json=body).status_code, 200)
        saved = self.client.get("/api/presets").json()["mine"]
        self.assertEqual(saved["style"]["font_size"], 51)
        self.assertEqual(saved["aesthetics"]["zoom_amount"], 0.05)
        self.assertEqual(saved["aesthetics"]["ca_shift_px"], 12)  # clamped to the schema range

    def test_legacy_flat_preset_file_still_loads_as_style_only(self):
        path = lyrics_sync_ui.PRESETS_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"old": {"font_size": 40, "bold": False}}), encoding="utf-8")
        old = self.client.get("/api/presets").json()["old"]
        self.assertEqual(old["style"]["font_size"], 40)
        self.assertIsNone(old["aesthetics"])

    def test_legacy_flat_body_is_accepted(self):
        self.assertEqual(self.client.post("/api/presets/flat", json={"font_size": 44}).status_code, 200)
        flat = self.client.get("/api/presets").json()["flat"]
        self.assertEqual(flat["style"]["font_size"], 44)


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestFxFrameEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        subprocess.run(
            [
                find_ffmpeg(), "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=160x240:rate=15:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(cls.dir / "song - Compose.mp4"),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        patcher = mock.patch.object(lyrics_sync_ui, "VIDEO_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(lyrics_sync_ui.app)

    def _frame(self, aesthetics):
        res = self.client.post(
            "/api/fx-frame", json={"song_name": "song", "time_sec": 1.0, "aesthetics": aesthetics}
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-type"], "image/jpeg")
        img = cv2.imdecode(np.frombuffer(res.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape, (240, 160, 3))
        return img.astype(np.int32)

    def test_returns_a_jpeg_and_knobs_change_it(self):
        on = self._frame({})
        off = self._frame({"enabled": False})
        self.assertGreater(np.abs(on - off).mean(), 1.0)
        # same knobs twice -> identical frame (fixed seed), so slider comparisons are meaningful
        np.testing.assert_array_equal(on, self._frame({}))

    def test_errors(self):
        res = self.client.post("/api/fx-frame", json={"song_name": "nope", "time_sec": 0})
        self.assertEqual(res.status_code, 404)
        res = self.client.post("/api/fx-frame", json={"song_name": "song", "time_sec": "abc"})
        self.assertEqual(res.status_code, 400)


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestPrunePreviews(unittest.TestCase):
    def test_keeps_newest_and_leaves_foreign_files(self):
        import os

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            for i in range(8):
                stem = f"{i:010x}"
                for ext in (".mp4", ".ass"):
                    f = folder / f"{stem}{ext}"
                    f.write_bytes(b"x")
                    os.utime(f, (1000 + i, 1000 + i))
            (folder / "my notes.mp4").write_bytes(b"keep me")
            with mock.patch.object(lyrics_sync_ui, "PREVIEWS_DIR", folder):
                lyrics_sync_ui.prune_previews(keep=3)
            left = sorted(p.name for p in folder.iterdir())
            self.assertIn("my notes.mp4", left)
            kept = [n for n in left if n.endswith(".mp4") and n != "my notes.mp4"]
            self.assertEqual(kept, [f"{i:010x}.mp4" for i in (5, 6, 7)])
            self.assertEqual(sum(n.endswith(".ass") for n in left), 3)  # old .ass files went too


if __name__ == "__main__":
    unittest.main()
