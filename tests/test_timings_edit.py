"""Tests for editable lyric timings: normalise_timings, build_ass on edits, PUT /api/timings."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrics_sync import MIN_LINE_SECONDS, build_ass, normalise_timings


def line(text, start, end, words=None):
    if words is None:
        toks = text.split()
        step = (end - start) / len(toks)
        words = [
            {"text": t, "start": start + i * step, "end": start + (i + 1) * step}
            for i, t in enumerate(toks)
        ]
    return {"text": text, "start": start, "end": end, "words": words}


class TestNormaliseTimings(unittest.TestCase):
    def test_unchanged_input_is_stable(self):
        src = [line("hello big world", 1.0, 4.0), line("second line", 5.0, 7.0)]
        out = normalise_timings(src)
        self.assertEqual(len(out), 2)
        self.assertEqual([w["text"] for w in out[0]["words"]], ["hello", "big", "world"])
        for a, b in zip(src[0]["words"], out[0]["words"]):
            self.assertAlmostEqual(a["start"], b["start"], places=2)
            self.assertAlmostEqual(a["end"], b["end"], places=2)
        # idempotent
        self.assertEqual(normalise_timings(out), out)

    def test_sorts_and_clamps_overlap(self):
        out = normalise_timings([line("b b", 5.0, 9.0), line("a a", 1.0, 6.0)])
        self.assertEqual([ln["text"] for ln in out], ["a a", "b b"])
        self.assertLessEqual(out[0]["end"], out[1]["start"])
        self.assertAlmostEqual(out[0]["end"], 5.0)  # clamped to the next line's start

    def test_min_duration_and_start_floor(self):
        out = normalise_timings([line("x y", -3.0, -3.0)])
        self.assertEqual(out[0]["start"], 0.0)
        self.assertGreaterEqual(out[0]["end"] - out[0]["start"], MIN_LINE_SECONDS)

    def test_same_word_count_text_swap_keeps_timings(self):
        original = line("hello world", 1.0, 3.0,
                        [{"text": "hello", "start": 1.0, "end": 1.5},
                         {"text": "world", "start": 1.8, "end": 3.0}])
        edited = dict(original, text="hola mundo")
        out = normalise_timings([edited])[0]
        self.assertEqual([w["text"] for w in out["words"]], ["hola", "mundo"])
        self.assertEqual([(w["start"], w["end"]) for w in out["words"]],
                         [(1.0, 1.5), (1.8, 3.0)])

    def test_word_count_change_redistributes_over_window(self):
        edited = line("one", 2.0, 4.0)
        edited["text"] = "one two three four"
        out = normalise_timings([edited])[0]
        words = out["words"]
        self.assertEqual(len(words), 4)
        self.assertAlmostEqual(words[0]["start"], 2.0)
        self.assertAlmostEqual(words[-1]["end"], 4.0)
        for a, b in zip(words, words[1:]):
            self.assertAlmostEqual(a["end"], b["start"], places=2)  # contiguous
        # longer words get more time ("three" > "one")
        dur = {w["text"]: w["end"] - w["start"] for w in words}
        self.assertGreater(dur["three"], dur["one"])

    def test_moving_window_rescales_words(self):
        edited = line("a b", 1.0, 3.0)
        edited["start"], edited["end"] = 10.0, 14.0
        out = normalise_timings([edited])[0]
        self.assertAlmostEqual(out["words"][0]["start"], 10.0)
        self.assertAlmostEqual(out["words"][-1]["end"], 14.0)

    def test_missing_words_key_is_created(self):
        out = normalise_timings([{"text": "no words here", "start": 0, "end": 3}])
        self.assertEqual(len(out[0]["words"]), 3)

    def test_empty_text_dropped_and_whitespace_collapsed(self):
        out = normalise_timings([line("  spaced   out ", 1, 3), {"text": "  ", "start": 4, "end": 5}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "spaced out")

    def test_malformed_input_raises(self):
        for bad in ("nope", [1], [{"text": "x", "start": "a", "end": 2}],
                    [{"text": "x", "end": 2}], [{"text": "x", "start": float("nan"), "end": 2}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                normalise_timings(bad)


class TestBuildAssOnEdits(unittest.TestCase):
    def test_edited_lines_never_produce_negative_karaoke(self):
        edited = normalise_timings([line("old", 1.0, 2.0), line("two words now", 1.5, 6.0)])
        ass = build_ass(edited, duration=10.0)
        durations = [int(x) for x in re.findall(r"\\kf(-?\d+)", ass)]
        self.assertTrue(durations)
        self.assertTrue(all(d >= 1 for d in durations), durations)
        self.assertIn("two", ass)


try:
    from fastapi.testclient import TestClient

    import lyrics_sync_ui
except Exception:  # fastapi/httpx or heavy deps not installed
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class TestTimingsEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "song - Compose.mp4").write_bytes(b"")
        self.original = [line("hello world", 1.0, 3.0)]
        (self.dir / "song - lyrics.json").write_text(json.dumps(self.original), encoding="utf-8")
        patcher = mock.patch.object(lyrics_sync_ui, "VIDEO_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(lyrics_sync_ui.app)

    def test_put_then_get_round_trip_and_backup(self):
        edited = [dict(self.original[0], text="hola a todo el mundo", start=1.5, end=4.0)]
        res = self.client.put("/api/timings/song", json={"timings": edited})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()["timings"]
        self.assertEqual(body[0]["text"], "hola a todo el mundo")
        self.assertEqual(len(body[0]["words"]), 5)

        got = self.client.get("/api/timings/song").json()["timings"]
        self.assertEqual(got, body)
        bak = self.dir / "song - lyrics.json.bak"
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), self.original)

        # a second save must not overwrite the original backup
        self.client.put("/api/timings/song", json={"timings": body})
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), self.original)

    def test_bad_payloads(self):
        self.assertEqual(self.client.put("/api/timings/song", json={"timings": "x"}).status_code, 400)
        self.assertEqual(self.client.put("/api/timings/song", json={"timings": []}).status_code, 400)
        self.assertEqual(
            self.client.put("/api/timings/missing", json={"timings": [line("a", 0, 1)]}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
