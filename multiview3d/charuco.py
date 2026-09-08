from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class BoardSpec:
    squares_x: int = 45
    squares_y: int = 5
    square_mm: float = 4.0
    marker_mm: float = 2.8
    dictionary: str = "DICT_4X4_250"
    first_marker_id: int = 0
    last_marker_id: int = 111

    @property
    def dimensions_mm(self) -> tuple[float, float]:
        return self.squares_x * self.square_mm, self.squares_y * self.square_mm

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"dimensions_mm": list(self.dimensions_mm)}


BOARD_SPEC = BoardSpec()


def make_board(spec: BoardSpec = BOARD_SPEC):
    import cv2

    dictionary_id = getattr(cv2.aruco, spec.dictionary)
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    return cv2.aruco.CharucoBoard(
        (spec.squares_x, spec.squares_y),
        spec.square_mm,
        spec.marker_mm,
        dictionary,
    )


def detect_charuco(image: np.ndarray, spec: BoardSpec = BOARD_SPEC) -> dict[str, Any]:
    """Detect the exact rig target without resizing the input image."""
    import cv2

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    elif image.ndim == 2:
        gray = image
    else:
        raise ValueError(f"expected a 2-D/3-D image, got shape {image.shape}")
    detector = cv2.aruco.CharucoDetector(make_board(spec))
    corners, ids, marker_corners, marker_ids = detector.detectBoard(gray)
    ids_flat = [] if ids is None else [int(value) for value in ids.reshape(-1)]
    marker_flat = [] if marker_ids is None else [int(value) for value in marker_ids.reshape(-1)]
    points = np.empty((0, 2), np.float32) if corners is None else corners.reshape(-1, 2)
    coverage = 0.0
    if len(points) >= 3:
        coverage = float(cv2.contourArea(cv2.convexHull(points.astype(np.float32))) /
                         (gray.shape[0] * gray.shape[1]))
    centroid = None if not len(points) else [float(v) for v in points.mean(axis=0)]
    return {
        "image_size": [int(gray.shape[1]), int(gray.shape[0])],
        "charuco_corner_count": len(ids_flat),
        "charuco_ids": ids_flat,
        "marker_count": len(marker_flat),
        "marker_ids": marker_flat,
        "coverage_fraction": coverage,
        "centroid_px": centroid,
        "corners": points,
        "marker_corners": marker_corners,
    }
