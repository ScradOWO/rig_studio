"""Convert rig-studio H5 recordings (MEX layout) to mp4 for labeling/tracking.

    python h5_to_mp4.py --tag run_20260825_163407
    python h5_to_mp4.py --tag <TAG> --out <folder> --crf 16

Reads /images [N, W*H] + attrs, trims to the valid-frame prefix
(timestamps writer_seq > 0), computes true fps from t_host, and pipes
grayscale rawvideo into ffmpeg (h264, yuv420p for maximum player/SLEAP
compatibility).
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import h5py
import numpy as np

ROOTS = {0: r"D:\h5_rec_cam0", 1: r"C:\h5_rec_cam1", 2: r"D:\h5_rec_cam2",
         3: r"C:\h5_rec_cam3", 4: r"D:\h5_rec_cam4", 5: r"C:\h5_rec_cam5"}
CHUNK = 64


def convert(h5_path: Path, out_path: Path, crf: int) -> str:
    with h5py.File(h5_path, "r") as f:
        width = int(f.attrs["image_width"])
        height = int(f.attrs["image_height"])
        ts = f["timestamps"][:]
        valid = ts[:, 1] > 0
        n = int(valid.sum())
        if n < 2:
            return f"SKIP {h5_path.name}: no valid frames"
        t = ts[:n, 0]
        fps = (n - 1) / max(t[-1] - t[0], 1e-9)
        if crf <= 0:  # mathematically lossless (lab-standard: x264 qp 0, gray)
            encode = ["-c:v", "libx264", "-preset", "medium", "-qp", "0",
                      "-pix_fmt", "gray"]
        else:
            encode = ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
                      "-pix_fmt", "yuv420p"]
        cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "gray",
               "-video_size", f"{width}x{height}", "-framerate", f"{fps:.6f}",
               "-i", "-", *encode, str(out_path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        images = f["images"]
        for start in range(0, n, CHUNK):
            block = images[start:min(start + CHUNK, n)]
            proc.stdin.write(np.ascontiguousarray(block, np.uint8).tobytes())
        proc.stdin.close()
        proc.wait()
        if proc.returncode:
            return f"FAIL {h5_path.name}: ffmpeg exit {proc.returncode}"
    size_mb = out_path.stat().st_size / 1e6
    return (f"OK {out_path.name}: {n} frames, {fps:.2f} fps, "
            f"{width}x{height}, {size_mb:.0f} MB")


def latest_tag() -> str | None:
    """Newest run tag across all camera roots (by file mtime)."""
    best = None
    for cam, root in ROOTS.items():
        for h5_path in Path(root).glob(f"*_cam{cam}.h5"):
            stem = h5_path.stem
            if not stem.endswith(f"_cam{cam}"):
                continue
            tag = stem[: -len(f"_cam{cam}")]
            if tag.startswith("charuco"):
                continue  # calibration captures are not trials
            mtime = h5_path.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, tag)
    return best[1] if best else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=None,
                        help="capture tag; omit to auto-convert the NEWEST run")
    parser.add_argument("--out", default=None,
                        help="output folder (default Desktop\\Trials\\<tag>)")
    parser.add_argument("--crf", type=int, default=0,
                        help="0 (default) = mathematically LOSSLESS (qp 0, gray); "
                             ">0 = lossy CRF for small share/preview copies")
    args = parser.parse_args()
    tag = args.tag or latest_tag()
    if tag is None:
        print("no run recordings found")
        return
    print(f"converting run: {tag}")
    out_dir = Path(args.out) if args.out else \
        Path.home() / "Desktop" / "Trials" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    for cam, root in ROOTS.items():
        h5_path = Path(root) / f"{tag}_cam{cam}.h5"
        out_path = out_dir / f"{tag}_cam{cam}.mp4"
        if not h5_path.is_file():
            print(f"MISSING {h5_path}")
            continue
        if out_path.is_file() and out_path.stat().st_mtime > h5_path.stat().st_mtime:
            print(f"SKIP {out_path.name}: already converted")
            continue
        print(convert(h5_path, out_path, args.crf), flush=True)
    print(f"done -> {out_dir}")


if __name__ == "__main__":
    main()
