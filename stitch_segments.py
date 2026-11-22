#!/usr/bin/env python3
"""Join the pieces of a long Oto export into one video, without re-encoding.

Oto builds a whole movie in memory and then hands it to the app in one piece. A
full track at HD/UHD is bigger than that hand-off allows, so the export stops at
the end with a buffer error. The way around it is to export the track as a few
shorter ranges (Export -> Length -> Custom, Fade -> No fade) and glue them back
together here.

The video is stream-copied (no quality loss). The sound is rebuilt from the
pieces so the joins are sample-accurate: Oto pads every AAC excerpt, which would
otherwise leave an audible gap at each cut.

If the pieces were exported with Oto's automatic fade (the default), every cut
has a half-second fade-out and the joins will dip. In that case pass the song's
master with --audio: it replaces the piece audio with one continuous track.

Examples:
    python3 scripts/stitch_segments.py "a - Compose 1.mp4" "a - Compose 2.mp4" -o "a - Compose.mp4"
    python3 scripts/stitch_segments.py --dir ~/Downloads --prefix "a - Compose" -o full.mp4
    python3 scripts/stitch_segments.py --dir parts --prefix "song - Compose" \\
        --audio "wav_files/song.wav" -o "song - Compose.mp4"

Use --reencode if the pieces came from different export settings; it decodes and
re-encodes everything, which is slower but tolerant of mismatches.

The Web Studio's Stitch tab calls stitch() from this module, so the command line
and the UI always run the same pipeline.
"""

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

SR = 48000


class Cancelled(Exception):
    """The caller asked to stop."""


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit(
            "ffmpeg not found. Install it with 'brew install ffmpeg' "
            "or 'pip install imageio-ffmpeg'."
        )


def natural_key(path: Path):
    """Sort 'clip 2' before 'clip 10'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", path.name)]


def group_key(path: Path) -> str:
    """Oto appends _2, _3... when a name is taken; the base name is the group."""
    return re.sub(r"_\d+$", "", path.stem)


def discover_groups(folder: Path) -> list[tuple[str, list[Path]]]:
    """Group the .mp4 files in a folder by the name they were exported under."""
    if not folder.is_dir():
        return []
    groups: dict[str, list[Path]] = {}
    for path in sorted(folder.glob("*.mp4"), key=natural_key):
        if not path.is_file() or path.name.startswith(".") or ".part" in path.name:
            continue
        groups.setdefault(group_key(path), []).append(path)
    return sorted(groups.items(), key=lambda item: item[0].lower())


def probe_video(ffmpeg: str, path: Path) -> dict:
    """Frame count, fps, size and duration of the picture track.

    The container duration is not used for cutting: it includes the AAC padding,
    and the concat demuxer would offset every piece by it.
    """
    info = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True
    ).stderr
    fps_match = re.search(r"(\d+(?:\.\d+)?) fps", info)
    fps = float(fps_match.group(1)) if fps_match else 30.0
    size_match = re.search(r"Video:.*?, (\d+)x(\d+)", info)
    width, height = (int(size_match.group(1)), int(size_match.group(2))) if size_match else (0, 0)
    counted = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    frames = re.findall(r"frame=\s*(\d+)", counted)
    if not frames:
        raise RuntimeError(f"Could not read the video track of {path.name}")
    count = int(frames[-1])
    return {
        "frames": count,
        "fps": fps,
        "duration": count / fps if fps else 0.0,
        "width": width,
        "height": height,
    }


def video_duration(ffmpeg: str, path: Path) -> float:
    return probe_video(ffmpeg, path)["duration"]


def run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed:\n" + proc.stderr[-2000:])


def run_progress(cmd, what, total, on_pct=None, cancel_check=None) -> None:
    """Run ffmpeg with -progress and turn its output into a 0..1 fraction."""
    with tempfile.TemporaryFile(mode="w+") as errlog:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=errlog, text=True, bufsize=1
        )
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    if on_pct and total > 0:
                        on_pct(max(0.0, min(1.0, seconds / total)))
                elif line == "progress=end" and on_pct:
                    on_pct(1.0)
                if cancel_check and cancel_check():
                    proc.kill()
                    raise Cancelled()
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code != 0:
            errlog.seek(0)
            raise RuntimeError(f"{what} failed:\n" + errlog.read()[-2000:])


def decode_audio(ffmpeg: str, src: Path, dst: Path) -> bool:
    proc = subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-i", str(src),
            "-map", "0:a:0", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def trim_pad(path: Path, seconds: float) -> bytes:
    """Cut the AAC padding after the last real sample, or pad a short piece."""
    with wave.open(str(path)) as w:
        data = w.readframes(w.getnframes())
    need = round(seconds * SR) * 4  # 2 channels x 16-bit
    if len(data) >= need:
        return data[:need]
    return data + b"\x00" * (need - len(data))


def tail_fade_ratio(path: Path) -> float:
    """How much quieter the last 100 ms is than the 500 ms before it. Oto's
    automatic fade drops this close to zero."""
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        span = min(w.getnframes(), int(0.6 * sr))
        w.setpos(w.getnframes() - span)
        samples = array("h")
        samples.frombytes(w.readframes(span))
    cut = int(0.5 * sr) * 2
    before, tail = samples[:cut], samples[cut:]

    def rms(values) -> float:
        return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0

    loud = rms(before)
    return rms(tail) / loud if loud else 1.0


def write_sound(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


def external_audio(ffmpeg: str, src: Path, dst: Path, start: float, seconds: float) -> bytes:
    """The song's own master, cut to the length of the joined picture."""
    if not src.is_file():
        raise RuntimeError(f"Audio file not found: {src}")
    run(
        [ffmpeg, "-v", "error", "-y", "-i", str(src), "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
         "-map", "0:a:0", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)],
        "ffmpeg (audio)",
    )
    return trim_pad(dst, seconds)


def concat_line(path: Path) -> str:
    # ffmpeg's concat demuxer wants single quotes, escaped as '\'' inside the path.
    return "file '" + str(path.resolve()).replace("'", "'\\''") + "'\n"


def stitch(
    files,
    output,
    *,
    audio=None,
    audio_start: float = 0.0,
    reencode: bool = False,
    on_progress=None,
    cancel_check=None,
    on_plan=None,
) -> dict:
    """Join ``files`` into ``output``. Returns a small report about the result.

    on_progress(fraction, message) is called as work completes; cancel_check()
    is polled between steps and while ffmpeg runs the slow ones; on_plan() is
    handed [(path, seconds)] once the pieces have been measured.
    """
    files = [Path(f).expanduser().resolve() for f in files]
    output = Path(output).expanduser().resolve()
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise RuntimeError("Missing segment(s):\n  " + "\n  ".join(missing))
    if len(files) < 2:
        raise RuntimeError("Need at least two segments to join.")
    if output in files:
        raise RuntimeError("The output would overwrite one of the segments.")

    def report(pct, msg):
        if on_progress:
            on_progress(max(0.0, min(1.0, pct)), msg)

    def check():
        if cancel_check and cancel_check():
            raise Cancelled()

    ffmpeg = find_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    report(0.01, "Reading the pieces...")
    durations = []
    for f in files:
        check()
        durations.append(video_duration(ffmpeg, f))
    total = sum(durations)
    if on_plan:
        on_plan(list(zip(files, durations)))

    sound = None
    with tempfile.TemporaryDirectory(prefix="stitch_") as tmp:
        tmpdir = Path(tmp)

        if reencode and not audio:
            report(0.03, "Re-encoding and joining...")
            inputs = [a for f in files for a in ("-i", str(f))]
            chain = "".join(f"[{i}:v][{i}:a]" for i in range(len(files)))
            run(
                [ffmpeg, "-hide_banner", "-y", *inputs,
                 "-filter_complex", f"{chain}concat=n={len(files)}:v=1:a=1[v][a]",
                 "-map", "[v]", "-map", "[a]",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)],
                "ffmpeg",
            )
        else:
            video = tmpdir / "video.mp4"
            if reencode:
                inputs = [a for f in files for a in ("-i", str(f))]
                chain = "".join(f"[{i}:v]" for i in range(len(files)))
                report(0.03, "Re-encoding the picture...")
                run(
                    [ffmpeg, "-hide_banner", "-y", *inputs,
                     "-filter_complex", f"{chain}concat=n={len(files)}:v=1:a=0[v]",
                     "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                     "-pix_fmt", "yuv420p", str(video)],
                    "ffmpeg (video)",
                )
            else:
                # Strip the sound first: Oto's container duration includes the AAC
                # padding, and the concat demuxer would offset every piece by it,
                # leaving a gap (and losing frames) at each join.
                parts = []
                for i, f in enumerate(files):
                    check()
                    report(0.02 + 0.38 * i / len(files), f"Separating {f.name}...")
                    part = tmpdir / f"v{i}.mp4"
                    run(
                        [ffmpeg, "-v", "error", "-y", "-i", str(f),
                         "-map", "0:v:0", "-c", "copy", "-an", str(part)],
                        "ffmpeg (strip sound)",
                    )
                    parts.append(part)
                listing = tmpdir / "segments.txt"
                listing.write_text("".join(concat_line(p) for p in parts), encoding="utf-8")
                report(0.42, "Joining the picture...")
                run(
                    [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                     "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(video)],
                    "ffmpeg (video)",
                )

            check()
            sound = None
            faded: list[str] = []
            if audio:
                report(0.55, "Laying the master audio under it...")
                part = tmpdir / "master.wav"
                pcm = external_audio(
                    ffmpeg, Path(audio).expanduser(), part, audio_start, total
                )
                sound = tmpdir / "sound.wav"
                write_sound(sound, pcm)
            else:
                pcm = bytearray()
                for i, (f, d) in enumerate(zip(files, durations)):
                    check()
                    report(0.45 + 0.10 * i / len(files), f"Rebuilding the sound of {f.name}...")
                    part = tmpdir / f"a{i}.wav"
                    if not decode_audio(ffmpeg, f, part):
                        pcm = None
                        break
                    if tail_fade_ratio(part) < 0.5:
                        faded.append(f.name)
                    pcm += trim_pad(part, d)
                if pcm is None:
                    sound = None
                else:
                    sound = tmpdir / "sound.wav"
                    write_sound(sound, bytes(pcm))

            if sound:
                report(0.66, "Writing the final file...")
                run_progress(
                    [ffmpeg, "-hide_banner", "-y", "-progress", "pipe:1", "-nostats",
                     "-i", str(video), "-i", str(sound),
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "aac", "-b:a", "192k", "-shortest",
                     "-movflags", "+faststart", str(output)],
                    "ffmpeg (mux)",
                    total,
                    on_pct=lambda f: report(0.66 + 0.33 * f, "Writing the final file..."),
                    cancel_check=cancel_check,
                )
            else:
                report(0.66, "Writing the final file...")
                shutil.copyfile(video, output)

    check()
    report(1.0, "Done.")
    info = probe_video(ffmpeg, output)
    return {
        "output": str(output),
        "seconds": info["duration"],
        "frames": info["frames"],
        "fps": info["fps"],
        "width": info["width"],
        "height": info["height"],
        "size_mb": round(output.stat().st_size / (1024 * 1024), 1),
        "audio": "master" if audio else ("segments" if sound else "none"),
        "faded": faded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Join Oto export segments into one video.")
    parser.add_argument("segments", nargs="*", help="segments, in order")
    parser.add_argument("--dir", help="folder to collect segments from")
    parser.add_argument("--prefix", help="only files starting with this (with --dir)")
    parser.add_argument("-o", "--output", required=True, help="finished video")
    parser.add_argument(
        "--audio",
        help="song master to use as the soundtrack (for pieces exported with Oto's auto fade)",
    )
    parser.add_argument(
        "--audio-start",
        type=float,
        default=0.0,
        help="where the picture starts inside --audio, in seconds (default: 0)",
    )
    parser.add_argument(
        "--reencode",
        action="store_true",
        help="re-encode everything instead of copying the video (tolerates mismatched pieces)",
    )
    args = parser.parse_args()

    if not args.dir and not args.segments:
        parser.error("give the segment files, or --dir with --prefix")

    if args.dir:
        folder = Path(args.dir).expanduser()
        if not folder.is_dir():
            sys.exit(f"Folder not found: {folder}")
        groups = discover_groups(folder)
        if args.prefix:
            groups = [g for g in groups if g[0].startswith(args.prefix)]
        files = [p for _, group in groups for p in group]
        if not files:
            pattern = f"{args.prefix}*.mp4" if args.prefix else "*.mp4"
            sys.exit(f"No .mp4 files matching {pattern!r} in {folder}")
        output_path = Path(args.output).expanduser().resolve()
        files = [p for p in files if p.resolve() != output_path]
    else:
        files = [Path(p).expanduser() for p in args.segments]

    def show_plan(plan):
        print(f"Joining {len(plan)} segments ({sum(d for _, d in plan):.1f} s):")
        for path, seconds in plan:
            print(f"  {path.name}  ({seconds:.2f} s)")

    try:
        report = stitch(
            files,
            args.output,
            audio=args.audio,
            audio_start=args.audio_start,
            reencode=args.reencode,
            on_plan=show_plan,
        )
    except (RuntimeError, Cancelled) as e:
        sys.exit(str(e) or "Cancelled.")

    print(f"\nWrote {report['output']}  ({report['size_mb']:.1f} MB, {report['seconds']:.1f} s)")
    if report["faded"]:
        print(
            "\nWarning: these pieces end with Oto's fade-out, so the joins will dip:\n  "
            + "\n  ".join(report["faded"])
            + "\nRe-export with Fade -> No fade, or pass --audio with the song master.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
