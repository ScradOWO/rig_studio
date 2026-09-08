"""Isolated parametric multi-view calibration tools.

This package never participates in the primary TPS surface mapping branch.
"""

from .charuco import BOARD_SPEC, BoardSpec, detect_charuco, make_board

__all__ = ["BOARD_SPEC", "BoardSpec", "detect_charuco", "make_board"]
