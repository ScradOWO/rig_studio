import subprocess
import sys

from multiview3d.charuco import BOARD_SPEC


def test_exact_board_definition_and_detection():
    assert BOARD_SPEC.dimensions_mm == (180.0, 20.0)
    code = """
from multiview3d.charuco import detect_charuco, make_board
board = make_board()
image = board.generateImage((1800, 200), marginSize=0, borderBits=1)
result = detect_charuco(image)
assert result['marker_count'] == 112, result['marker_count']
assert min(result['marker_ids']) == 0
assert max(result['marker_ids']) == 111
assert result['charuco_corner_count'] > 100
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
