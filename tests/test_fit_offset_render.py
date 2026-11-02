"""Tests for the global time offset, whole-word line fitting, the noise knob and safe renders."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import lyrics_sync
from lyrics_sync import (
    FIT_SAFETY,
    MIN_FIT_SCALE,
    MIN_SIDE_MARGIN,
    CaptionStyle,
    build_ass,
    plan_line_fit,
    shift_timings,
    split_word,
)
from video_fx import AestheticConfig, apply_fx_frame, build_fx_context


def fake_measure(text, size):
    """Every glyph is half the font size wide, so tests don't depend on installed fonts."""
    return 0.5 * size * len(text)


def line(text, start, end):
    toks = text.split()
    step = (end - start) / len(toks)
    words = [{"text": t, "start": start + i * step, "end": start + (i + 1) * step} for i, t in enumerate(toks)]
    return {"text": text, "start": start, "end": end, "words": words}


def first_start(ass):
    m = re.search(r"Dialogue: 0,(\d+):(\d+):(\d+\.\d+),", ass)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


class TestTimeOffset(unittest.TestCase):
    def test_shift_moves_lines_and_words_without_mutating(self):
        src = [line("a b", 10.0, 12.0)]
        out = shift_timings(src, 1.5)
        self.assertEqual((out[0]["start"], out[0]["end"]), (11.5, 13.5))
        self.assertEqual(out[0]["words"][0]["start"], 11.5)
        self.assertEqual(src[0]["start"], 10.0)  # input untouched

    def test_negative_shift_clamps_at_zero_and_drops_lines_before_zero(self):
        out = shift_timings([line("early", 0.5, 1.0), line("later", 4.0, 6.0)], -2.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "later")
        out = shift_timings([line("straddles zero", 1.0, 5.0)], -3.0)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertAlmostEqual(out[0]["end"], 2.0)
        self.assertTrue(all(w["start"] >= 0 and w["end"] >= w["start"] for w in out[0]["words"]))

    def test_zero_offset_is_identity(self):
        src = [line("a b", 1, 2)]
        self.assertIs(shift_timings(src, 0), src)

    def test_build_ass_applies_offset(self):
        lines = [line("hello there world", 10.0, 13.0)]
        base = first_start(build_ass(lines, 60.0, style=CaptionStyle()))
        later = first_start(build_ass(lines, 60.0, style=CaptionStyle(time_offset=2.0)))
        earlier = first_start(build_ass(lines, 60.0, style=CaptionStyle(time_offset=-2.0)))
        self.assertAlmostEqual(later - base, 2.0, places=1)
        self.assertAlmostEqual(base - earlier, 2.0, places=1)

    def test_offset_survives_style_round_trip(self):
        st = CaptionStyle.from_dict({"time_offset": 0.75})
        self.assertEqual(CaptionStyle.from_dict(st.to_dict()).time_offset, 0.75)


class TestLineFit(unittest.TestCase):
    # 48px font -> 24px per glyph; column 1080 - 70 - 70 = 940px
    def plan(self, word, font=48, ml=70, mr=70):
        return plan_line_fit(["short", word], font, 1080, ml, mr, fake_measure)

    def test_words_that_fit_leave_the_line_alone(self):
        p = self.plan("x" * 30)
        self.assertEqual((p["margins"], p["font_size"], p["chunks"]), (None, None, {}))

    def test_first_resort_is_widening_the_column(self):
        p = self.plan("x" * 40)  # 960 * 1.03 > 940, but fits the full width
        self.assertIsNotNone(p["margins"])
        self.assertLess(p["margins"][0], 70)
        self.assertGreaterEqual(p["margins"][0], MIN_SIDE_MARGIN)
        self.assertIsNone(p["font_size"])
        self.assertEqual(p["chunks"], {})
        self.assertGreaterEqual(1080 - sum(p["margins"]), 960 * FIT_SAFETY)

    def test_second_resort_is_shrinking_the_font(self):
        p = self.plan("x" * 46)  # wider than the whole screen at 48px, within the shrink limit
        self.assertLess(p["font_size"], 48)
        self.assertGreaterEqual(p["font_size"], 48 * MIN_FIT_SCALE)
        self.assertEqual(p["chunks"], {})
        width = fake_measure("x" * 46, p["font_size"]) * FIT_SAFETY
        self.assertLessEqual(width, 1080 - sum(p["margins"]))

    def test_last_resort_hyphenates_and_never_loses_letters(self):
        word = "supercalifragilisticoespialidoso" * 3  # 96 glyphs: far too wide even when shrunk
        p = self.plan(word)
        chunks = p["chunks"][1]
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.endswith("-") for c in chunks[:-1]))
        self.assertFalse(chunks[-1].endswith("-"))
        self.assertEqual("".join(c.rstrip("-") for c in chunks), word)
        limit = 1080 - 2 * MIN_SIDE_MARGIN
        for c in chunks:
            self.assertLessEqual(fake_measure(c, 48) * FIT_SAFETY, limit)
        self.assertNotIn(0, p["chunks"])  # the short word is untouched

    def test_split_word_always_makes_progress(self):
        self.assertEqual("".join(split_word("abcdef", 100, 1, fake_measure)).replace("-", ""), "abcdef")


class TestBuildAssFit(unittest.TestCase):
    long_line = [line("la supercalifragilisticoespialidoso palabra", 1.0, 4.0)]

    def build(self, **style):
        with mock.patch.object(lyrics_sync, "make_measurer", lambda st: fake_measure):
            return build_ass(self.long_line, 10.0, style=CaptionStyle(**style))

    def test_normal_line_is_unchanged(self):
        ass = self.build(font_size=20)
        dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][0]
        self.assertIn("Lyrics,,0,0,0,,", dialogue)
        self.assertNotIn("\\fs", dialogue)
        self.assertNotIn("\\N", dialogue)

    def test_wide_word_widens_that_line_only(self):
        ass = self.build(font_size=48, box_width=500)  # column 500px, the word needs ~ 780px
        dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][0]
        m = re.search(r"Lyrics,,(\d+),(\d+),0,,", dialogue)
        self.assertTrue(m and int(m.group(1)) < 290 and int(m.group(1)) >= MIN_SIDE_MARGIN, dialogue)
        self.assertNotIn("\\N", dialogue)
        self.assertIn("Style: Lyrics", ass)  # style margins untouched (box_width 500 -> 290)

    def test_hyphenated_word_keeps_karaoke_duration(self):
        ass = self.build(font_size=120, box_width=500)
        dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][0]
        self.assertIn("-\\N", dialogue)
        # all karaoke centiseconds still add up to roughly the line, and none is zero/negative
        durations = [int(x) for x in re.findall(r"\\kf(\d+)", dialogue)]
        self.assertTrue(all(d >= 1 for d in durations))
        body = re.split(r"Lyrics,,\d+,\d+,\d+,,", dialogue, maxsplit=1)[1]
        text = re.sub(r"\{[^}]*\}", "", body).replace("\\N", "")
        self.assertEqual(text.replace("-", "").replace(" ", ""), "lasupercalifragilisticoespialidosopalabra")

    def test_plain_text_mode_also_hyphenates(self):
        ass = self.build(font_size=120, box_width=500, karaoke=False)
        dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][0]
        self.assertIn("-\\N", dialogue)


class TestNoiseKnob(unittest.TestCase):
    def frame_after(self, **cfg):
        config = AestheticConfig(
            el_cocuy_wash=False, fogged_glass_vignette=False, chromatic_aberration=False,
            fuzzed_glitch=False, crt_scanlines=False, sidechain_pump=False,
            framerate_decimate=False, seed=3, **cfg,
        )
        ctx = build_fx_context(config, 64, 96)
        src = np.full((64, 96, 3), 120, dtype=np.uint8)
        return src, apply_fx_frame(src.copy(), ctx, 0.0, 0.0, 0.0)

    def test_zero_strength_means_no_base_grain(self):
        src, out = self.frame_after(noise_strength=0.0)
        self.assertTrue(np.array_equal(src, out))

    def test_grain_grows_with_strength(self):
        _, low = self.frame_after(noise_strength=0.1)
        _, high = self.frame_after(noise_strength=0.9)
        self.assertLess(float(np.std(low.astype(float))), float(np.std(high.astype(float))))


try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
except Exception:  # fastapi/httpx not installed
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestRenderIsSafe(unittest.TestCase):
    """A cancelled or failed render must not destroy the previous export or leave scratch files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "song - Compose.mp4").write_bytes(b"src")
        (self.dir / "song - lyrics.json").write_text(json.dumps([line("hello world", 1, 3)]), encoding="utf-8")
        self.final = self.dir / "song - lyrics.mp4"
        self.final.write_bytes(b"PREVIOUS EXPORT")

        class Inline:  # run the job synchronously
            @staticmethod
            def submit(fn):
                fn()

        for target, value in {
            "VIDEO_DIR": self.dir, "executor": Inline(),
            "get_duration": lambda p: 10.0, "find_ffmpeg": lambda: "ffmpeg",
        }.items():
            patcher = mock.patch.object(lyrics_sync_ui, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(lyrics_sync_ui.app)

    def run_render(self, lofi, fake_render, fake_lofi=None):
        with mock.patch.object(lyrics_sync_ui, "render", fake_render), \
                mock.patch.object(lyrics_sync_ui, "process_video_lofi", fake_lofi or mock.Mock()):
            res = self.client.post("/api/render", json={"song_name": "song", "lofi_mode": lofi})
        self.assertEqual(res.status_code, 200, res.text)
        return lyrics_sync_ui.jobs[res.json()["job_id"]]

    def leftovers(self):
        return sorted(p.name for p in self.dir.iterdir() if ".part" in p.name or "_clean" in p.name)

    def test_cancelled_clean_render_keeps_previous_export(self):
        def render(video, ass, out, *a, **k):
            Path(out).write_bytes(b"half a video")
            raise RuntimeError("Render cancelled by user")

        job = self.run_render(False, render)
        self.assertEqual(job.status, "failed")
        self.assertEqual(self.final.read_bytes(), b"PREVIOUS EXPORT")
        self.assertEqual(self.leftovers(), [])

    def test_cancelled_lofi_pass_keeps_previous_export_and_removes_scratch(self):
        def render(video, ass, out, *a, **k):
            Path(out).write_bytes(b"clean")

        def lofi(clean, out, **k):
            Path(out).write_bytes(b"partial lofi")
            raise RuntimeError("Process cancelled by user")

        job = self.run_render(True, render, lofi)
        self.assertEqual(job.status, "failed")
        self.assertEqual(self.final.read_bytes(), b"PREVIOUS EXPORT")
        self.assertEqual(self.leftovers(), [])

    def test_success_replaces_export_and_removes_scratch(self):
        def render(video, ass, out, *a, **k):
            Path(out).write_bytes(b"clean")

        def lofi(clean, out, **k):
            Path(out).write_bytes(b"NEW LOFI EXPORT")

        job = self.run_render(True, render, lofi)
        self.assertEqual(job.status, "completed", job.message)
        self.assertEqual(self.final.read_bytes(), b"NEW LOFI EXPORT")
        self.assertEqual(self.leftovers(), [])

    def test_preset_normaliser_drops_time_offset(self):
        preset = lyrics_sync_ui._normalise_preset({"style": {"font_size": 50, "time_offset": 3.0}})
        self.assertEqual(preset["style"]["font_size"], 50)
        self.assertNotIn("time_offset", preset["style"])


if __name__ == "__main__":
    unittest.main()
