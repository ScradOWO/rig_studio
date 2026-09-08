"""perf_counter <-> POSIX offset. perf_counter is QPC-backed on Windows and
shared by all processes on the machine, but each process has its own epoch;
the bracketing estimate below agrees across processes to well under 1 ms
(<< the 5.6 ms trigger period), so an absolute-POSIX write gate lands every
camera's first recorded frame on the same trigger edge."""
from __future__ import annotations

import time


def posix_offset_ns(samples: int = 15) -> int:
    """offset such that posix_ns ~= perf_counter_ns() + offset."""
    best_bracket = None
    best_offset = 0
    for _ in range(max(3, samples)):
        a = time.perf_counter_ns()
        posix = time.time_ns()
        b = time.perf_counter_ns()
        bracket = b - a
        if best_bracket is None or bracket < best_bracket:
            best_bracket = bracket
            best_offset = posix - (a + b) // 2
    return best_offset


def perf_ns_at_posix(posix_ns: int, offset_ns: int) -> int:
    return posix_ns - offset_ns


def t_host_s(perf_ns: int, offset_ns: int, origin_posix_ns: int) -> float:
    """Seconds from the shared session origin (the gate time)."""
    return (perf_ns + offset_ns - origin_posix_ns) / 1e9
