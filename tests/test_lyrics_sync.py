"""Unit tests for lyrics_sync core functionality."""

import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrics_sync import (
    CaptionStyle,
    ass_time,
    build_ass,
    clean_line,
    find_closest,
    hex_to_ass_color,
    normalise_name,
    normalise_section,
    parse_lyrics,
)


class TestColorAndNaming(unittest.TestCase):
    def test_hex_to_ass_color(self):
        self.assertEqual(hex_to_ass_color("#FFFF00"), "&H0000FFFF")  # yellow in BGR
        self.assertEqual(hex_to_ass_color("#FFFFFF"), "&H00FFFFFF")  # white
        self.assertEqual(hex_to_ass_color("#000000"), "&H00000000")  # black
        self.assertEqual(hex_to_ass_color("&H00AABBCC"), "&H00AABBCC")

    def test_normalise_name(self):
        self.assertEqual(normalise_name("rudio blanco.txt"), "rudio blanco")
        self.assertEqual(normalise_name("espacio tiempo (2am) - Compose.mp4"), "espacio tiempo 2am compose")

    def test_find_closest_fuzzy(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "rudio blanco.txt").write_text("dummy", encoding="utf-8")
            (d / "espacio tiempo (2am).txt").write_text("dummy", encoding="utf-8")

            found = find_closest(d, "ruido blanco", {".txt"})
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "rudio blanco.txt")


class TestParser(unittest.TestCase):
    def test_parse_structure(self):
        sample = """
[Intro]
one
two

[Chorus]
three four

structure:
[Intro]
[Chorus]
[Intro]
"""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tf:
            tf.write(sample)
            tf.flush()
            temp_path = Path(tf.name)

        try:
            lines = parse_lyrics(temp_path)
            self.assertEqual(lines, ["one", "two", "three four", "one", "two"])
        finally:
            temp_path.unlink()


class TestBuildASS(unittest.TestCase):
    def setUp(self):
        self.lines = [
            {
                "text": "hello world",
                "start": 1.0,
                "end": 2.5,
                "words": [
                    {"text": "hello", "start": 1.0, "end": 1.6},
                    {"text": "world", "start": 1.7, "end": 2.5},
                ],
            }
        ]

    def test_default_style_helvetica(self):
        ass = build_ass(self.lines, duration=5.0)
        self.assertIn("Style: Lyrics,Helvetica,48,", ass)
        self.assertIn("\\fad(", ass)
        self.assertIn("\\kf", ass)

    def test_box_width_and_lowercase(self):
        st = CaptionStyle(
            box_width=600,
            lowercase=True,
            opacity_trailing=False,
            fade_in_ms=100,
            fade_out_ms=200,
        )
        ass = build_ass([{"text": "HELLO World", "start": 0.5, "end": 2.0, "words": []}], duration=5.0, style=st)
        # 1080 - 600 = 480 // 2 = 240
        self.assertIn(",240,240,760,1", ass)
        self.assertIn("hello world", ass)
        self.assertIn("\\fad(100,200)", ass)

    def test_custom_style_effects(self):
        st = CaptionStyle(
            font_name="Arial",
            font_size=60,
            primary_color="#FF0000",
            bold=False,
            italic=True,
            outline=2.5,
            shadow=1.0,
            alignment=5,
            pos_x=540,
            pos_y=960,
            fade_in_ms=250,
            fade_out_ms=300,
            karaoke=False,
            opacity_trailing=False,
            blur=1.5,
            pop_scale=20,
            letter_spacing=4,
        )
        ass = build_ass(self.lines, duration=5.0, style=st)
        self.assertIn("Style: Lyrics,Arial,60,", ass)
        self.assertIn("100,100,4,0,1,2.5,1.0,5,", ass)
        self.assertIn("\\fad(250,300)", ass)
        self.assertIn("\\blur1.5", ass)
        self.assertIn("\\an5\\pos(540,960)", ass)
        self.assertIn("\\t(0,180,\\fscx120\\fscy120)", ass)
        # Karaoke is disabled
        self.assertNotIn("\\kf", ass)


if __name__ == "__main__":
    unittest.main()
