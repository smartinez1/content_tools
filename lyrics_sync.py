#!/usr/bin/env python3
"""Align a lyric .txt file to a song and burn synced karaoke captions into a video.

The .txt parser understands the section format used in this project, e.g.:

    [Verse-1]
    line one
    line two

    structure:
    [Intro]
    [Verse-1]
    [Chorus]

Repeated sections in the structure list are expanded automatically.

Project layout (auto-discovered when using --song):

    lyrics/<song>.txt
    wav_files/<song>.wav
    sm_content/<song> - Compose.mp4
    sm_content/<song> - lyrics.mp4   (output)

Examples:

    python3 scripts/lyrics_sync.py --song "espacio tiempo (2am)"
    python3 scripts/lyrics_sync.py sm_content/video.mp4 lyrics/song.txt
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("LYRICS_PROJECT_ROOT", Path(__file__).resolve().parent.parent))
LYRICS_DIR = PROJECT_ROOT / "lyrics"
AUDIO_DIR = PROJECT_ROOT / "wav_files"
VIDEO_DIR = PROJECT_ROOT / "sm_content"


@dataclass
class CaptionStyle:
    font_name: str = "Helvetica"
    font_size: int = 48
    primary_color: str = "#FFFF00"      # Sung / active fill (yellow)
    secondary_color: str = "#FFFFFF"    # Unsung fill (white)
    outline_color: str = "#000000"      # Outline border (black)
    back_color: str = "#80000000"       # Shadow / back box (&H80000000)
    bold: bool = True
    italic: bool = False
    outline: float = 4.0
    shadow: float = 2.0
    alignment: int = 2                  # Numpad alignment (2 = bottom-center)
    margin_l: int = 70
    margin_r: int = 70
    margin_v: int = 760                 # Vertical margin (bottom offset)
    box_width: int | None = None        # Subtitle column width in script pixels (e.g. 700)
    pos_x: float | None = None          # Absolute script-pixel X override
    pos_y: float | None = None          # Absolute script-pixel Y override
    fade_in_ms: int = 150
    fade_out_ms: int = 250
    karaoke: bool = True
    karaoke_tag: str = "kf"             # 'kf' (sweep), 'k' (jump), 'ko' (outline)
    blur: float = 0.0
    pop_scale: float = 0.0              # Pop scale percentage (e.g. 15 for 115%)
    letter_spacing: int = 0             # Letter spacing in pixels (\fsp)
    lowercase: bool = True              # Grotesque emo aesthetic (all lowercase)
    text_opacity: float = 0.88          # Opacity (0.0 to 1.0; 0.88 = never 100% solid)
    opacity_trailing: bool = True       # Uneven trailing dissolve
    time_offset: float = 0.0            # Seconds added to every line/word time (+ = later)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CaptionStyle":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def hex_to_ass_color(color_str: str, default_alpha: int = 0) -> str:
    """Convert #RGB, #RRGGBB, #RRGGBBAA, or &H... to ASS &HAABBGGRR."""
    s = color_str.strip()
    if s.startswith("&H") or s.startswith("&h"):
        return s
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        a = default_alpha
    elif len(s) == 8:
        r, g, b, a = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
    else:
        return "&H00FFFFFF"
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Install it with 'brew install ffmpeg' or 'pip install imageio-ffmpeg'.")


def put_ffmpeg_on_path() -> str:
    """stable-ts calls the literal command 'ffmpeg', so expose a symlink named ffmpeg."""
    ffmpeg = find_ffmpeg()
    bin_dir = Path(tempfile.mkdtemp(prefix="lyrics_sync_bin_"))
    for name in ("ffmpeg", "ffprobe"):
        link = bin_dir / name
        if not link.exists():
            link.symlink_to(ffmpeg)
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    return ffmpeg


def clean_line(line: str) -> str:
    line = line.split("\t", 1)[0]
    line = line.replace("[B]", " ")
    line = re.sub(r"\s+\d+\s*$", "", line)
    line = re.sub(r"\s+\d+\s*\([^)]*\)\s*$", "", line)
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def normalise_section(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def normalise_name(name: str) -> str:
    name = Path(name).name
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"\.(mp4|mov|m4v|wav|mp3|txt|ass|json)$", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# File-name markers for outputs, which must never be picked as source material:
# rendered lyric videos ("<song> - lyrics.mp4") and promo snippets ("<song> - snippet ....mp4").
NON_SOURCE_MARKERS = (" - lyrics", "_lyrics", " - snippet", "_snippet")


def find_closest(directory: Path, name: str, suffixes: set[str], exclude: tuple[str, ...] = ()) -> Path | None:
    if not directory.is_dir():
        return None
    target = normalise_name(name)
    if not target:
        return None

    scored = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in path.name.lower() for part in exclude):
            continue
        candidate = normalise_name(path.stem)
        score = difflib.SequenceMatcher(None, target, candidate).ratio()
        if candidate == target:
            score = 1.0
        elif candidate.startswith(target) or target.startswith(candidate):
            score = max(score, 0.9)
        scored.append((score, path))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    best_score, best_path = scored[0]
    return best_path if best_score >= 0.75 else None


def has_audio(video: Path) -> bool:
    try:
        import av

        with av.open(str(video)) as container:
            return bool(container.streams.audio)
    except Exception:
        return True


def parse_lyrics(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts = re.split(r"(?im)^\s*structure\s*:?\s*$", raw, maxsplit=1)
    body, order_text = parts[0], parts[1] if len(parts) > 1 else ""

    sections: dict[str, list[str]] = {}
    current = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", line)
        if header:
            current = normalise_section(header.group(1))
            sections.setdefault(current, [])
            continue
        if current is None or current in {"title", "tittle"}:
            continue
        cleaned = clean_line(line)
        if cleaned:
            sections[current].append(cleaned)

    order = [normalise_section(x) for x in re.findall(r"\[([^\]]+)\]", order_text)]
    if not order:
        order = [name for name in sections if name not in {"title", "tittle"}]

    lines: list[str] = []
    for name in order:
        lines.extend(sections.get(name, []))
    if not lines:
        raise ValueError(f"No lyric lines found in {path}")
    return lines


def group_words_by_line(result, lines: list[str]):
    words = [w for w in result.all_words() if w.word.strip()]
    expected = sum(len(line.split()) for line in lines)
    if len(words) != expected:
        raise ValueError(
            f"Alignment produced {len(words)} words but the lyrics contain {expected}. "
            "Check the .txt formatting or try a larger Whisper model."
        )

    grouped = []
    index = 0
    for line in lines:
        count = len(line.split())
        chunk = words[index:index + count]
        grouped.append(
            {
                "text": line,
                "start": float(chunk[0].start),
                "end": float(chunk[-1].end),
                "words": [
                    {"text": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                    for w in chunk
                ],
            }
        )
        index += count
    return grouped


MIN_LINE_SECONDS = 0.1


def _distribute_words(tokens: list[str], start: float, end: float) -> list[dict]:
    """Spread tokens contiguously over [start, end], weighted by character length."""
    weights = [max(len(t), 1) for t in tokens]
    total = float(sum(weights))
    span = end - start
    words = []
    cursor = start
    for i, (tok, weight) in enumerate(zip(tokens, weights)):
        word_end = end if i == len(tokens) - 1 else cursor + span * weight / total
        words.append({"text": tok, "start": round(cursor, 3), "end": round(word_end, 3)})
        cursor = word_end
    return words


def _clean_words(raw) -> list[dict]:
    """Keep only well-formed words ({text,start,end} with numeric times)."""
    cleaned = []
    for w in raw if isinstance(raw, list) else []:
        try:
            text = str(w["text"]).strip()
            start, end = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            cleaned.append({"text": text, "start": start, "end": max(end, start)})
    return cleaned


def normalise_timings(segments) -> list[dict]:
    """Validate and repair user-edited line timings.

    Input/output schema matches the alignment cache: a list of
    ``{"text", "start", "end", "words": [{"text", "start", "end"}]}``.

    - lines with empty text are dropped; text whitespace is collapsed;
    - lines are sorted by start, each line is at least MIN_LINE_SECONDS long, its end is
      clamped to the next line's start, and any remaining overlap is resolved by pushing
      later lines forward;
    - karaoke words follow the (possibly edited) text: unchanged word count keeps the word
      timings (rescaled if the line window moved), a different count redistributes the
      words evenly (by length) across the line window.

    Raises ValueError for malformed input.
    """
    if not isinstance(segments, list):
        raise ValueError("timings must be a list of line objects")

    lines = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"line {i + 1}: expected an object")
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"line {i + 1}: start and end must be numbers") from None
        if start != start or end != end or abs(start) == float("inf") or abs(end) == float("inf"):
            raise ValueError(f"line {i + 1}: start and end must be finite")
        text = " ".join(str(seg.get("text", "")).split())
        if not text:
            continue
        lines.append({"text": text, "start": max(0.0, start), "end": end,
                      "words": _clean_words(seg.get("words"))})

    lines.sort(key=lambda line: (line["start"], line["end"]))

    # Pass 1: minimum length, end clamped to the next line's start.
    for i, line in enumerate(lines):
        line["end"] = max(line["end"], line["start"] + MIN_LINE_SECONDS)
        if i + 1 < len(lines) and line["end"] > lines[i + 1]["start"]:
            line["end"] = max(lines[i + 1]["start"], line["start"] + MIN_LINE_SECONDS)
    # Pass 2: resolve remaining overlap (lines closer than MIN_LINE_SECONDS) by pushing forward.
    prev_end = 0.0
    for line in lines:
        if line["start"] < prev_end:
            line["start"] = prev_end
        line["end"] = max(line["end"], line["start"] + MIN_LINE_SECONDS)
        prev_end = line["end"]

    for line in lines:
        start, end = line["start"], line["end"]
        tokens = line["text"].split()
        old = line["words"]
        if len(old) == len(tokens):
            old_start, old_end = old[0]["start"], max(w["end"] for w in old)
            if old_end - old_start > 1e-6:
                scale = (end - start) / (old_end - old_start)
                words = [
                    {
                        "text": tok,
                        "start": round(start + (w["start"] - old_start) * scale, 3),
                        "end": round(start + (w["end"] - old_start) * scale, 3),
                    }
                    for tok, w in zip(tokens, old)
                ]
            else:
                words = _distribute_words(tokens, start, end)
        else:
            words = _distribute_words(tokens, start, end)
        # Final guard: words stay inside the line and never run backwards.
        cursor = start
        for w in words:
            w["start"] = min(max(w["start"], cursor), end)
            w["end"] = min(max(w["end"], w["start"]), end)
            cursor = w["start"]
        line["words"] = words
        line["start"], line["end"] = round(start, 3), round(end, 3)
    return lines


def align_lyrics(audio: Path, lines: list[str], model_size: str, language: str):
    put_ffmpeg_on_path()
    try:
        import stable_whisper
    except ImportError:
        sys.exit("stable-ts is not installed. Run: pip install stable-ts faster-whisper")

    model = stable_whisper.load_faster_whisper(model_size)
    result = model.align(str(audio), "\n".join(lines), language=language)
    if result is None:
        raise RuntimeError("Whisper alignment failed. Try a larger model (e.g. --model medium).")
    return group_words_by_line(result, lines)


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


MIN_FIT_SCALE = 0.6      # a line is never shrunk below this fraction of the font size to keep a word whole
MIN_SIDE_MARGIN = 30     # narrowest side margin (script px) a line may be widened to
FIT_SAFETY = 1.03        # measured widths are padded by this much (renderer/font differences)
_MEASURE_REF_SIZE = 100


def shift_timings(lines, offset: float):
    """Copies of ``lines`` moved by ``offset`` seconds; nothing starts before 0, lines that end up
    entirely before 0 are dropped. The input is not modified."""
    if not offset:
        return lines

    def mv(t):
        return max(0.0, t + offset)

    shifted = []
    for line in lines:
        if line["end"] + offset <= 0:
            continue
        new = dict(line, start=mv(line["start"]), end=mv(line["end"]))
        new["words"] = [dict(w, start=mv(w["start"]), end=mv(w["end"])) for w in line.get("words", [])]
        shifted.append(new)
    return shifted


@lru_cache(maxsize=32)
def _reference_font(name: str, bold: bool, italic: bool):
    """A PIL font for ``name`` found through fontconfig, or None when it can't be resolved."""
    try:
        from PIL import ImageFont

        spec = name + (":bold" if bold else "") + (":italic" if italic else "")
        out = subprocess.run(
            ["fc-match", "-f", "%{file}\n%{index}", spec], capture_output=True, text=True, timeout=5
        ).stdout.split("\n")
        if not out or not out[0].strip():
            return None
        index = int(out[1]) & 0xFFFF if len(out) > 1 and out[1].strip().isdigit() else 0
        return ImageFont.truetype(out[0].strip(), _MEASURE_REF_SIZE, index=index)
    except Exception:
        return None


def make_measurer(st: CaptionStyle):
    """``measure(text, font_size) -> width in script px``. Uses the real font when fontconfig and
    Pillow can find it, otherwise an average-glyph-width estimate (slightly pessimistic)."""
    font = _reference_font(st.font_name, st.bold, st.italic)

    def measure(text: str, size: float) -> float:
        if font is not None:
            width = font.getlength(text) * size / _MEASURE_REF_SIZE
        else:
            width = len(text) * size * (0.6 if st.bold else 0.55)
        return width + st.letter_spacing * len(text)

    return measure


def split_word(word: str, size: float, max_width: float, measure) -> list[str]:
    """Break ``word`` into chunks that each fit ``max_width``; every chunk but the last ends in '-'."""
    chunks = []
    rest = word
    while len(rest) > 2 and measure(rest, size) > max_width:
        n = len(rest) - 1
        while n > 1 and measure(rest[:n] + "-", size) > max_width:
            n -= 1
        chunks.append(rest[:n] + "-")
        rest = rest[n:]
    chunks.append(rest)
    return chunks


def plan_line_fit(words, font_size, width, margin_l, margin_r, measure) -> dict:
    """Keep every word of a line whole instead of letting the renderer cut it mid-word.

    A word is only cut when it is wider than the text column. In order of preference this
    1) widens the column for that line (down to ``MIN_SIDE_MARGIN``), 2) shrinks the font (down to
    ``MIN_FIT_SCALE``), 3) as a last resort splits the word with a trailing '-'.

    Returns ``{"margins": (l, r) | None, "font_size": int | None, "chunks": {word index: [str, ...]}}``;
    empty/None values mean "leave the line alone".
    """
    plan = {"margins": None, "font_size": None, "chunks": {}}
    widths = [measure(w, font_size) * FIT_SAFETY for w in words]
    widest = max(widths, default=0.0)
    if widest <= width - margin_l - margin_r:
        return plan

    max_avail = width - 2 * MIN_SIDE_MARGIN
    if widest <= max_avail:  # 1) widen
        side = max(MIN_SIDE_MARGIN, int((width - widest) // 2))
        plan["margins"] = (min(margin_l, side), min(margin_r, side))
        return plan

    plan["margins"] = (MIN_SIDE_MARGIN, MIN_SIDE_MARGIN)
    scale = max_avail / widest
    if scale >= MIN_FIT_SCALE:  # 2) shrink
        plan["font_size"] = max(1, int(font_size * scale * 0.98))
        return plan

    for i, (word, w) in enumerate(zip(words, widths)):  # 3) hyphenate what still can't fit
        if w > max_avail:
            plan["chunks"][i] = split_word(word, font_size, max_avail / FIT_SAFETY, measure)
    return plan


def build_ass(
    lines,
    duration: float,
    width: int = 1080,
    height: int = 1920,
    font: str | None = None,
    fontsize: int | None = None,
    margin_v: int | None = None,
    style: CaptionStyle | None = None,
) -> str:
    # Resolve style with backward-compatible parameter overrides
    st = CaptionStyle() if style is None else CaptionStyle(**style.to_dict())
    if font is not None:
        st.font_name = font
    if fontsize is not None:
        st.font_size = fontsize
    if margin_v is not None:
        st.margin_v = margin_v

    lines = shift_timings(lines, st.time_offset)
    measure = make_measurer(st)

    # First pass: decide how long each line stays on screen.
    events = []
    for i, line in enumerate(lines):
        start = max(0.0, line["start"] - 0.15)
        if events:
            start = max(start, events[-1]["end"] + 0.01)
        next_start = lines[i + 1]["start"] if i + 1 < len(lines) else duration
        end = min(next_start - 0.03, line["end"] + 1.5)
        if i + 1 == len(lines):
            end = min(duration, line["end"] + 2.0)
        if end <= start:
            end = start + 0.5
        events.append({"start": start, "end": end, "line": line})

    # Calculate horizontal margins
    margin_l = st.margin_l
    margin_r = st.margin_r
    if st.box_width is not None and st.box_width > 0:
        side = max(10, (width - int(st.box_width)) // 2)
        margin_l = side
        margin_r = side

    alpha_val = int(round((1.0 - max(0.05, min(1.0, st.text_opacity))) * 255))
    pri_col = hex_to_ass_color(st.primary_color, default_alpha=alpha_val)
    sec_col = hex_to_ass_color(st.secondary_color, default_alpha=alpha_val)
    out_col = hex_to_ass_color(st.outline_color, default_alpha=0)
    bak_col = hex_to_ass_color(st.back_color, default_alpha=0x80)

    bold_val = -1 if st.bold else 0
    italic_val = -1 if st.italic else 0

    header = f"""[Script Info]
; Generated by lyrics_sync.py
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,{st.font_name},{st.font_size},{pri_col},{sec_col},{out_col},{bak_col},{bold_val},{italic_val},0,0,100,100,{st.letter_spacing},0,1,{st.outline},{st.shadow},{st.alignment},{margin_l},{margin_r},{st.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogues = []
    tag_prefix = "\\" + st.karaoke_tag.lstrip("\\")
    for event in events:
        line = event["line"]
        words = line["words"]
        start, end = event["start"], event["end"]

        line_tags = []
        f_in = st.fade_in_ms
        f_out = st.fade_out_ms
        if st.opacity_trailing:
            f_in = max(180, f_in)
            f_out = max(350, f_out)
        if f_in > 0 or f_out > 0:
            line_tags.append(f"\\fad({int(f_in)},{int(f_out)})")
        if st.blur > 0:
            line_tags.append(f"\\blur{st.blur:g}")
        if st.pos_x is not None and st.pos_y is not None:
            line_tags.append(f"\\an{st.alignment}\\pos({int(round(st.pos_x))},{int(round(st.pos_y))})")
        if st.pop_scale > 0:
            target_scale = int(round(100 + st.pop_scale))
            line_tags.append(f"\\t(0,180,\\fscx{target_scale}\\fscy{target_scale})\\t(180,360,\\fscx100\\fscy100)")

        use_karaoke = bool(words and st.karaoke)
        if use_karaoke:
            tokens = [w["text"].lower() if st.lowercase else w["text"] for w in words]
        else:
            raw_text = line["text"].lower() if st.lowercase else line["text"]
            tokens = raw_text.split()
        plan = plan_line_fit(tokens, st.font_size, width, margin_l, margin_r, measure)
        if plan["font_size"]:
            line_tags.append(f"\\fs{plan['font_size']}")
        ev_l, ev_r = plan["margins"] or (0, 0)  # 0 = use the style's margins
        chunks = plan["chunks"]

        parts = [f"{{{ ''.join(line_tags) }}}"] if line_tags else []

        if use_karaoke:
            gap = words[0]["start"] - start
            if gap > 0.005:
                parts.append("{%s%d}" % (tag_prefix, round(gap * 100)))

            previous_end = words[0]["start"]
            for j, word in enumerate(words):
                if j > 0:
                    gap = word["start"] - previous_end
                    if gap > 0.005:
                        parts.append("{%s%d}" % (tag_prefix, round(gap * 100)))
                prefix = "" if j == 0 else " "
                pieces = chunks.get(j, [tokens[j]])
                total_chars = sum(len(c.rstrip("-")) if k < len(pieces) - 1 else len(c)
                                  for k, c in enumerate(pieces)) or 1
                word_cs = max(1, round((word["end"] - word["start"]) * 100))
                for k, piece in enumerate(pieces):
                    last = k == len(pieces) - 1
                    share = len(piece) if last else len(piece.rstrip("-"))
                    duration_cs = max(1, round(word_cs * share / total_chars))
                    tail = "" if last else "\\N"
                    parts.append("{%s%d}%s%s%s" % (
                        tag_prefix, duration_cs, prefix if k == 0 else "", ass_escape(piece), tail))
                previous_end = word["end"]

            remaining = end - previous_end
            if remaining > 0.005:
                parts.append("{%s%d}" % (tag_prefix, round(remaining * 100)))
        else:
            if chunks:
                text = " ".join("\\N".join(ass_escape(c) for c in chunks.get(i, [t]))
                                for i, t in enumerate(tokens))
            else:
                text = ass_escape(raw_text)
            parts.append(text)

        dialogues.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Lyrics,,{ev_l},{ev_r},0,,{''.join(parts)}"
        )

    return header + "\n".join(dialogues) + "\n"


def get_duration(video: Path) -> float:
    try:
        import av

        with av.open(str(video)) as container:
            if container.duration:
                return float(container.duration) / av.time_base
    except Exception:
        pass
    ffmpeg = find_ffmpeg()
    output = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video)],
        capture_output=True,
        text=True,
    ).stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
    if not match:
        raise RuntimeError(f"Could not determine duration of {video}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def verify_ffmpeg_has_ass(ffmpeg_exe: str | None = None) -> bool:
    exe = ffmpeg_exe or find_ffmpeg()
    try:
        res = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True)
        return " ass " in res.stdout
    except Exception:
        return False


def render(
    video: Path,
    ass_file: Path,
    output: Path,
    ffmpeg: str,
    preview: tuple | None = None,
    on_progress=None,
    cancel_check=None,
):
    # libass filter paths cannot contain unescaped spaces/colons, so stage a copy.
    temp_ass = Path(tempfile.mkdtemp(prefix="lyrics_sync_ass_")) / "lyrics.ass"
    shutil.copyfile(ass_file, temp_ass)
    ass_arg = f"ass={temp_ass}"
    cmd = [ffmpeg, "-hide_banner", "-y"]
    if preview:
        start, duration = preview
        cmd += ["-ss", str(start), "-t", str(duration), "-copyts", "-i", str(video)]
    else:
        cmd += ["-i", str(video)]
    cmd += [
        "-vf", ass_arg,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
    ]
    if on_progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(output))

    print("Running:", " ".join(cmd))
    if not on_progress and not cancel_check:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            sys.exit(f"ffmpeg failed with exit code {result.returncode}")
        print(f"Done: {output}")
        return

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        for line in proc.stdout:
            if cancel_check and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("Render cancelled by user")
            line = line.strip()
            if "=" in line and on_progress:
                k, v = line.split("=", 1)
                on_progress(k, v)
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}: {err[-400:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    print(f"Done: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, nargs="?", help="input video (omit with --song)")
    parser.add_argument("lyrics", type=Path, nargs="?", help="lyric .txt file (omit with --song)")
    parser.add_argument("audio", type=Path, nargs="?", help="audio to align against (default: video audio)")
    parser.add_argument("--song", help="auto-discover assets by song name in lyrics/, wav_files/, sm_content/")
    parser.add_argument("--audio", type=Path, dest="audio_opt", help="align against this audio instead of the video audio")
    parser.add_argument("-o", "--output", type=Path, help="output video (default: <video> - lyrics.mp4)")
    parser.add_argument("--model", default="small", help="Whisper model size (default: small)")
    parser.add_argument("--language", default="es", help="lyric language (default: es)")
    parser.add_argument("--font", default="Helvetica", help="caption font (default: Helvetica)")
    parser.add_argument("--fontsize", type=int, default=48, help="caption font size (default: 48)")
    parser.add_argument("--margin-v", type=int, default=760, help="bottom margin in pixels (default: 760)")
    parser.add_argument("--box-width", type=int, help="subtitle column box width in pixels (e.g. 700)")
    parser.add_argument("--no-lowercase", action="store_true", help="keep original lyric casing (default: lowercase)")
    parser.add_argument("--project-root", type=Path, help="override project root directory")
    parser.add_argument("--ass-only", action="store_true", help="write the .ass file but do not render")
    parser.add_argument("--timings", type=Path, help="write aligned line timings as JSON (default in --song mode: <output>.json)")
    parser.add_argument("--preview", nargs=2, type=float, metavar=("START", "DURATION"), help="render only a preview segment")
    args = parser.parse_args()

    global PROJECT_ROOT, LYRICS_DIR, AUDIO_DIR, VIDEO_DIR
    if args.project_root:
        PROJECT_ROOT = args.project_root.expanduser().resolve()
        LYRICS_DIR = PROJECT_ROOT / "lyrics"
        AUDIO_DIR = PROJECT_ROOT / "wav_files"
        VIDEO_DIR = PROJECT_ROOT / "sm_content"

    if args.song:
        song = args.song
        video = (
            args.video
            or find_closest(VIDEO_DIR, song, {".mp4", ".mov", ".m4v"}, exclude=NON_SOURCE_MARKERS)
            or find_closest(PROJECT_ROOT, song, {".mp4", ".mov", ".m4v"}, exclude=NON_SOURCE_MARKERS)
        )
        if video is None:
            sys.exit(f"No source video for {song!r} found in {VIDEO_DIR} (excluding rendered lyrics/snippet files).")
        video = video.expanduser().resolve()

        lyrics = args.lyrics or find_closest(LYRICS_DIR, song, {".txt"}) or find_closest(PROJECT_ROOT, song, {".txt"})
        if lyrics is None:
            sys.exit(f"No lyric .txt for {song!r} found in {LYRICS_DIR}.")

        audio_arg = args.audio_opt or args.audio
        if audio_arg is None and not has_audio(video):
            audio_arg = find_closest(AUDIO_DIR, song, {".wav"}) or find_closest(PROJECT_ROOT, song, {".mp3"})
            if audio_arg is None:
                sys.exit(f"{video.name} has no audio and no WAV/MP3 master was found for {song!r}.")
        if audio_arg is None:
            wav_master = find_closest(AUDIO_DIR, song, {".wav"})
            if wav_master:
                print(f"Note: aligning against video audio; WAV master available at {wav_master.name} (pass --audio to use it).")

        output = args.output or (VIDEO_DIR / f"{song} - lyrics.mp4")
        timings = args.timings or output.with_suffix(".json")
    else:
        if not args.video or not args.lyrics:
            parser.error("provide video and lyrics, or use --song")
        video = args.video
        lyrics = args.lyrics
        audio_arg = args.audio_opt or args.audio
        output = args.output or video.with_name(f"{video.stem} - lyrics.mp4")
        timings = args.timings

    video = video.expanduser().resolve()
    lyrics = lyrics.expanduser().resolve()
    audio = audio_arg.expanduser().resolve() if audio_arg else video
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if timings:
        timings = timings.expanduser().resolve()
        timings.parent.mkdir(parents=True, exist_ok=True)

    print(f"Video:  {video}")
    print(f"Lyrics: {lyrics}")
    if audio_arg:
        print(f"Audio:  {audio}")

    lines = parse_lyrics(lyrics)
    print(f"Parsed {len(lines)} lyric lines from {lyrics.name}")

    if audio_arg is None:
        # Extract audio to a temporary 16 kHz mono WAV for stable-ts.
        ffmpeg = find_ffmpeg()
        temp_dir = Path(tempfile.mkdtemp(prefix="lyrics_sync_audio_"))
        audio = temp_dir / "audio.wav"
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
            check=True,
        )

    aligned = align_lyrics(audio, lines, args.model, args.language)

    if timings:
        timings.write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote timings: {timings}")

    duration = get_duration(video)
    cli_style = CaptionStyle(
        font_name=args.font,
        font_size=args.fontsize,
        margin_v=args.margin_v,
        box_width=args.box_width,
        lowercase=not args.no_lowercase,
    )
    ass_text = build_ass(
        aligned,
        duration,
        style=cli_style,
    )
    ass_file = output.with_suffix(".ass")
    ass_file.write_text(ass_text, encoding="utf-8")
    print(f"Wrote subtitles: {ass_file}")

    if not args.ass_only:
        render(video, ass_file, output, find_ffmpeg(), preview=tuple(args.preview) if args.preview else None)


if __name__ == "__main__":
    main()
