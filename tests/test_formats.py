"""Output formats: mobile 9:16 (sm_content/) and YouTube 16:9 (sm_content/youtube/)."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrics_sync import FORMATS, CaptionStyle, get_format, parse_safe_area, safe_area_style  # noqa: E402

try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
    from lyrics_sync import find_ffmpeg
except Exception:  # fastapi/httpx or heavy deps not installed
    TestClient = None


class TestFormatDefinitions(unittest.TestCase):
    def test_get_format(self):
        self.assertEqual(get_format(None)["id"], "mobile")
        self.assertEqual(get_format("youtube")["ref"], (1920, 1080))
        with self.assertRaises(ValueError):
            get_format("tiktok")

    def test_safe_area_defaults_and_limits_per_format(self):
        self.assertEqual(parse_safe_area(True, "mobile"), FORMATS["mobile"]["safe_area"])
        self.assertEqual(parse_safe_area(True, "youtube"), FORMATS["youtube"]["safe_area"])
        top, bottom, sides, _ = parse_safe_area({"top": 9999, "sides": 9999}, "youtube")
        self.assertEqual((top, sides), (300, 500))
        self.assertEqual(parse_safe_area({"sides": 9999}, "mobile")[2], 250)

    def test_caption_mapping_uses_the_formats_canvas(self):
        # landscape 1920x1080: bottom-anchored caption 80 px above the bottom edge
        sa = (100, 100, 100, 100)  # box 1720x880 -> scale 0.8148, picture 1564x880 centred
        out = safe_area_style(CaptionStyle(margin_v=80), sa, (1920, 1080))
        # text bottom at y=1000 -> 100 + 1000*0.8148 = 914.8 -> margin 1080 - 914.8
        self.assertEqual(out.margin_v, round(1080 - (100 + 1000 * (880 / 1080))))


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestFormatApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / "sm_content" / "youtube").mkdir(parents=True)
        (cls.root / "lyrics").mkdir()
        (cls.root / "lyrics" / "song.txt").write_text("[Verse]\nhola\n", encoding="utf-8")
        (cls.root / "oto_generated_clips" / "joined").mkdir(parents=True)
        for rel, size in (("sm_content/song - Compose.mp4", "108x192"),
                          ("sm_content/youtube/song - Compose.mp4", "192x108"),
                          ("sm_content/youtube/only yt - Compose.mp4", "192x108"),
                          # the Stitch tab's joined folder: the YouTube format also looks here
                          ("oto_generated_clips/joined/only joined - Compose.mp4", "192x108"),
                          ("oto_generated_clips/joined/song - Compose.mp4", "320x180"),
                          ("oto_generated_clips/joined/part only - Compose.part.mp4", "192x108")):
            subprocess.run(
                [find_ffmpeg(), "-hide_banner", "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050:duration=2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                 str(cls.root / rel)],
                check=True,
            )
        lines = [{"text": "hola", "start": 0.2, "end": 1.5,
                  "words": [{"text": "hola", "start": 0.2, "end": 1.5}]}]
        (cls.root / "sm_content" / "song - lyrics.json").write_text(json.dumps(lines), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for name, value in (
            ("PROJECT_ROOT", self.root),
            ("VIDEO_DIR", self.root / "sm_content"),
            ("LYRICS_DIR", self.root / "lyrics"),
            ("AUDIO_DIR", self.root / "wav_files"),
            ("PREVIEWS_DIR", self.root / "sm_content" / "previews"),
        ):
            patcher = mock.patch.object(lyrics_sync_ui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "sm_content" / "previews").mkdir(exist_ok=True)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_formats_endpoint(self):
        data = self.client.get("/api/formats").json()
        self.assertEqual(data["default"], "mobile")
        byid = {f["id"]: f for f in data["formats"]}
        self.assertEqual((byid["mobile"]["ref_w"], byid["mobile"]["ref_h"]), (1080, 1920))
        self.assertEqual((byid["youtube"]["ref_w"], byid["youtube"]["ref_h"]), (1920, 1080))
        self.assertTrue(byid["mobile"]["safe_default_on"])
        self.assertFalse(byid["youtube"]["safe_default_on"])

    def test_songs_are_listed_per_format(self):
        mobile = {s["name"]: s for s in self.client.get("/api/songs").json()["songs"]}
        yt = {s["name"]: s for s in self.client.get("/api/songs?format=youtube").json()["songs"]}
        self.assertIn("song", mobile)
        self.assertNotIn("only yt", mobile)  # a YouTube-only Compose does not leak into mobile
        self.assertIn("only yt", yt)
        self.assertIn("youtube", yt["song"]["video_path"])
        self.assertTrue(yt["song"]["has_timings"])  # lyric timings are shared between formats
        self.assertEqual(self.client.get("/api/songs?format=tiktok").status_code, 400)

    def test_youtube_also_finds_the_joined_folder(self):
        yt = {s["name"]: s for s in self.client.get("/api/songs?format=youtube").json()["songs"]}
        mobile = {s["name"]: s for s in self.client.get("/api/songs").json()["songs"]}
        self.assertIn("joined", yt["only joined"]["video_path"])
        self.assertNotIn("only joined", mobile)  # mobile never looks there
        # the format's own folder wins over joined/
        self.assertIn("youtube", yt["song"]["video_path"])
        self.assertNotIn("joined", yt["song"]["video_path"])
        # a join that is still being written (.part.mp4) is not a source
        self.assertNotIn("part only", yt)
        self.assertEqual(self.client.get("/api/video/source/only joined?format=youtube").status_code, 200)
        self.assertEqual(self.client.get("/api/video/source/only joined").status_code, 404)

    def test_joined_video_works_in_the_editing_endpoints(self):
        import cv2
        import numpy as np

        res = self.client.post("/api/fx-frame", json={
            "song_name": "only joined", "time_sec": 1.0, "format": "youtube", "apply_fx": False})
        self.assertEqual(res.status_code, 200, res.text)
        img = cv2.imdecode(np.frombuffer(res.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape[:2], (108, 192))
        res = self.client.post("/api/fx-frame", json={"song_name": "only joined", "time_sec": 1.0})
        self.assertEqual(res.status_code, 404)  # mobile has no such video
        res = self.client.post("/api/fx-clip", json={"song_name": "ghost", "format": "youtube"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("joined", res.json()["detail"])  # tells where it looks

    def test_source_video_comes_from_the_formats_folder(self):
        self.assertEqual(self.client.get("/api/video/source/only yt").status_code, 404)
        self.assertEqual(self.client.get("/api/video/source/only yt?format=youtube").status_code, 200)

    def test_fx_frame_uses_the_youtube_video(self):
        import cv2
        import numpy as np

        res = self.client.post("/api/fx-frame", json={
            "song_name": "song", "time_sec": 1.0, "format": "youtube", "apply_fx": False})
        self.assertEqual(res.status_code, 200, res.text)
        img = cv2.imdecode(np.frombuffer(res.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape[:2], (108, 192))  # the landscape file, not the portrait one
        res = self.client.post("/api/fx-frame", json={
            "song_name": "song", "time_sec": 1.0, "format": "youtube", "apply_fx": False,
            "safe_area": {"top": 10, "bottom": 10, "sides": 40}})
        self.assertEqual(res.status_code, 200, res.text)

    def _render(self, payload, song="song"):
        seen = {}

        def fake_render(video, ass_file, output, ffmpeg, **kw):
            seen["ass"] = Path(ass_file).read_text(encoding="utf-8")
            seen["video"] = Path(video)
            seen["kw"] = kw
            Path(output).write_bytes(b"x")

        with mock.patch.object(lyrics_sync_ui, "render", fake_render):
            res = self.client.post("/api/render", json={"song_name": song, "lofi_mode": False, **payload})
            self.assertEqual(res.status_code, 200, res.text)
            job_id = res.json()["job_id"]
            for _ in range(100):
                job = self.client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.1)
        self.assertEqual(job["status"], "completed", job)
        return seen

    def test_render_uses_each_formats_canvas_folder_and_caption_mapping(self):
        mobile = self._render({"safe_area": False})
        self.assertIn("PlayResX: 1080", mobile["ass"])
        self.assertIn("PlayResY: 1920", mobile["ass"])
        self.assertTrue((self.root / "sm_content" / "song - lyrics.mp4").is_file())

        yt = self._render({"format": "youtube", "safe_area": False})
        self.assertIn("PlayResX: 1920", yt["ass"])
        self.assertIn("PlayResY: 1080", yt["ass"])
        self.assertEqual(yt["video"].parent.name, "youtube")
        self.assertTrue((self.root / "sm_content" / "youtube" / "song - lyrics.mp4").is_file())

    def test_first_render_creates_the_formats_output_folder(self):
        fresh = self.root / "sm_content" / "fresh_yt"  # does not exist yet, like sm_content/youtube/ at first
        shutil_rmtree = __import__("shutil").rmtree
        self.addCleanup(shutil_rmtree, fresh, True)
        (self.root / "sm_content" / "only joined - lyrics.json").write_text(
            (self.root / "sm_content" / "song - lyrics.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.addCleanup((self.root / "sm_content" / "only joined - lyrics.json").unlink, missing_ok=True)
        real = lyrics_sync_ui.video_dir
        with mock.patch.object(lyrics_sync_ui, "video_dir", lambda fmt="mobile": fresh if fmt == "youtube" else real(fmt)):
            self.assertFalse(fresh.exists())
            seen = self._render({"format": "youtube", "safe_area": False}, song="only joined")
        self.assertIn("PlayResX: 1920", seen["ass"])
        self.assertTrue((fresh / "only joined - lyrics.mp4").is_file())
        self.assertTrue((fresh / "only joined - lyrics.ass").is_file())
        self.assertEqual(seen["video"].parent.name, "joined")  # source came from the joined folder

    def test_safe_area_moves_the_captions_in_the_burned_ass(self):
        def margin_v(ass):
            style = next(ln for ln in ass.splitlines() if ln.startswith("Style: Lyrics"))
            return int(style.split(",")[-2])

        plain = self._render({"safe_area": False, "style": {"margin_v": 780}})
        shrunk = self._render({"safe_area": {"top": 200, "bottom": 380, "sides": 100},
                               "style": {"margin_v": 780}})
        self.assertEqual(margin_v(plain["ass"]), 780)
        self.assertEqual(margin_v(shrunk["ass"]), 924)  # same spot on the shrunk picture
        self.assertEqual(shrunk["kw"]["safe_area"], (200, 380, 100, 100))
        self.assertEqual(shrunk["kw"]["safe_area_ref"], (1080, 1920))


if __name__ == "__main__":
    unittest.main()
