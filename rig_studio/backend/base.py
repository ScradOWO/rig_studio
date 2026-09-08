# Vendored from racestrip/racestrip/backend/base.py @ 2026-08-17.
# LOCAL: added node_read_nocache() default (GS3 firmware serves stale cached values).
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray


class FrameGapError(RuntimeError):
    """Raised when a hardware-triggered frame sequence is not contiguous."""


@dataclass(frozen=True, slots=True)
class Frame:
    image: NDArray[np.generic]
    frame_id: int
    timestamp_ns: int
    exposure_us: float


Frames = tuple[Frame, ...]


class CamHandle(ABC):
    @abstractmethod
    def node_read(self, name: str) -> Any: ...

    def node_read_nocache(self, name: str) -> Any:
        return self.node_read(name)

    @abstractmethod
    def node_write(self, name: str, value: Any) -> None: ...

    @abstractmethod
    def nodemap_dump(self) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    def begin_acquisition(self, buffers: int) -> None: ...

    @abstractmethod
    def grab(self, n: int, timeout_s: float) -> Frames: ...

    def grab_into(self, destination: NDArray[np.uint8], timeout_s: float) -> Frame:
        """Fetch one frame into caller-owned contiguous uint8 storage.

        Backends should override this to avoid an intermediate allocation. The
        compatibility implementation preserves the existing grab() contract.
        """
        frame = self.grab(1, timeout_s)[0]
        target = np.asarray(destination)
        if target.dtype != np.uint8 or not target.flags.c_contiguous or not target.flags.writeable:
            raise ValueError("destination must be writable, C-contiguous uint8 storage")
        if target.size != frame.image.size:
            raise ValueError(
                f"destination has {target.size} bytes, frame has {frame.image.size}"
            )
        np.copyto(target.reshape(frame.image.shape), frame.image, casting="no")
        return Frame(
            image=target.reshape(frame.image.shape),
            frame_id=frame.frame_id,
            timestamp_ns=frame.timestamp_ns,
            exposure_us=frame.exposure_us,
        )

    @abstractmethod
    def discard_pending(self, timeout_s: float) -> int:
        """Discard frames already awaiting delivery; return the count."""
        ...

    @abstractmethod
    def end_acquisition(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class CamBackend(ABC):
    @abstractmethod
    def enumerate(self) -> list[str]: ...

    @abstractmethod
    def open(self, serial: str) -> CamHandle: ...

    @abstractmethod
    def close(self) -> None: ...
