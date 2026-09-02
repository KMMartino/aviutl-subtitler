"""Developer-only Windows process-memory sampling for alignment benchmarks."""

from __future__ import annotations

import csv
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ProcessMemorySnapshot:
    working_set_bytes: int
    private_working_set_bytes: int
    peak_working_set_bytes: int
    commit_bytes: int
    peak_commit_bytes: int


def read_process_memory() -> ProcessMemorySnapshot:
    if os.name != "nt":
        return ProcessMemorySnapshot(0, 0, 0, 0, 0)

    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCountersEx2(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
            ("private_working_set_size", ctypes.c_size_t),
            ("shared_commit_usage", ctypes.c_ulonglong),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    counters = ProcessMemoryCountersEx2()
    counters.cb = ctypes.sizeof(counters)
    succeeded = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not succeeded:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "GetProcessMemoryInfo failed")
    return ProcessMemorySnapshot(
        working_set_bytes=int(counters.working_set_size),
        private_working_set_bytes=int(counters.private_working_set_size),
        peak_working_set_bytes=int(counters.peak_working_set_size),
        commit_bytes=int(counters.private_usage),
        peak_commit_bytes=int(counters.peak_pagefile_usage),
    )


class AlignmentMemoryProfiler:
    def __init__(
        self,
        output_path: Path | None,
        *,
        interval_sec: float = 0.2,
        memory_reader: Callable[[], ProcessMemorySnapshot] = read_process_memory,
    ) -> None:
        self.output_path = output_path
        self.interval_sec = interval_sec
        self.memory_reader = memory_reader
        self._started = time.perf_counter()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker_phases: dict[int, tuple[str, int, str]] = {}
        self._rows: list[dict[str, object]] = []
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.output_path is not None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self.checkpoint("pool_created")
        self._thread = threading.Thread(target=self._sample_loop, name="alignment-memory", daemon=True)
        self._thread.start()

    def phase(self, phase: str, chunk_index: int) -> None:
        if not self.enabled:
            return
        thread_id = threading.get_ident()
        thread_name = threading.current_thread().name
        with self._lock:
            if phase == "idle":
                self._worker_phases.pop(thread_id, None)
            else:
                self._worker_phases[thread_id] = (thread_name, chunk_index, phase)
            self._record_locked("phase", thread_name, chunk_index, phase)

    def checkpoint(self, event: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._record_locked(event, threading.current_thread().name, -1, "")

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_sec * 5))
        self.checkpoint("pool_closed")
        self._write()

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self.checkpoint("sample")

    def _record_locked(self, event: str, thread_name: str, chunk_index: int, phase: str) -> None:
        try:
            memory = self.memory_reader()
        except (AttributeError, OSError, ValueError):
            return
        active_workers = sum(1 for name, _, _ in self._worker_phases.values() if _is_alignment_worker(name))
        emission_workers = sum(
            1
            for name, _, worker_phase in self._worker_phases.values()
            if _is_alignment_worker(name) and worker_phase == "generate_emissions"
        )
        self._rows.append(
            {
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
                "event": event,
                "thread": thread_name,
                "chunk_index": "" if chunk_index < 0 else chunk_index,
                "phase": phase,
                "active_alignment_workers": active_workers,
                "emission_workers": emission_workers,
                "working_set_bytes": memory.working_set_bytes,
                "private_working_set_bytes": memory.private_working_set_bytes,
                "peak_working_set_bytes": memory.peak_working_set_bytes,
                "commit_bytes": memory.commit_bytes,
                "peak_commit_bytes": memory.peak_commit_bytes,
            }
        )

    def _write(self) -> None:
        if self.output_path is None or not self._rows:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self._rows[0]))
            writer.writeheader()
            writer.writerows(self._rows)
        print(f"Wrote alignment memory profile: {self.output_path}", flush=True)


def _is_alignment_worker(thread_name: str) -> bool:
    prefix = "aligner-"
    return thread_name.startswith(prefix) and thread_name[len(prefix) :].isdigit()
