#!/usr/bin/env python3
"""Convert a .mov video to a .mp4 (H.264/AAC) using ffmpeg."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


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


def convert(input_path: Path, output_path: Path, crf: int, preset: str) -> None:
    if not input_path.is_file():
        sys.exit(f"Input file not found: {input_path}")

    cmd = [
        find_ffmpeg(),
        "-hide_banner",
        "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed with exit code {result.returncode}")
    print(f"Done: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a .mov file to .mp4")
    parser.add_argument("input", type=Path, help="input .mov file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="output .mp4 file (default: input name with .mp4 extension)",
    )
    parser.add_argument("--crf", type=int, default=18, help="quality, lower is better (default: 18)")
    parser.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    args = parser.parse_args()

    output = args.output or args.input.with_suffix(".mp4")
    convert(args.input, output, args.crf, args.preset)


if __name__ == "__main__":
    main()
