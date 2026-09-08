from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .charuco import BOARD_SPEC, detect_charuco

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
ADJACENT_PAIRS = ((0, 1), (1, 2), (2, 3))


def _images(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def validate_session(root: str | Path, *, min_corners: int = 12) -> dict:
    """Validate synchronized, original-resolution ChArUco image folders.

    Expected layout: ROOT/cam0, ROOT/cam1, ROOT/cam2, ROOT/cam3. Matching file
    stems are treated as simultaneous observations.
    """
    import cv2

    root = Path(root)
    cameras: dict[str, dict] = {}
    valid_stems: dict[int, set[str]] = {}
    failures: list[str] = []
    for position in range(4):
        name = f"cam{position}"
        directory = root / name
        if not directory.is_dir():
            failures.append(f"missing directory: {directory}")
            cameras[name] = {"images": 0, "valid": 0, "sizes": []}
            valid_stems[position] = set()
            continue
        rows = []
        sizes: Counter[tuple[int, int]] = Counter()
        valid: set[str] = set()
        for path in _images(directory):
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None:
                rows.append({"file": path.name, "error": "unreadable"})
                continue
            result = detect_charuco(image)
            size = tuple(result["image_size"])
            sizes[size] += 1
            ok = result["charuco_corner_count"] >= min_corners
            if ok:
                valid.add(path.stem)
            rows.append({
                "file": path.name,
                "size": list(size),
                "corners": result["charuco_corner_count"],
                "markers": result["marker_count"],
                "coverage_fraction": result["coverage_fraction"],
                "centroid_px": result["centroid_px"],
                "valid": ok,
            })
        if len(sizes) > 1:
            failures.append(f"{name}: mixed image sizes (resize/crop changed): {dict(sizes)}")
        if not valid:
            failures.append(f"{name}: no images with at least {min_corners} ChArUco corners")
        cameras[name] = {
            "images": len(rows),
            "valid": len(valid),
            "sizes": [{"width": key[0], "height": key[1], "count": value}
                      for key, value in sorted(sizes.items())],
            "frames": rows,
        }
        valid_stems[position] = valid

    pair_counts = {}
    for left, right in ADJACENT_PAIRS:
        common = sorted(valid_stems[left] & valid_stems[right])
        pair_counts[f"cam{left}-cam{right}"] = {
            "simultaneous_valid_frames": len(common),
            "frame_stems": common,
        }
        if len(common) < 15:
            failures.append(
                f"cam{left}-cam{right}: only {len(common)} simultaneous valid frames; need >=15")

    return {
        "schema_version": 1,
        "branch": "multiview3d",
        "board": BOARD_SPEC.as_dict(),
        "session_root": str(root.resolve()),
        "minimum_corners_per_image": int(min_corners),
        "cameras": cameras,
        "adjacent_pairs": pair_counts,
        "passed": not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate exact-board synchronized multi-view calibration images")
    parser.add_argument("session", help="folder containing cam0..cam3")
    parser.add_argument("--output", default="multiview3d_validation.json")
    parser.add_argument("--min-corners", type=int, default=12)
    args = parser.parse_args()
    report = validate_session(args.session, min_corners=args.min_corners)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "output": str(output.resolve()),
        "failures": report["failures"],
    }, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
