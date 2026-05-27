"""Parallel forced-alignment worker pool."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from .aligner import AlignmentTooLongError, ForcedAligner, is_japanese_language
from .models import AlignedChunk, TranscriptChunk
from .profiling import PipelineProfiler, now
from .vad import split_chunk_with_tighter_vad


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


class AlignmentPool:
    def __init__(self, workers: int, config: AlignmentConfig, profiler: PipelineProfiler) -> None:
        self.workers = max(1, workers)
        self.config = config
        self.profiler = profiler
        self._jobs: queue.Queue[TranscriptChunk | None] = queue.Queue()
        self._results: dict[int, list[AlignedChunk]] = {}
        self._errors: list[BaseException] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._submitted = 0
        self._completed = 0
        print(f"Loading aligner workers: 0/{self.workers}...", flush=True)
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
        results: list[AlignedChunk] = []
        for index in sorted(self._results):
            results.extend(sorted(self._results[index], key=lambda item: (item.chunk.start, item.chunk.end)))
        return results

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
            with self._lock:
                print(f"Aligner worker {worker_id} ready.", flush=True)
        except BaseException as exc:
            with self._lock:
                self._errors.append(exc)
            return

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
            subchunks = split_chunk_with_tighter_vad(
                item.chunk,
                sample_rate=self.config.sample_rate,
                temp_dir=self.config.temp_dir,
                keep_temp=True,
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
