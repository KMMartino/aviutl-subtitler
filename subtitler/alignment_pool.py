"""Parallel forced-alignment worker pool."""

from __future__ import annotations

import contextlib
import queue
import threading
import traceback
from dataclasses import dataclass
from dataclasses import replace
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path

from .aligner import AlignmentTooLongError, ForcedAligner, is_japanese_language
from .audio import write_wav_segment
from .errors import AlignmentError
from .models import AlignedChunk, AudioChunk, TranscriptChunk
from .profiling import PipelineProfiler, now
from .vad import VadSession, split_chunk_with_tighter_vad


@dataclass(frozen=True)
class AlignmentConfig:
    model_name: str
    language: str
    device: str
    split_size: str
    temp_dir: Path
    sample_rate: int
    emission_batch_size: int
    torch_threads: int | None
    max_split_depth: int = 4
    vad_session: VadSession | None = None
    isolate_models: bool = False


@dataclass(frozen=True)
class AlignmentProfile:
    chunk_index: int
    align_ms: float
    worker_id: int
    error: str = ""


class _InProcessAlignmentPool:
    def __init__(self, workers: int, config: AlignmentConfig, profiler: PipelineProfiler) -> None:
        self.workers = max(1, min(2, workers))
        self.config = config
        self.profiler = profiler
        self._jobs: queue.Queue[TranscriptChunk | None] = queue.Queue()
        self._results: dict[int, list[AlignedChunk]] = {}
        self._errors: list[BaseException] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._submitted = 0
        self._completed = 0
        print(f"Loading {self.workers} dedicated aligner model(s)...", flush=True)
        for worker_id in range(1, self.workers + 1):
            thread = threading.Thread(target=self._worker, args=(worker_id,), name=f"aligner-{worker_id}")
            thread.start()
            self._threads.append(thread)

    def submit(self, transcript: TranscriptChunk) -> None:
        self._submitted += 1
        self._jobs.put(transcript)

    def close_and_collect(self) -> list[AlignedChunk]:
        for _ in self._threads:
            self._jobs.put(None)
        for thread in self._threads:
            thread.join()
        if self._errors:
            raise self._errors[0]
        results = [aligned for group in self._results.values() for aligned in group]
        return sorted(results, key=lambda item: (item.chunk.start, item.chunk.end, item.chunk.index))

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
        with self._lock:
            print(f"Aligner worker {worker_id} ready with dedicated model.", flush=True)

        while True:
            item = self._jobs.get()
            if item is None:
                self._jobs.task_done()
                return
            start = now()
            try:
                aligned = self._align_with_retry(aligner, item, depth=0)
                elapsed_ms = (now() - start) * 1000
                with self._lock:
                    self._results[item.chunk.index] = aligned
                    self._completed += 1
                    self.profiler.add_ms(item.chunk.index, "align_ms", elapsed_ms)
                    self.profiler.set_align_worker(item.chunk.index, worker_id)
                    if self._submitted < 20 or self._completed % 5 == 0 or self._completed == self._submitted:
                        print(f"Alignment complete: {self._completed}/{self._submitted}", flush=True)
            except BaseException as exc:
                self.profiler.mark_error(item.chunk.index, exc)
                with self._lock:
                    self._errors.append(exc)
            finally:
                self._jobs.task_done()

    def _align_with_retry(
        self,
        aligner: ForcedAligner,
        item: TranscriptChunk,
        depth: int,
    ) -> list[AlignedChunk]:
        try:
            return [aligner.align(item)]
        except AlignmentTooLongError as exc:
            if depth >= self.config.max_split_depth:
                raise AlignmentTooLongError(
                    f"chunk {item.chunk.index} still exceeds CTC target length after "
                    f"{self.config.max_split_depth} VAD split attempts: {exc}"
                ) from exc
            retry_chunk = _chunk_with_samples(item.chunk)
            subchunks = split_chunk_with_tighter_vad(
                retry_chunk,
                sample_rate=self.config.sample_rate,
                temp_dir=self.config.temp_dir,
                keep_temp=True,
                session=self.config.vad_session,
            )
            if len(subchunks) < 2:
                raise AlignmentTooLongError(
                    f"chunk {item.chunk.index} exceeds CTC target length and could not be split: {exc}"
                ) from exc
            transcripts = _split_transcript_for_subchunks(item, subchunks, self.config.language)
            print(
                f"Alignment target too long for chunk {item.chunk.index}; "
                f"reran VAD and split into {len(transcripts)} subchunks "
                f"(attempt {depth + 1}/{self.config.max_split_depth}).",
                flush=True,
            )
            aligned: list[AlignedChunk] = []
            for transcript in transcripts:
                aligned.extend(self._align_with_retry(aligner, transcript, depth + 1))
            return aligned


def _alignment_process_main(
    connection: Connection,
    workers: int,
    config: AlignmentConfig,
) -> None:
    """Own all alignment runtime state so process exit releases it deterministically."""
    profiler = PipelineProfiler(enabled=True, output_path=None)
    child_config = replace(config, vad_session=VadSession())
    try:
        pool = _InProcessAlignmentPool(workers, child_config, profiler)
        while True:
            message, payload = connection.recv()
            if message == "submit":
                transcript = payload
                profiler.start_chunk(
                    transcript.chunk.index,
                    transcript.chunk.start,
                    transcript.chunk.end,
                )
                pool.submit(transcript)
            elif message == "close":
                results = pool.close_and_collect()
                profiles = [
                    AlignmentProfile(
                        chunk_index=row.chunk_index,
                        align_ms=row.align_ms,
                        worker_id=row.align_worker_id,
                        error=row.error,
                    )
                    for row in profiler.rows.values()
                ]
                connection.send(("ok", (results, profiles)))
                return
            else:
                raise AlignmentError(f"Unknown alignment process message: {message}")
    except BaseException as exc:
        with contextlib.suppress(BaseException):
            connection.send(("error", (str(exc), traceback.format_exc())))
    finally:
        connection.close()


class AlignmentPool:
    """Subprocess-backed alignment pool with a small, path-based IPC payload."""

    def __init__(self, workers: int, config: AlignmentConfig, profiler: PipelineProfiler) -> None:
        self.workers = max(1, min(2, workers))
        self.config = config
        self.profiler = profiler
        self._closed = False
        self._original_chunks: dict[int, AudioChunk] = {}
        self._pending: list[TranscriptChunk] = []
        self._context = get_context("spawn")
        process_count = self.workers if config.isolate_models and self.workers > 1 else 1
        child_workers = 1 if process_count > 1 else self.workers
        self._connections: list[Connection] = []
        self._processes = []
        child_config = replace(config, vad_session=None)
        for process_index in range(process_count):
            parent_connection, child_connection = self._context.Pipe()
            process = self._context.Process(
                target=_alignment_process_main,
                args=(child_connection, child_workers, child_config),
                name=(
                    f"alignment-runtime-{process_index + 1}"
                    if process_count > 1
                    else "alignment-runtime"
                ),
            )
            process.start()
            child_connection.close()
            self._connections.append(parent_connection)
            self._processes.append(process)
        if process_count > 1:
            print(f"Alignment runtimes started in {process_count} isolated processes.", flush=True)
        else:
            print("Alignment runtime started in a dedicated process.", flush=True)

    def submit(self, transcript: TranscriptChunk) -> None:
        if self._closed:
            raise AlignmentError("Cannot submit work to a closed alignment runtime")
        self._original_chunks[transcript.chunk.index] = transcript.chunk
        self._pending.append(self._path_backed_transcript(transcript))

    def close_and_collect(self) -> list[AlignedChunk]:
        if self._closed:
            raise AlignmentError("Alignment runtime was already closed")
        self._closed = True
        try:
            lanes = self._balanced_lanes(len(self._connections))
            for connection, transcripts in zip(self._connections, lanes, strict=True):
                for transcript in transcripts:
                    connection.send(("submit", transcript))
                connection.send(("close", None))
            combined_results: list[AlignedChunk] = []
            for lane_index, connection in enumerate(self._connections, start=1):
                status, payload = connection.recv()
                if status != "ok":
                    message, child_traceback = payload
                    raise AlignmentError(
                        f"Alignment process {lane_index} failed: {message}\n"
                        f"Child traceback:\n{child_traceback}"
                    )
                results, profiles = payload
                combined_results.extend(results)
                for profile in profiles:
                    self.profiler.add_ms(profile.chunk_index, "align_ms", profile.align_ms)
                    self.profiler.set_align_worker(profile.chunk_index, lane_index)
                    if profile.error:
                        self.profiler.mark_error(profile.chunk_index, profile.error)
            restored = [self._restore_original_chunk(result) for result in combined_results]
            return sorted(restored, key=lambda item: (item.chunk.start, item.chunk.end, item.chunk.index))
        except (EOFError, BrokenPipeError, OSError) as exc:
            exit_codes = [process.exitcode for process in self._processes]
            raise AlignmentError(
                f"Alignment process exited unexpectedly (exit codes {exit_codes})"
            ) from exc
        finally:
            for connection in self._connections:
                connection.close()
            for process in self._processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
            noun = "runtimes exited" if len(self._processes) > 1 else "runtime exited"
            print(f"Alignment {noun}; model memory released.", flush=True)

    def _balanced_lanes(self, lane_count: int) -> list[list[TranscriptChunk]]:
        lanes: list[list[TranscriptChunk]] = [[] for _ in range(max(1, lane_count))]
        durations = [0.0] * len(lanes)
        ordered = sorted(
            self._pending,
            key=lambda item: (
                -(item.chunk.end - item.chunk.start),
                item.chunk.start,
                item.chunk.index,
            ),
        )
        for transcript in ordered:
            lane_index = min(range(len(lanes)), key=lambda index: (durations[index], index))
            lanes[lane_index].append(transcript)
            durations[lane_index] += max(0.0, transcript.chunk.end - transcript.chunk.start)
        return lanes

    def _path_backed_transcript(self, transcript: TranscriptChunk) -> TranscriptChunk:
        chunk = transcript.chunk
        wav_path = chunk.wav_path
        if wav_path is None:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self.config.temp_dir / f"alignment_ipc_{chunk.index:05d}.wav"
            write_wav_segment(chunk.samples, self.config.sample_rate, wav_path)
        lightweight_chunk = AudioChunk(
            index=chunk.index,
            start=chunk.start,
            end=chunk.end,
            samples=[],
            wav_path=wav_path,
            vad_activation=chunk.vad_activation,
            vad_peak=chunk.vad_peak,
            vad_group_index=chunk.vad_group_index,
        )
        return TranscriptChunk(chunk=lightweight_chunk, text=transcript.text, error=transcript.error)

    def _restore_original_chunk(self, aligned: AlignedChunk) -> AlignedChunk:
        original = self._original_chunks.get(aligned.chunk.index)
        if (
            original is None
            or aligned.chunk.start != original.start
            or aligned.chunk.end != original.end
        ):
            return aligned
        return replace(aligned, chunk=original)


def _chunk_with_samples(chunk: AudioChunk) -> AudioChunk:
    if len(chunk.samples) or chunk.wav_path is None:
        return chunk
    import soundfile as sf

    samples, _sample_rate = sf.read(str(chunk.wav_path), dtype="float32", always_2d=False)
    return replace(chunk, samples=samples)


def _split_transcript_for_subchunks(
    item: TranscriptChunk,
    subchunks: list,
    language: str,
) -> list[TranscriptChunk]:
    text = item.text.strip()
    if not text:
        return [TranscriptChunk(chunk=chunk, text="") for chunk in subchunks]
    units = _text_units(text, language)
    if len(units) < len(subchunks):
        return [TranscriptChunk(chunk=item.chunk, text=text)]

    total_duration = max(item.chunk.end - item.chunk.start, 0.001)
    cursor = 0
    result: list[TranscriptChunk] = []
    for index, subchunk in enumerate(subchunks):
        if index == len(subchunks) - 1:
            next_cursor = len(units)
        else:
            ratio = max(0.0, min(1.0, (subchunk.end - item.chunk.start) / total_duration))
            next_cursor = round(ratio * len(units))
            min_remaining = len(subchunks) - index - 1
            next_cursor = max(cursor + 1, min(next_cursor, len(units) - min_remaining))
        part_units = units[cursor:next_cursor]
        result.append(TranscriptChunk(chunk=subchunk, text=_join_units(part_units, language)))
        cursor = next_cursor
    return [transcript for transcript in result if transcript.text.strip()]


def _text_units(text: str, language: str) -> list[str]:
    if is_japanese_language(language):
        return [char for char in text if char.strip()]
    return text.split()


def _join_units(units: list[str], language: str) -> str:
    if is_japanese_language(language):
        return "".join(units)
    return " ".join(units)
