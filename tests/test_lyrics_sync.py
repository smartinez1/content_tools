"""Unit tests for lyrics_sync core functionality."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrics_sync import (
    NON_SOURCE_MARKERS,
    CaptionStyle,
    build_ass,
    find_closest,
    hex_to_ass_color,
    normalise_name,
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

    def test_find_closest_skips_snippet_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "song - Compose.mp4").write_bytes(b"")
            (d / "song - snippet 00m01.00s-00m31.00s.mp4").write_bytes(b"")

            found = find_closest(d, "song", {".mp4"}, exclude=NON_SOURCE_MARKERS)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "song - Compose.mp4")

    def test_non_source_markers_cover_lyrics_and_snippets(self):
        for name in ("song - lyrics.mp4", "song_lyrics.mp4", "song - snippet 00m01.00s.mp4", "song_snippet.mp4"):
            self.assertTrue(any(marker in name.lower() for marker in NON_SOURCE_MARKERS), name)
        for name in ("song - Compose.mp4", "song.mp4", "song - instrumental.mp4"):
            self.assertFalse(any(marker in name.lower() for marker in NON_SOURCE_MARKERS), name)


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


class SafeAreaFilterTests(unittest.TestCase):
    def test_default_fits_frame_inside_margins(self):
        from lyrics_sync import safe_area_filter

        f = safe_area_filter((200, 380, 100, 100))
        # height is the limiting side: (1920-200-380)/1920 = 0.697917
        self.assertIn("iw*0.697917", f)
        self.assertTrue(f.startswith("split[bg][fg];"))
        self.assertTrue(f.endswith("'"))  # last filter is the overlay so ',ass=' can follow

    def test_rejects_margins_that_leave_no_room(self):
        from lyrics_sync import safe_area_filter

        with self.assertRaises(ValueError):
            safe_area_filter((900, 900, 0, 0))


class SafeAreaStyleTests(unittest.TestCase):
    def test_caption_stays_on_same_spot_of_the_picture(self):
        from lyrics_sync import CaptionStyle, safe_area_style

        out = safe_area_style(CaptionStyle(margin_v=780, font_size=48, box_width=680), (200, 380, 100, 100))
        # text bottom was y=1140 of 1920 -> 200 + 1140*0.697917 = 995.6 -> margin 924
        self.assertEqual(out.margin_v, 924)
        self.assertEqual((out.font_size, out.box_width), (48, 680))  # size untouched

    def test_none_is_a_no_op(self):
        from lyrics_sync import CaptionStyle, safe_area_style

        st = CaptionStyle()
        self.assertIs(safe_area_style(st, None), st)


class SafeAreaParseTests(unittest.TestCase):
    def test_parse(self):
        from lyrics_sync import SAFE_AREA_DEFAULT, parse_safe_area

        self.assertIsNone(parse_safe_area(False))
        self.assertIsNone(parse_safe_area(None))
        self.assertEqual(parse_safe_area(True), SAFE_AREA_DEFAULT)
        self.assertEqual(parse_safe_area({"top": 100, "bottom": 200, "sides": 30}), (100, 200, 30, 30))
        # missing keys fall back to the defaults, out-of-range values are clamped
        self.assertEqual(parse_safe_area({"top": 9999, "sides": -5})[0:1], (500,))
        self.assertEqual(parse_safe_area({"sides": -5})[2:], (0, 0))
        with self.assertRaises(ValueError):
            parse_safe_area({"top": "abc"})
        with self.assertRaises(ValueError):
            parse_safe_area("yes")

    def test_frame_matches_the_filter_geometry(self):
        import numpy as np

        from lyrics_sync import safe_area_frame

        frame = np.full((192, 108, 3), 255, np.uint8)
        out = safe_area_frame(frame, (200, 400, 100, 100))  # margins are in 1080x1920 units; scale 0.6875
        self.assertEqual(out.shape, frame.shape)
        self.assertGreater(out[96, 54].mean(), 240)  # picture in the middle
        self.assertLess(out[2, 54].mean(), 200)  # darkened fill above it
        self.assertIs(safe_area_frame(frame, None), frame)

    def test_style_maps_custom_position_too(self):
        from lyrics_sync import CaptionStyle, safe_area_style

        out = safe_area_style(CaptionStyle(pos_x=540, pos_y=960), (200, 380, 100, 100))
        self.assertAlmostEqual(out.pos_x, 540, delta=1)  # centred picture, centred caption
        self.assertAlmostEqual(out.pos_y, 200 + 960 * 0.697917, delta=1)
