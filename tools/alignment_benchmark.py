"""Replay captured alignment jobs with controlled worker counts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from subtitler.aligner import ForcedAligner
from subtitler.alignment_pool import AlignmentConfig, AlignmentPool
from subtitler.models import AlignedChunk, AudioChunk, TranscriptChunk
from subtitler.profiling import PipelineProfiler, now
from tools.alignment_memory import AlignmentMemoryProfiler


REPLAY_FORMAT_VERSION = 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark a captured forced-alignment workload")
    parser.add_argument("bundle", type=Path, help="Directory containing manifest.json and captured WAV files")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--single-worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--replicated-models", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--torch-threads", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--device", help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--isolated-processes", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--artifact-label", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def load_replay(bundle: Path) -> tuple[dict[str, Any], list[TranscriptChunk]]:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != REPLAY_FORMAT_VERSION:
        raise ValueError(f"Unsupported alignment replay format: {manifest.get('format_version')}")
    if not manifest.get("capture_complete"):
        raise ValueError("Alignment replay capture is incomplete")
    jobs = []
    for job in manifest.get("jobs", []):
        wav_path = bundle / str(job["wav_file"])
        if not wav_path.is_file():
            raise FileNotFoundError(f"Captured alignment WAV is missing: {wav_path}")
        chunk = AudioChunk(
            index=int(job["chunk_index"]),
            start=float(job["start"]),
            end=float(job["end"]),
            samples=[],
            wav_path=wav_path,
            vad_activation=float(job.get("vad_activation", 0.0)),
            vad_peak=float(job.get("vad_peak", 0.0)),
            vad_group_index=job.get("vad_group_index"),
        )
        jobs.append(TranscriptChunk(chunk=chunk, text=str(job["text"])))
    if not jobs:
        raise ValueError("Alignment replay manifest contains no jobs")
    return manifest, jobs


def run_single(
    bundle: Path,
    workers: int,
    *,
    replicated_models: bool = False,
    torch_threads_override: int | None = None,
    device_override: str | None = None,
    batch_size_override: int | None = None,
    isolated_processes: bool = False,
    artifact_label: str | None = None,
) -> dict[str, Any]:
    manifest, jobs = load_replay(bundle)
    captured = manifest["alignment"]
    actual_workers = max(1, min(workers, len(jobs)))
    torch_threads, captured_thread_budget = benchmark_torch_threads(manifest, actual_workers)
    if torch_threads_override is not None:
        torch_threads = max(1, torch_threads_override)
    output_dir = bundle / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    layout = "replicated" if replicated_models else ("isolated" if isolated_processes else "shared")
    if torch_threads_override is not None:
        prefix = output_dir / f"{layout}-workers-{actual_workers}-threads-{torch_threads}"
    else:
        prefix = output_dir / (
            f"{layout}-workers-{actual_workers}" if replicated_models else f"workers-{actual_workers}"
        )
    emission_batch_size = (
        max(1, batch_size_override)
        if batch_size_override is not None
        else int(captured["emission_batch_size"])
    )
    if batch_size_override is not None:
        prefix = prefix.with_name(f"{prefix.name}-batch-{emission_batch_size}")
    if artifact_label:
        safe_label = "".join(character for character in artifact_label if character.isalnum() or character in "-_")
        if safe_label:
            prefix = prefix.with_name(f"{prefix.name}-{safe_label}")
    profiler = PipelineProfiler(enabled=True, output_path=prefix.with_suffix(".timing.csv"))
    for job in jobs:
        profiler.start_chunk(job.chunk.index, job.chunk.start, job.chunk.end)
    config = AlignmentConfig(
        model_name=str(captured["model_name"]),
        language=str(captured["language"]),
        device=device_override or str(captured["device"]),
        split_size=str(captured["split_size"]),
        temp_dir=bundle,
        sample_rate=int(captured["sample_rate"]),
        emission_batch_size=emission_batch_size,
        torch_threads=torch_threads,
        max_split_depth=int(captured["max_split_depth"]),
        isolate_models=isolated_processes,
    )
    started = time.perf_counter()
    memory_profiler = AlignmentMemoryProfiler(prefix.with_suffix(".memory.csv"))
    memory_profiler.start()
    pool = ReplicatedAlignmentPool(actual_workers, config, profiler) if replicated_models else AlignmentPool(
        actual_workers, config, profiler
    )
    for job in jobs:
        pool.submit(job)
    aligned = pool.close_and_collect()
    memory_profiler.checkpoint("workers_joined")
    memory_profiler.close()
    elapsed_sec = time.perf_counter() - started
    profiler.write()
    with prefix.with_suffix(".memory.csv").open(encoding="utf-8", newline="") as handle:
        memory_rows = list(csv.DictReader(handle))
    result_payload = [
        {
            "chunk_index": item.chunk.index,
            "fallback": item.fallback,
            "tokens": [(token.text, token.start, token.end, token.kind) for token in item.tokens],
        }
        for item in aligned
    ]
    digest = hashlib.sha256(
        json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    prefix.with_suffix(".result.json").write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "workers": actual_workers,
        "model_layout": layout,
        "torch_threads": torch_threads,
        "emission_batch_size": config.emission_batch_size,
        "thread_policy": "explicit_override" if torch_threads_override is not None else "fixed_captured_worker_thread_budget",
        "captured_worker_thread_budget": captured_thread_budget,
        "job_count": len(jobs),
        "elapsed_sec": elapsed_sec,
        "peak_private_working_set_bytes": max(int(row["private_working_set_bytes"]) for row in memory_rows),
        "peak_working_set_bytes": max(int(row["peak_working_set_bytes"]) for row in memory_rows),
        "peak_commit_bytes": max(int(row["peak_commit_bytes"]) for row in memory_rows),
        "result_sha256": digest,
    }
    prefix.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary), flush=True)
    return summary


def run_matrix(bundle: Path, workers: list[int]) -> int:
    for worker_count in workers:
        command = [
            sys.executable,
            "-m",
            "tools.alignment_benchmark",
            str(bundle),
            "--single-worker",
            str(worker_count),
        ]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parent.parent)
        if result.returncode != 0:
            return result.returncode
    print(f"Alignment benchmark results: {bundle / 'benchmarks'}", flush=True)
    return 0


def benchmark_torch_threads(manifest: dict[str, Any], workers: int) -> tuple[int, int]:
    captured_workers = max(1, int(manifest["workers_at_capture"]))
    captured_threads = max(1, int(manifest["alignment"]["torch_threads"]))
    captured_thread_budget = captured_workers * captured_threads
    return max(1, captured_thread_budget // max(1, workers)), captured_thread_budget


class ReplicatedAlignmentPool:
    """Developer-only reproduction of the former one-model-per-worker pool."""

    def __init__(self, workers: int, config: AlignmentConfig, profiler: PipelineProfiler) -> None:
        self.config = config
        self.profiler = profiler
        self._jobs: queue.Queue[TranscriptChunk | None] = queue.Queue()
        self._results: dict[int, AlignedChunk] = {}
        self._errors: list[BaseException] = []
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._worker, args=(worker_id,), name=f"replicated-aligner-{worker_id}")
            for worker_id in range(1, max(1, workers) + 1)
        ]
        print(f"Loading {len(self._threads)} replicated aligner models...", flush=True)
        for thread in self._threads:
            thread.start()

    def submit(self, transcript: TranscriptChunk) -> None:
        self._jobs.put(transcript)

    def close_and_collect(self) -> list[AlignedChunk]:
        for _ in self._threads:
            self._jobs.put(None)
        for thread in self._threads:
            thread.join()
        if self._errors:
            raise self._errors[0]
        return sorted(
            self._results.values(),
            key=lambda item: (item.chunk.start, item.chunk.end, item.chunk.index),
        )

    def _worker(self, worker_id: int) -> None:
        try:
            aligner = ForcedAligner(
                model_name=self.config.model_name,
                language=self.config.language,
                device=self.config.device,
                split_size=self.config.split_size,
                temp_dir=self.config.temp_dir,
                sample_rate=self.config.sample_rate,
                emission_batch_size=self.config.emission_batch_size,
                torch_threads=self.config.torch_threads,
            )
        except BaseException as exc:
            with self._lock:
                self._errors.append(exc)
            return
        while True:
            item = self._jobs.get()
            if item is None:
                return
            started = now()
            try:
                aligned = aligner.align(item)
                with self._lock:
                    self._results[item.chunk.index] = aligned
                    self.profiler.add_ms(item.chunk.index, "align_ms", (now() - started) * 1000)
                    self.profiler.set_align_worker(item.chunk.index, worker_id)
                    print(f"Replicated alignment complete: {len(self._results)}/4", flush=True)
            except BaseException as exc:
                with self._lock:
                    self._errors.append(exc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.single_worker is not None:
        run_single(
            args.bundle.resolve(),
            args.single_worker,
            replicated_models=args.replicated_models,
            torch_threads_override=args.torch_threads,
            device_override=args.device,
            batch_size_override=args.batch_size,
            isolated_processes=args.isolated_processes,
            artifact_label=args.artifact_label,
        )
        return 0
    return run_matrix(args.bundle.resolve(), args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
