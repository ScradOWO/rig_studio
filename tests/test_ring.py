from __future__ import annotations

import numpy as np

from rig_studio.recorder.clockcal import posix_offset_ns, t_host_s
from rig_studio.recorder.ring import FrameRing


def _put(ring: FrameRing, value: int) -> bool:
    slot = ring.claim()
    if slot is None:
        return False
    ring.pool[slot] = value
    ring.meta[slot] = (float(value), float(value), 0.0)
    ring.commit()
    return True


def test_fifo_and_wraparound():
    ring = FrameRing(4, 8)
    for value in range(3):
        assert _put(ring, value)
    images, meta = ring.peek(10)
    assert len(images) == 3
    assert [int(row[0]) for row in images] == [0, 1, 2]
    ring.release(3)
    # wrap: slots 3, 0, 1
    for value in (10, 11, 12):
        assert _put(ring, value)
    images, meta = ring.peek(10)
    assert len(images) == 1  # contiguous run stops at the wrap point
    assert int(images[0][0]) == 10
    ring.release(1)
    images, _ = ring.peek(10)
    assert [int(row[0]) for row in images] == [11, 12]


def test_producer_never_blocks_and_counts_drops():
    ring = FrameRing(4, 8, queue_cap=2, drop_log_every=1)
    assert _put(ring, 0)
    assert _put(ring, 1)
    assert not _put(ring, 2)  # over queue_cap -> dropped, not blocked
    assert ring.dropped == 1
    ring.release(1)
    assert _put(ring, 3)
    assert ring.dropped == 1


def test_clockcal_offset_sane():
    import time

    offset = posix_offset_ns()
    now_via_offset = time.perf_counter_ns() + offset
    assert abs(now_via_offset - time.time_ns()) < 5_000_000  # < 5 ms
    origin = time.time_ns()
    t = t_host_s(time.perf_counter_ns(), offset, origin)
    assert abs(t) < 0.1
