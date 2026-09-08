#!/usr/bin/env python3
"""Stream FLIR HDF5 recordings into lossless or near-lossless video files."""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


CAMERA_DIMENSIONS = {
    0: (768, 750),
    1: (1024, 750),
    2: (1024, 750),
    3: (768, 750),
    4: (1024, 704),
    5: (1024, 704),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one or more camera HDF5 files to video without loading the "
            "whole recording into memory. MKV + FFV1 is the recommended bit-exact default."
        )
    )
    parser.add_argument("inputs", nargs="+", help="H5 files, directories, or glob patterns")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output directory (default: beside each input)")
    parser.add_argument("--container", choices=("mkv", "avi", "mp4"), default="mkv")
    parser.add_argument("--fps", type=float, help="Output frame rate (default: infer from timestamps)")
    parser.add_argument("--width", type=int, help="Override frame width; requires --height")
    parser.add_argument("--height", type=int, help="Override frame height; requires --width")
    parser.add_argument("--dataset", default="images", help="Image dataset name (default: images)")
    parser.add_argument("--timestamps-dataset", default="timestamps")
    parser.add_argument("--frames", type=int, help="Convert at most this many valid frames")
    parser.add_argument("--chunk-frames", type=int, default=32, help="H5 frames read per chunk (default: 32)")
    parser.add_argument("--preset", default="medium", help="libx264 preset used for MP4 (default: medium)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable or full path")
    parser.add_argument("--plugin-dir", type=Path, help="Directory containing the HDF5 Zstd filter DLL")
    parser.add_argument("--timestamps-csv", action="store_true", help="Also export valid timestamp rows to CSV")
    parser.add_argument("--recursive", action="store_true", help="Search supplied directories recursively")
    parser.add_argument("-y", "--overwrite", action="store_true", help="Overwrite existing output files")
    args = parser.parse_args()

    if args.fps is not None and args.fps <= 0:
        parser.error("--fps must be positive")
    if args.chunk_frames <= 0:
        parser.error("--chunk-frames must be positive")
    if args.frames is not None and args.frames <= 0:
        parser.error("--frames must be positive")
    if (args.width is None) != (args.height is None):
        parser.error("--width and --height must be supplied together")
    if args.width is not None and (args.width <= 0 or args.height <= 0):
        parser.error("--width and --height must be positive")
    return args


def configure_hdf5_plugins(args: argparse.Namespace) -> None:
    candidates = []
    if args.plugin_dir:
        candidates.append(args.plugin_dir)
    candidates.extend((Path(__file__).resolve().parent, Path.cwd()))

    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*h5*zstd*.dll")):
            existing = os.environ.get("HDF5_PLUGIN_PATH")
            value = str(candidate.resolve())
            os.environ["HDF5_PLUGIN_PATH"] = value if not existing else value + os.pathsep + existing
            return


def resolve_inputs(specs: list[str], recursive: bool) -> list[Path]:
    resolved: list[Path] = []
    for spec in specs:
        path = Path(spec)
        if path.is_file():
            resolved.append(path)
        elif path.is_dir():
            pattern = "**/*.h5" if recursive else "*.h5"
            resolved.extend(path.glob(pattern))
        else:
            resolved.extend(Path(match) for match in glob.glob(spec, recursive=recursive))

    unique = []
    seen = set()
    for path in resolved:
        absolute = path.resolve()
        key = str(absolute).casefold()
        if absolute.is_file() and absolute.suffix.casefold() in (".h5", ".hdf5") and key not in seen:
            seen.add(key)
            unique.append(absolute)
    if not unique:
        raise ValueError("No .h5/.hdf5 input files found")
    return unique


def infer_dimensions(path: Path, pixels_per_frame: int, width: int | None, height: int | None) -> tuple[int, int]:
    if width is not None and height is not None:
        if width * height != pixels_per_frame:
            raise ValueError(
                f"--width x --height is {width * height}, but each H5 frame has {pixels_per_frame} pixels"
            )
        return width, height

    match = re.search(r"(?:^|_)cam([0-5])(?:_|$)", path.stem, flags=re.IGNORECASE)
    if match:
        dimensions = CAMERA_DIMENSIONS[int(match.group(1))]
        if dimensions[0] * dimensions[1] == pixels_per_frame:
            return dimensions

    matches = [(w, h) for w, h in CAMERA_DIMENSIONS.values() if w * h == pixels_per_frame]
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    raise ValueError(
        f"Cannot infer dimensions for {pixels_per_frame} pixels/frame. Supply --width and --height."
    )


def valid_frame_count(timestamp_dataset, total_frames: int) -> int:
    import numpy as np

    if timestamp_dataset is None:
        return total_frames
    timestamps = timestamp_dataset[:]
    if timestamps.ndim == 1:
        valid = np.isfinite(timestamps) & (timestamps != 0)
    else:
        valid = np.any(np.isfinite(timestamps) & (timestamps != 0), axis=1)
    invalid = np.flatnonzero(~valid)
    count = int(invalid[0]) if invalid.size else int(valid.size)
    return min(count, total_frames)


def infer_fps(timestamp_dataset, frame_count: int) -> float:
    import numpy as np

    if timestamp_dataset is None or frame_count < 2:
        return 178.0
    values = np.asarray(timestamp_dataset[:frame_count, 0] if timestamp_dataset.ndim > 1 else timestamp_dataset[:frame_count])
    values = values[np.isfinite(values)]
    if values.size < 2 or values[-1] <= values[0]:
        return 178.0
    # The end-to-end average is less biased by host-timestamp jitter than the
    # median of individual intervals, especially for short recordings.
    fps = float(values.size - 1) / float(values[-1] - values[0])
    return fps if 0.1 <= fps <= 10_000 else 178.0


def ffmpeg_command(args: argparse.Namespace, width: int, height: int, fps: float, output: Path) -> list[str]:
    command = [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel", "warning",
        "-y" if args.overwrite else "-n",
        "-f", "rawvideo",
        "-pixel_format", "gray",
        "-video_size", f"{width}x{height}",
        "-framerate", f"{fps:.12g}",
        "-i", "pipe:0",
        "-an",
    ]
    if args.container in ("mkv", "avi"):
        command.extend(("-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-slicecrc", "1"))
    else:
        command.extend(("-c:v", "libx264", "-preset", args.preset, "-qp", "0", "-pix_fmt", "gray"))
    command.append(str(output))
    return command


def export_timestamps(path: Path, dataset, frame_count: int) -> None:
    if dataset is None:
        print(f"  warning: no timestamps dataset; CSV not written", file=sys.stderr)
        return
    output = path.with_suffix(path.suffix + ".timestamps.csv")
    values = dataset[:frame_count]
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        columns = values.shape[1] if values.ndim > 1 else 1
        header = ["t_host", "writer_count", "hw_frame_id"][:columns]
        header.extend(f"timestamp_{i}" for i in range(len(header), columns))
        writer.writerow(header)
        writer.writerows(values if values.ndim > 1 else ((value,) for value in values))


def convert(path: Path, args: argparse.Namespace, h5py) -> Path:
    output_dir = args.output_dir.resolve() if args.output_dir else path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{path.stem}.{args.container}"
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists (use -y to overwrite): {output}")

    with h5py.File(path, "r") as h5:
        if args.dataset not in h5:
            raise KeyError(f"Dataset {args.dataset!r} not found; available: {list(h5.keys())}")
        images = h5[args.dataset]
        if images.dtype.kind != "u" or images.dtype.itemsize != 1:
            raise ValueError(f"Expected uint8 images, found {images.dtype}")
        if images.ndim == 2:
            total_frames, pixels_per_frame = map(int, images.shape)
            width, height = infer_dimensions(path, pixels_per_frame, args.width, args.height)
        elif images.ndim == 3:
            total_frames, height, width = map(int, images.shape)
            if args.width is not None and (width != args.width or height != args.height):
                raise ValueError(f"H5 dimensions are {width}x{height}, not {args.width}x{args.height}")
        else:
            raise ValueError(f"Expected a rank-2 or rank-3 image dataset, found shape {images.shape}")

        timestamps = h5.get(args.timestamps_dataset)
        available_frames = valid_frame_count(timestamps, total_frames)
        fps = args.fps or infer_fps(timestamps, available_frames)
        frame_count = available_frames
        if args.frames is not None:
            frame_count = min(frame_count, args.frames)
        if frame_count == 0:
            raise ValueError("No written frames found")

        codec = "FFV1 (bit-exact lossless)" if args.container in ("mkv", "avi") else "H.264 lossless mode"
        print(f"{path}")
        print(f"  {frame_count} frames, {width}x{height} gray8, {fps:.6f} fps -> {output.name} [{codec}]")

        process = subprocess.Popen(ffmpeg_command(args, width, height, fps, output), stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            for start in range(0, frame_count, args.chunk_frames):
                stop = min(start + args.chunk_frames, frame_count)
                block = images[start:stop]
                process.stdin.write(block.tobytes(order="C"))
            process.stdin.close()
            return_code = process.wait()
        except BrokenPipeError:
            return_code = process.wait()
        if return_code != 0:
            if output.exists():
                output.unlink()
            raise RuntimeError(f"ffmpeg failed with exit code {return_code}")

        if args.timestamps_csv:
            export_timestamps(output, timestamps, frame_count)
    return output


def main() -> int:
    args = parse_args()
    configure_hdf5_plugins(args)
    try:
        import h5py
        import numpy  # noqa: F401 - validated here for clearer dependency errors
    except ImportError as error:
        print(f"Missing Python dependency: {error}. Install h5py and numpy.", file=sys.stderr)
        return 2

    if not shutil.which(args.ffmpeg) and not Path(args.ffmpeg).is_file():
        print(f"ffmpeg not found: {args.ffmpeg}", file=sys.stderr)
        return 2

    try:
        inputs = resolve_inputs(args.inputs, args.recursive)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    failures = 0
    for path in inputs:
        try:
            convert(path, args, h5py)
        except Exception as error:
            failures += 1
            print(f"ERROR: {path}: {error}", file=sys.stderr)
    print(f"Converted {len(inputs) - failures}/{len(inputs)} file(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
