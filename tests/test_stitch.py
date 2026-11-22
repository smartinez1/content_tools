"""Tests for stitch_segments helpers and the /api/stitch* path handling."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stitch_segments import discover_groups, group_key, natural_key

try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
except Exception:  # fastapi/httpx or heavy deps not installed
    TestClient = None


class TestDiscovery(unittest.TestCase):
    def test_natural_sort_and_group_suffix(self):
        self.assertEqual(group_key(Path("song - Compose_2.mp4")), "song - Compose")
        self.assertEqual(group_key(Path("song - Compose.mp4")), "song - Compose")
        names = ["a 10.mp4", "a 2.mp4", "a 1.mp4"]
        self.assertEqual(
            [p.name for p in sorted((Path(n) for n in names), key=natural_key)],
            ["a 1.mp4", "a 2.mp4", "a 10.mp4"],
        )

    def test_discover_groups_skips_partials_and_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for n in ["x.mp4", "x_2.mp4", "x_10.mp4", "y.mp4", "y.part.mp4", ".hidden.mp4", "notes.txt"]:
                (d / n).write_bytes(b"")
            groups = dict(discover_groups(d))
        self.assertEqual([p.name for p in groups["x"]], ["x.mp4", "x_2.mp4", "x_10.mp4"])
        self.assertEqual([p.name for p in groups["y"]], ["y.mp4"])
        self.assertEqual(set(groups), {"x", "y"})
        self.assertEqual(discover_groups(Path(tmp) / "missing"), [])


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestStitchApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for target, value in (("PROJECT_ROOT", self.root), ("AUDIO_DIR", self.root / "wav_files")):
            patcher = mock.patch.object(lyrics_sync_ui, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_paths_outside_the_project_are_refused(self):
        res = self.client.get("/api/stitch-sources", params={"folder": "/etc"})
        self.assertEqual(res.status_code, 400)
        res = self.client.get("/api/video/stitch", params={"path": "../../etc/passwd.mp4"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/stitch", json={"group": "g", "folder": "/tmp"})
        self.assertEqual(res.status_code, 400)

    def test_missing_folder_and_bad_requests(self):
        self.assertEqual(self.client.get("/api/stitch-sources").status_code, 404)
        self.assertEqual(self.client.post("/api/stitch", json={}).status_code, 400)
        (self.root / "oto_generated_clips").mkdir()
        res = self.client.post("/api/stitch", json={"group": "nope"})
        self.assertEqual(res.status_code, 404)

    def test_needs_two_segments(self):
        clips = self.root / "oto_generated_clips"
        clips.mkdir()
        (clips / "g.mp4").write_bytes(b"")
        res = self.client.post("/api/stitch", json={"group": "g"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("two segments", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestStitchEndToEnd(unittest.TestCase):
    """Real ffmpeg: three tiny pieces named the way Oto names them, joined through the API."""

    @classmethod
    def setUpClass(cls):
        import subprocess

        from lyrics_sync import find_ffmpeg

        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        clips = cls.root / "oto_generated_clips"
        clips.mkdir()
        for name in ("song - Compose.mp4", "song - Compose_2.mp4", "song - Compose_10.mp4"):
            subprocess.run(
                [
                    find_ffmpeg(), "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x240:rate=15:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(clips / name),
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for target, value in (
            ("PROJECT_ROOT", self.root),
            ("AUDIO_DIR", self.root / "wav_files"),
            ("VIDEO_DIR", self.root / "sm_content"),
        ):
            patcher = mock.patch.object(lyrics_sync_ui, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(lyrics_sync_ui.app)

    def _wait(self, job_id):
        import time

        for _ in range(200):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.1)
        self.fail("stitch job did not finish")

    def test_sources_group_by_oto_suffix_in_natural_order(self):
        data = self.client.get("/api/stitch-sources").json()
        self.assertEqual([g["name"] for g in data["groups"]], ["song - Compose"])
        group = data["groups"][0]
        self.assertEqual(
            [s["name"] for s in group["segments"]],
            ["song - Compose.mp4", "song - Compose_2.mp4", "song - Compose_10.mp4"],
        )
        seg = group["segments"][0]
        self.assertEqual((seg["width"], seg["height"]), (160, 240))
        self.assertTrue(seg["url"].startswith("/api/video/stitch?path="))
        self.assertEqual(group["compose"]["mobile"]["path"], "sm_content/song - Compose.mp4")
        self.assertEqual(group["compose"]["youtube"]["path"], "sm_content/youtube/song - Compose.mp4")
        self.assertFalse(group["compose"]["mobile"]["exists"])

    def test_join_writes_atomically_and_reports_the_result(self):
        res = self.client.post("/api/stitch", json={"group": "song - Compose"})
        self.assertEqual(res.status_code, 200, res.text)
        job = self._wait(res.json()["job_id"])
        self.assertEqual(job["status"], "completed", job)
        out = self.root / "oto_generated_clips" / "joined" / "song - Compose.mp4"
        self.assertTrue(out.is_file())
        self.assertEqual([p.name for p in out.parent.iterdir()], ["song - Compose.mp4"])  # no .part left
        self.assertAlmostEqual(job["result"]["seconds"], 3.0, delta=0.2)
        self.assertEqual(job["result"]["output"], str(out.resolve()))
        # the joined file is not picked up as a piece of the next scan
        groups = self.client.get("/api/stitch-sources").json()["groups"]
        self.assertEqual(len(groups[0]["segments"]), 3)
        self.assertTrue(groups[0]["output_url"])

    def test_audio_start_must_be_a_number_or_auto(self):
        res = self.client.post("/api/stitch", json={"group": "song - Compose", "audio_start": "soon"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("audio_start", res.json()["detail"])

    def test_failed_join_leaves_an_existing_video_alone(self):
        target = self.root / "sm_content" / "song - Compose.mp4"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"precious")
        self.addCleanup(target.unlink, missing_ok=True)
        res = self.client.post(
            "/api/stitch",
            json={"group": "song - Compose", "output": "sm_content/song - Compose.mp4", "audio": "wav_files/missing.wav"},
        )
        self.assertEqual(res.status_code, 404)  # refused up front
        self.assertEqual(target.read_bytes(), b"precious")
        # an unusable subset (one piece) is refused too
        res = self.client.post(
            "/api/stitch",
            json={"group": "song - Compose", "files": ["song - Compose.mp4"], "output": "sm_content/song - Compose.mp4"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["song - Compose.mp4"])


class TestMasterAlignment(unittest.TestCase):
    """The song master is laid under the joined picture, shifted to match it."""

    @classmethod
    def setUpClass(cls):
        import subprocess

        from lyrics_sync import find_ffmpeg

        cls.ff = find_ffmpeg()
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = Path(cls.tmp.name)

        def ffm(*args):
            subprocess.run([cls.ff, "-hide_banner", "-v", "error", "-y", *map(str, args)], check=True)

        # master: 7 s of pink noise. The picture's own sound plays it 0.3 s late (the master is ahead).
        ffm("-f", "lavfi", "-i", "anoisesrc=d=7:c=pink:r=48000:seed=11", "-ac", "2", d / "master.wav")
        ffm("-f", "lavfi", "-i", "testsrc=size=160x90:rate=15:duration=6.3",
            "-f", "lavfi", "-i", "anoisesrc=d=7:c=pink:r=48000:seed=11,adelay=300|300",
            "-t", "6.3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", d / "whole.mp4")
        cls.pieces = []
        for i in range(3):
            piece = d / f"song_{i}.mp4"
            ffm("-ss", i * 2.1, "-t", 2.1, "-i", d / "whole.mp4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", piece)
            cls.pieces.append(piece)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_negative_start_pads_silence_and_keeps_the_length(self):
        from stitch_segments import SR, external_audio

        data = external_audio(self.ff, self.d / "master.wav", self.d / "cut.wav", -0.5, 2.0)
        self.assertEqual(len(data), round(2.0 * SR) * 4)
        self.assertEqual(data[: round(0.5 * SR) * 4], b"\x00" * (round(0.5 * SR) * 4))
        self.assertNotEqual(data[round(0.6 * SR) * 4 : round(0.7 * SR) * 4].strip(b"\x00"), b"")
        positive = external_audio(self.ff, self.d / "master.wav", self.d / "cut2.wav", 0.25, 2.0)
        self.assertEqual(len(positive), round(2.0 * SR) * 4)

    def test_auto_start_finds_the_shift_and_the_result_follows_the_picture(self):
        from lyrics_sync import estimate_offset
        from stitch_segments import stitch

        out = self.d / "joined.mp4"
        result = stitch(self.pieces, out, audio=self.d / "master.wav", audio_start="auto")
        self.assertAlmostEqual(result["audio_start"], -0.3, delta=0.03)
        self.assertGreater(result["audio_confidence"], 0.5)
        self.assertEqual(result["audio"], "master")
        self.assertEqual(result["faded"], [])
        # the joined file plays the master 0.3 s late, exactly like the picture's original sound
        late = estimate_offset(self.d / "master.wav", out, seconds=6)
        self.assertAlmostEqual(late["offset"], 0.3, delta=0.03)

    def test_auto_start_refuses_a_master_that_does_not_match(self):
        import subprocess

        from stitch_segments import stitch

        other = self.d / "other.wav"
        subprocess.run([self.ff, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "anoisesrc=d=7:c=white:r=48000:seed=99", "-ac", "2", str(other)], check=True)
        with self.assertRaisesRegex(RuntimeError, "Could not align the master"):
            stitch(self.pieces, self.d / "x.mp4", audio=other, audio_start="auto")
