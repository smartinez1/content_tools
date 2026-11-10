"""Studio asset discovery: songs, snippet sources and the end-card logo."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lyrics_sync_ui as ui


class TestSongScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lyrics = self.root / "lyrics"
        self.audio = self.root / "wav_files"
        self.video = self.root / "sm_content"
        for d in (self.lyrics, self.audio, self.video):
            d.mkdir()

        (self.lyrics / "song.txt").write_text("dummy", encoding="utf-8")
        (self.lyrics / "other.txt").write_text("dummy", encoding="utf-8")
        (self.lyrics / "Untitled.jpg").write_bytes(b"")
        (self.video / "song - Compose.mp4").write_bytes(b"")
        (self.video / "song - lyrics.mp4").write_bytes(b"")
        (self.video / "song - snippet 00m01.00s-00m31.00s.mp4").write_bytes(b"")
        (self.video / "other - Compose.mp4").write_bytes(b"")

        self.patchers = [
            mock.patch.object(ui, "PROJECT_ROOT", self.root),
            mock.patch.object(ui, "LYRICS_DIR", self.lyrics),
            mock.patch.object(ui, "AUDIO_DIR", self.audio),
            mock.patch.object(ui, "VIDEO_DIR", self.video),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def test_snippet_outputs_and_stray_images_are_not_songs(self):
        names = [s["name"] for s in ui.get_songs()["songs"]]
        self.assertEqual(names, ["other", "song"])

    def test_source_video_ignores_snippets_and_lyrics_render(self):
        songs = {s["name"]: s for s in ui.get_songs()["songs"]}
        song = songs["song"]
        self.assertEqual(Path(song["video_path"]).name, "song - Compose.mp4")
        self.assertEqual(Path(song["rendered_path"]).name, "song - lyrics.mp4")

    def test_compose_video_is_not_reported_as_rendered(self):
        songs = {s["name"]: s for s in ui.get_songs()["songs"]}
        self.assertIsNone(songs["other"]["rendered_path"])
        self.assertFalse(songs["other"]["has_timings"])


class TestAliasDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lyrics = self.root / "lyrics"
        self.audio = self.root / "wav_files"
        self.video = self.root / "sm_content"
        for d in (self.lyrics, self.audio, self.video):
            d.mkdir()

        # Misspelled lyric file, correctly spelled audio/video: one song, not two.
        (self.lyrics / "rudio blanco.txt").write_text("dummy", encoding="utf-8")
        (self.audio / "ruido blanco.wav").write_bytes(b"")
        (self.video / "ruido blanco - Compose.mp4").write_bytes(b"")
        (self.video / "ruido blanco - lyrics.mp4").write_bytes(b"")

        self.patchers = [
            mock.patch.object(ui, "PROJECT_ROOT", self.root),
            mock.patch.object(ui, "LYRICS_DIR", self.lyrics),
            mock.patch.object(ui, "AUDIO_DIR", self.audio),
            mock.patch.object(ui, "VIDEO_DIR", self.video),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def test_misspelled_lyrics_file_does_not_create_a_second_song(self):
        songs = ui.get_songs()["songs"]
        self.assertEqual([s["name"] for s in songs], ["ruido blanco"])
        song = songs[0]
        self.assertEqual(Path(song["lyrics_path"]).name, "rudio blanco.txt")
        self.assertEqual(Path(song["video_path"]).name, "ruido blanco - Compose.mp4")
        self.assertEqual(Path(song["rendered_path"]).name, "ruido blanco - lyrics.mp4")


class TestSnippetSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.video = Path(self.tmp.name) / "sm_content"
        self.video.mkdir()
        (self.video / "song - lyrics.mp4").write_bytes(b"")
        (self.video / "other - Compose.mp4").write_bytes(b"")
        (self.video / "song - snippet 00m01.00s-00m31.00s.mp4").write_bytes(b"")
        self.patchers = [
            mock.patch.object(ui, "VIDEO_DIR", self.video),
            mock.patch.object(ui, "get_duration", return_value=42.0),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def test_only_rendered_lyric_videos_are_sources(self):
        data = ui.snippet_sources()
        self.assertEqual([s["name"] for s in data["songs"]], ["song"])
        self.assertEqual(data["songs"][0]["duration"], 42.0)


class TestDefaultLogo(unittest.TestCase):
    def test_prefers_any_known_extension(self):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                folder = root / "gemini_gens"
                folder.mkdir()
                (folder / f"{ui.DEFAULT_LOGO_STEM}{ext}").write_bytes(b"")
                self.assertEqual(ui.default_logo(root).suffix, ext)

    def test_missing_logo_falls_back_to_jpg_path(self):
        with tempfile.TemporaryDirectory() as td:
            logo = ui.default_logo(Path(td))
            self.assertEqual(logo.name, f"{ui.DEFAULT_LOGO_STEM}.jpg")
            self.assertFalse(logo.is_file())


if __name__ == "__main__":
    unittest.main()
