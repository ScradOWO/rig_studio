"""SPSC circular frame pool. Producer never blocks (MEX policy): a full ring
or an over-cap queue depth counts a drop instead of stalling the fetch loop.
head/tail are monotonically increasing Python ints (GIL-atomic reads)."""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class FrameRing:
    def __init__(self, capacity: int, frame_bytes: int, *,
                 queue_cap: int = 4096, drop_log_every: int = 500) -> None:
        self.capacity = int(capacity)
        self.frame_bytes = int(frame_bytes)
        self.pool = np.empty((self.capacity, self.frame_bytes), np.uint8)
        self.pool[::64] = 0  # touch pages so commit happens before recording
        self.meta = np.empty((self.capacity, 3), np.float64)  # t_host, hw_id, spare
        self.head = 0  # producer-owned
        self.tail = 0  # consumer-owned
        self.dropped = 0
        self._queue_cap = min(int(queue_cap), self.capacity)
        self._drop_log_every = int(drop_log_every)

    @property
    def depth(self) -> int:
        return self.head - self.tail

    def claim(self) -> int | None:
        """Producer: slot index to write into, or None (counted drop)."""
        if self.head - self.tail >= self._queue_cap:
            self.dropped += 1
            if self.dropped % self._drop_log_every == 1:
                log.warning("frame drop #%d (ring depth %d)", self.dropped, self.depth)
            return None
        return self.head % self.capacity

    def commit(self) -> None:
        self.head += 1

    def peek(self, max_n: int) -> tuple[np.ndarray, np.ndarray]:
        """Consumer: contiguous views of up to max_n pending frames (may be empty)."""
        n = min(self.head - self.tail, max_n)
        index = self.tail % self.capacity
        n = min(n, self.capacity - index)  # stop at the wrap point
        return self.pool[index:index + n], self.meta[index:index + n]

    def release(self, n: int) -> None:
        self.tail += n
