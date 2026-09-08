from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from rig_studio.config import RigConfig

STALL_S = 2.0
HANDLE_PX = 8       # grab tolerance / handle size on screen
MIN_FRAC = 0.02     # smallest crop-drag rect, as a fraction of the reference

_DRAG_CURSORS = {
    "move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor,
    "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor,
}


def rotate_rect(rect: tuple[float, float, float, float], rotation: int
                ) -> tuple[float, float, float, float]:
    """Map normalized raw-image rectangle coordinates into rotated display coordinates."""
    x, y, w, h = rect
    if rotation == 0:
        return rect
    if rotation == 90:
        return (1.0 - y - h, x, h, w)
    if rotation == 180:
        return (1.0 - x - w, 1.0 - y - h, w, h)
    if rotation == 270:
        return (y, 1.0 - x - w, h, w)
    raise ValueError(f"unsupported preview rotation: {rotation}")


def drag_rect(mode: str, rect0: tuple[float, float, float, float],
              dx: float, dy: float,
              min_frac: float = MIN_FRAC) -> tuple[float, float, float, float]:
    """New overlay rect for a drag of (dx, dy) fractions in the given mode
    ('move' or any of l/r/t/b edge flags)."""
    x, y, w, h = rect0
    if mode == "move":
        return (x + dx, y + dy, w, h)
    if "l" in mode:
        dx = min(dx, w - min_frac)
        x, w = x + dx, w - dx
    if "r" in mode:
        w = max(w + dx, min_frac)
    if "t" in mode:
        dy = min(dy, h - min_frac)
        y, h = y + dy, h - dy
    if "b" in mode:
        h = max(h + dy, min_frac)
    return (x, y, w, h)


class CamTile(QWidget):
    clicked = Signal(int)
    crop_dragged = Signal(int, object)  # (position, rect fractions) on release

    def __init__(self, position: int, serial: str, label: str,
                 preview_rotation: int = 0) -> None:
        super().__init__()
        self.position = position
        self.serial = serial
        self.cam_label = label
        self.preview_rotation = preview_rotation
        self.auto_contrast = False
        self.display_gamma = 1.0
        self.view = QLabel("no signal")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(240, 170)
        self.view.setStyleSheet("background:#111; color:#666;")
        self.caption = QLabel(self._caption())
        self.caption.setStyleSheet("font-family:Consolas; font-size:11px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.caption)
        self.last_frame_t = 0.0
        self.last_status: dict = {}
        self.selected = False
        self.last_image: np.ndarray | None = None
        self.reference_image: np.ndarray | None = None
        self.reference_frozen = False
        self.crop_overlay: tuple[float, float, float, float] | None = None
        self._drag: tuple[str, float, float, tuple] | None = None
        self.setMouseTracking(True)
        self.view.setMouseTracking(True)

    def _caption(self, fps: float = 0.0, frame_id: int = 0, note: str = "") -> str:
        return (f"cam{self.position} {self.cam_label} [{self.serial}]  "
                f"{fps:5.1f} fps  id {frame_id}  {note}")

    def _pixmap_geometry(self) -> tuple[float, float, int, int] | None:
        """(x0, y0, width, height) of the displayed pixmap in tile coords."""
        pixmap = self.view.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        return (self.view.x() + (self.view.width() - pixmap.width()) / 2,
                self.view.y() + (self.view.height() - pixmap.height()) / 2,
                pixmap.width(), pixmap.height())

    def _hit(self, event) -> tuple[str, float, float] | None:
        """Crop-drag mode + mouse position in reference fractions, or None."""
        if not (self.reference_frozen and self.crop_overlay):
            return None
        geo = self._pixmap_geometry()
        if geo is None:
            return None
        gx, gy, pw, ph = geo
        fx = (event.position().x() - gx) / pw
        fy = (event.position().y() - gy) / ph
        tx, ty = HANDLE_PX / pw, HANDLE_PX / ph
        x, y, w, h = self.crop_overlay
        in_y = y - ty <= fy <= y + h + ty
        in_x = x - tx <= fx <= x + w + tx
        on_l = abs(fx - x) <= tx and in_y
        on_r = abs(fx - (x + w)) <= tx and in_y
        on_t = abs(fy - y) <= ty and in_x
        on_b = abs(fy - (y + h)) <= ty and in_x
        mode = (("l" if on_l else "r" if on_r else "")
                + ("t" if on_t else "b" if on_b else ""))
        if not mode and x <= fx <= x + w and y <= fy <= y + h:
            mode = "move"
        return (mode, fx, fy) if mode else None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.position)
        hit = self._hit(event)
        if hit is not None:
            mode, fx, fy = hit
            self._drag = (mode, fx, fy, self.crop_overlay)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            geo = self._pixmap_geometry()
            if geo is None:
                return
            gx, gy, pw, ph = geo
            mode, fx0, fy0, rect0 = self._drag
            fx = (event.position().x() - gx) / pw
            fy = (event.position().y() - gy) / ph
            self.crop_overlay = drag_rect(mode, rect0, fx - fx0, fy - fy0)
            if self.reference_image is not None:
                self._show_image(self.reference_image)
            return
        hit = self._hit(event)
        self.setCursor(_DRAG_CURSORS.get(hit[0], Qt.ArrowCursor) if hit
                       else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            if self.crop_overlay is not None:
                raw_rect = rotate_rect(
                    self.crop_overlay, (360 - self.preview_rotation) % 360)
                self.crop_dragged.emit(self.position, raw_rect)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        color = "#4af" if selected else "#333"
        self.setStyleSheet(f"CamTile {{ border: 1px solid {color}; }}")

    def update_frame(self, preview: dict) -> None:
        image: np.ndarray = preview["image"]
        self.last_image = image.copy()
        if not self.reference_frozen:
            self._show_image(image)
        self.last_frame_t = time.monotonic()
        note = self._status_note()
        self.caption.setText(self._caption(preview.get("fps", 0.0),
                                           preview.get("frame_id", 0), note))

    def _show_image(self, image: np.ndarray) -> None:
        image = self.display_image(image)
        height, width = image.shape
        qimage = QImage(image.data, width, height, width, QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimage).scaled(
            self.view.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
        if self.reference_frozen and self.crop_overlay is not None:
            x, y, w, h = self.crop_overlay  # fractions of the reference frame
            X, Y = x * pixmap.width(), y * pixmap.height()
            W, H = w * pixmap.width(), h * pixmap.height()
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor("#f50"), 2))
            painter.drawRect(int(X), int(Y), int(W), int(H))
            painter.setBrush(QColor("#f50"))  # corner + edge-midpoint handles
            half = HANDLE_PX // 2
            for i in (0, 1, 2):
                for j in (0, 1, 2):
                    if i == 1 and j == 1:
                        continue
                    painter.drawRect(int(X + W * i / 2 - half),
                                     int(Y + H * j / 2 - half),
                                     HANDLE_PX, HANDLE_PX)
            painter.end()
        self.view.setPixmap(pixmap)

    def display_image(self, image: np.ndarray) -> np.ndarray:
        """Apply display-only levels/gamma and orientation; raw pixels stay unchanged."""
        displayed = np.asarray(image, dtype=np.uint8)
        if self.auto_contrast:
            histogram = np.bincount(displayed.reshape(-1), minlength=256)
            cumulative = np.cumsum(histogram)
            total = int(cumulative[-1])
            if total:
                lo = int(np.searchsorted(cumulative, total * 0.01))
                hi = int(np.searchsorted(cumulative, total * 0.995))
                if hi > lo:
                    lut = np.clip((np.arange(256) - lo) * 255.0 / (hi - lo),
                                  0, 255)
                    displayed = lut[displayed].astype(np.uint8)
        if self.display_gamma != 1.0:
            lut = np.round(255.0 * (np.arange(256) / 255.0) **
                           (1.0 / self.display_gamma)).astype(np.uint8)
            displayed = lut[displayed]
        if self.preview_rotation:
            displayed = np.rot90(displayed, -(self.preview_rotation // 90))
        return np.ascontiguousarray(displayed)

    def set_display_options(self, auto_contrast: bool, gamma: float) -> None:
        self.auto_contrast = bool(auto_contrast)
        self.display_gamma = float(gamma)
        image = self.reference_image if self.reference_frozen else self.last_image
        if image is not None:
            self._show_image(image)

    def set_reference_frozen(self, frozen: bool) -> bool:
        """Returns True if a reference frame is being shown; refuses (and stays
        live) when no frame has been received yet."""
        if frozen:
            if self.last_image is None:
                self.reference_frozen = False
                return False
            self.reference_frozen = True
            self.reference_image = self.last_image.copy()
            self._show_image(self.reference_image)
            return True
        self.reference_frozen = False
        self.reference_image = None
        self.crop_overlay = None
        if self.last_image is not None:
            self._show_image(self.last_image)
        return False

    def set_crop_overlay(self, rect: tuple[float, float, float, float] | None) -> None:
        """rect as fractions of the reference frame; redraws the frozen reference.
        Ignored mid-drag so panel refreshes don't fight the user's mouse."""
        if self._drag is not None:
            return
        self.crop_overlay = (None if rect is None else
                             rotate_rect(rect, self.preview_rotation))
        if self.reference_frozen and self.reference_image is not None:
            self._show_image(self.reference_image)

    def _status_note(self) -> str:
        status = self.last_status
        notes = []
        if status.get("recording"):
            notes.append(f"REC {status.get('frames_written', 0)}")
        elif status.get("armed"):
            notes.append("ARMED")
        if status.get("dropped"):
            notes.append(f"DROP {status['dropped']}")
        if self.last_frame_t and time.monotonic() - self.last_frame_t > STALL_S:
            notes.append("STALLED")
        return " ".join(notes)

    def tick(self) -> None:
        if self.last_frame_t and time.monotonic() - self.last_frame_t > STALL_S:
            self.caption.setText(self._caption(0.0, 0, self._status_note()))


class CameraGrid(QWidget):
    tile_selected = Signal(int)
    crop_dragged = Signal(int, object)

    def __init__(self, config: RigConfig) -> None:
        super().__init__()
        self.config = config
        self._layout = QGridLayout(self)
        self._layout.setSpacing(4)
        self.tiles: dict[int, CamTile] = {}
        for cam in sorted(config.cameras, key=lambda c: c.position):
            tile = CamTile(cam.position, cam.serial, cam.label,
                           cam.preview_rotation)
            tile.clicked.connect(self._on_tile_clicked)
            tile.crop_dragged.connect(self.crop_dragged.emit)
            self.tiles[cam.position] = tile
        self._visible_positions = set(config.gui.default_view_positions)
        self._strip = False
        self._rebuild()

    def set_strip(self, strip: bool) -> None:
        """strip: top 4 cams side by side in readable (canvas) order, sides below."""
        self._strip = bool(strip)
        self._rebuild()

    def set_visible_positions(self, positions: set[int]) -> None:
        unknown = set(positions) - set(self.tiles)
        if unknown:
            raise ValueError(f"unknown camera positions: {sorted(unknown)}")
        self._visible_positions = set(positions)
        self._rebuild()

    @property
    def visible_positions(self) -> set[int]:
        return set(self._visible_positions)

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for tile in self.tiles.values():
            tile.hide()
        if self._strip:
            # top cameras only, side by side in readable order; side cams hidden
            top_order = [
                position for position in self.config.gui.strip_top_order
                if position in self._visible_positions
            ]
            for column, position in enumerate(top_order):
                self._layout.addWidget(self.tiles[position], 0, column)
                self.tiles[position].show()
            self._layout.setRowStretch(0, 1)
            self._layout.setRowStretch(1, 0)
        else:
            cols = self.config.gui.grid_cols
            visible = sorted(self._visible_positions)
            for index, position in enumerate(visible):
                self._layout.addWidget(self.tiles[position], index // cols, index % cols)
                self.tiles[position].show()
            self._layout.setRowStretch(0, 1)
            self._layout.setRowStretch(1, 1)

    def _on_tile_clicked(self, position: int) -> None:
        for tile in self.tiles.values():
            tile.set_selected(tile.position == position)
        self.tile_selected.emit(position)

    def update_status(self, event: dict) -> None:
        tile = self.tiles.get(event.get("position"))
        if tile is not None:
            tile.last_status = event

    def set_reference_frozen(self, position: int, frozen: bool) -> bool:
        return self.tiles[position].set_reference_frozen(frozen)

    def set_crop_overlay(self, position: int,
                         rect: tuple[float, float, float, float] | None) -> None:
        self.tiles[position].set_crop_overlay(rect)

    def latest_image(self, position: int) -> np.ndarray | None:
        image = self.tiles[position].last_image
        return None if image is None else image.copy()

    def latest_display_image(self, position: int) -> np.ndarray | None:
        image = self.tiles[position].last_image
        return None if image is None else self.tiles[position].display_image(image)

    def set_display_options(self, position: int, auto_contrast: bool,
                            gamma: float) -> None:
        self.tiles[position].set_display_options(auto_contrast, gamma)

    def tick(self) -> None:
        for tile in self.tiles.values():
            tile.tick()
