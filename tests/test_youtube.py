"""YouTube-format pieces: timing offset detection, resolution-aware FX, encode profiles, snippets."""

import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from lyrics_sync import estimate_offset, find_ffmpeg, video_encode_args  # noqa: E402
from video_fx import AestheticConfig, build_fx_context, scale_fx_pixels  # noqa: E402

try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
except Exception:  # fastapi/httpx not installed
    TestClient = None

FF = find_ffmpeg()


def ffmpeg(*args):
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *map(str, args)], check=True)


def stream_info(path) -> str:
    return subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr


class TestOffsetDetection(unittest.TestCase):
    def test_finds_a_known_delay_between_two_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rng = np.random.default_rng(7)
            sig = (rng.normal(0, 0.3, 8000 * 20)).astype(np.float32)

            def write(path, samples):
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(8000)
                    w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())

            write(d / "ref.wav", sig)
            write(d / "late.wav", np.concatenate([np.zeros(int(1.234 * 8000), np.float32), sig]))
            write(d / "early.wav", sig[int(0.5 * 8000):])
            late = estimate_offset(d / "ref.wav", d / "late.wav", seconds=15)
            early = estimate_offset(d / "ref.wav", d / "early.wav", seconds=15)
        self.assertAlmostEqual(late["offset"], 1.234, delta=0.01)
        self.assertGreater(late["confidence"], 0.5)
        self.assertAlmostEqual(early["offset"], -0.5, delta=0.01)

    def test_unrelated_audio_has_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, seed in (("a", 1), ("b", 2)):
                rng = np.random.default_rng(seed)
                with wave.open(str(d / f"{name}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(8000)
                    w.writeframes((rng.normal(0, 0.3, 8000 * 10) * 32767).astype(np.int16).tobytes())
            result = estimate_offset(d / "a.wav", d / "b.wav", seconds=10)
        self.assertLess(result["confidence"], 0.1)


class TestResolutionAwareFx(unittest.TestCase):
    def test_pixel_knobs_scale_with_the_frame_only_above_1080(self):
        cfg = AestheticConfig(ca_shift_px=3, glitch_max_shift=18, glitch_slice_height=28,
                              scanline_spacing=3, scanline_thickness=1, noise_size=1, fog_blur=9.0)
        self.assertIs(scale_fx_pixels(cfg, 1.0), cfg)
        big = scale_fx_pixels(cfg, 2.0)
        self.assertEqual((big.ca_shift_px, big.glitch_max_shift, big.glitch_slice_height), (6, 36, 56))
        self.assertEqual((big.scanline_spacing, big.scanline_thickness, big.noise_size), (6, 2, 2))
        self.assertAlmostEqual(big.fog_blur, 18.0)
        self.assertLess(big.scanline_thickness, big.scanline_spacing)
        self.assertEqual(build_fx_context(cfg, 2160, 3840).cfg.ca_shift_px, 6)  # 4K landscape
        self.assertEqual(build_fx_context(cfg, 1920, 1080).cfg.ca_shift_px, 3)  # mobile: unchanged
        self.assertEqual(build_fx_context(cfg, 1080, 1920).cfg.ca_shift_px, 3)  # 1080p landscape
        self.assertEqual(build_fx_context(cfg, 90, 160).cfg.ca_shift_px, 3)  # previews: unchanged


class TestEncodeProfiles(unittest.TestCase):
    def test_profiles(self):
        self.assertIsNone(video_encode_args(None))
        yt = video_encode_args("youtube")
        self.assertIn("18", yt)
        self.assertIn("bt709", yt)
        self.assertNotIn("-maxrate", yt)
        self.assertIn("veryfast", video_encode_args("youtube_intermediate"))

    def test_burn_and_lofi_write_a_bt709_landscape_master(self):
        from lyrics_sync import CaptionStyle, build_ass, render
        from video_fx import process_video_lofi

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src = d / "src.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=2",
                   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
            lines = [{"text": "hola", "start": 0.2, "end": 1.5,
                      "words": [{"text": "hola", "start": 0.2, "end": 1.5}]}]
            ass = d / "s.ass"
            ass.write_text(build_ass(lines, 2.0, width=1920, height=1080, style=CaptionStyle()), encoding="utf-8")
            burned = d / "burned.mp4"
            render(src, ass, burned, FF, encode_profile="youtube_intermediate")
            self.assertIn("bt709", stream_info(burned))
            out = d / "lofi.mp4"
            process_video_lofi(burned, out, config=AestheticConfig(), render_scale=0.5, encode_profile="youtube")
            info = stream_info(out)
            self.assertIn("320x180", info)  # upscaled back to the source size
            self.assertIn("bt709", info)
            cap = cv2.VideoCapture(str(out))
            self.assertGreater(cap.get(cv2.CAP_PROP_FRAME_COUNT), 10)


class TestLandscapeEndCard(unittest.TestCase):
    def test_tall_logo_is_fitted_inside_a_landscape_card(self):
        from snippets import build_end_card

        with tempfile.TemporaryDirectory() as tmp:
            logo = Path(tmp) / "logo.png"
            img = np.full((900, 600, 3), 30, np.uint8)
            cv2.circle(img, (300, 450), 200, (255, 255, 255), -1)
            cv2.imwrite(str(logo), img)
            card = build_end_card(1920, 1080, logo, "20.11.2026")
            portrait = build_end_card(1080, 1920, logo, "20.11.2026")
        self.assertEqual(card.shape, (1080, 1920, 3))
        self.assertEqual(portrait.shape, (1920, 1080, 3))
        self.assertGreater(card[400:700, 800:1100].max(), 200)  # the logo's disc is centred, on the card


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestYoutubeApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / "sm_content" / "youtube").mkdir(parents=True)
        noise = "anoisesrc=d=12:c=pink:r=22050:seed=3"
        for rel, size, delay in (("sm_content/song - Compose.mp4", "108x192", 0),
                                 ("sm_content/youtube/song - Compose.mp4", "192x108", 1234)):
            audio = noise + (f",adelay={delay}|{delay}" if delay else "")
            ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration=12",
                   "-f", "lavfi", "-i", audio,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.root / rel))
            if delay:
                continue
        for fmt_dir in ("sm_content", "sm_content/youtube"):
            (cls.root / fmt_dir / "song - lyrics.mp4").write_bytes(
                (cls.root / fmt_dir / "song - Compose.mp4").read_bytes()
            )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for name, value in (
            ("PROJECT_ROOT", self.root),
            ("VIDEO_DIR", self.root / "sm_content"),
            ("PRESETS_FILE", self.root / "cfg" / "presets.json"),
        ):
            patcher = mock.patch.object(lyrics_sync_ui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "cfg").mkdir(exist_ok=True)
        (self.root / "cfg" / "song_settings.json").unlink(missing_ok=True)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_detect_offset_between_mobile_and_youtube(self):
        res = self.client.post("/api/song-settings/song/detect-offset", json={"format": "youtube"})
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertAlmostEqual(data["offset"], 1.234, delta=0.05)
        self.assertTrue(data["reliable"])
        self.assertEqual(self.client.post("/api/song-settings/song/detect-offset", json={}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/song-settings/ghost/detect-offset", json={"format": "youtube"}).status_code, 404
        )

    def test_song_offset_is_stored_per_format(self):
        url = "/api/song-settings/song"
        self.client.put(url, json={"time_offset": 0.5})
        self.client.put(url, json={"time_offset": 1.25, "format": "youtube"})
        self.assertEqual(self.client.get(url).json(), {"time_offset": 0.5})
        self.assertEqual(self.client.get(url + "?format=youtube").json(), {"time_offset": 1.25})
        self.client.put(url, json={"time_offset": 0.0})  # clearing mobile keeps youtube
        self.assertEqual(self.client.get(url + "?format=youtube").json(), {"time_offset": 1.25})
        self.client.put(url, json={"time_offset": 0.0, "format": "youtube"})
        self.assertEqual(self.client.get(url + "?format=youtube").json(), {"time_offset": 0.0})

    def test_presets_remember_the_format_they_were_made_for(self):
        self.assertEqual(self.client.post("/api/presets/yt look", json={"style": {"font_size": 56}, "format": "youtube"}).status_code, 200)
        self.assertEqual(self.client.post("/api/presets/odd", json={"style": {"font_size": 40}, "format": "tiktok"}).status_code, 200)
        self.addCleanup(self.client.delete, "/api/presets/yt look")
        self.addCleanup(self.client.delete, "/api/presets/odd")
        presets = self.client.get("/api/presets").json()
        self.assertEqual(presets["yt look"]["format"], "youtube")
        self.assertNotIn("format", presets["odd"])  # unknown formats are dropped

    def test_snippet_sources_and_list_follow_the_format(self):
        mobile = self.client.get("/api/snippet-sources").json()["songs"]
        yt = self.client.get("/api/snippet-sources?format=youtube").json()["songs"]
        self.assertEqual([s["name"] for s in mobile], ["song"])
        self.assertEqual([s["name"] for s in yt], ["song"])
        self.assertEqual(self.client.get("/api/video/snippet-source/song?format=youtube").status_code, 200)
        self.assertEqual(self.client.get("/api/snippets?format=youtube").json(), {"snippets": []})
        self.assertEqual(self.client.get("/api/snippets?format=nope").status_code, 400)


if __name__ == "__main__":
    unittest.main()
