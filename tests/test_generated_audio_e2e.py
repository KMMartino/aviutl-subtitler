import contextlib
import io
import json
import math
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import aviutl_subtitle
from subtitler.config import load_workflow_config
from subtitler.models import AlignedChunk, AlignedToken, AudioChunk, TranscriptChunk
from subtitler.timed_text import load_timed_text
from subtitler.transcript_workflow import run_transcript_workflow


class _FakeTranscriber:
    def __init__(self) -> None:
        self.seen_chunks: list[AudioChunk] = []

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        self.seen_chunks.append(chunk)
        return TranscriptChunk(chunk=chunk, text="テスト音声")


class _DeterministicAlignmentPool:
    def __init__(self, _workers, _config, _profiler) -> None:
        self.transcripts: list[TranscriptChunk] = []

    def submit(self, transcript: TranscriptChunk) -> None:
        self.transcripts.append(transcript)

    def close_and_collect(self) -> list[AlignedChunk]:
        return [
            AlignedChunk(
                chunk=item.chunk,
                text=item.text,
                tokens=[AlignedToken(text=item.text, start=item.chunk.start, end=item.chunk.end, kind="char")],
            )
            for item in self.transcripts
        ]


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for the generated-audio integration fixture")
class GeneratedAudioEndToEndTests(unittest.TestCase):
    def test_direct_editorial_transcription_produces_source_timed_document(self) -> None:
        with tempfile.TemporaryDirectory(prefix="subtitler-e2e-") as directory:
            root = Path(directory)
            source = root / "generated.wav"
            _write_generated_tone(source)
            config = load_workflow_config("hosted-long-stream")
            config["alignment"].update(device="cpu", workers=1)

            def deterministic_vad(*, samples, sample_rate, **_kwargs):
                self.assertEqual(sample_rate, 16000)
                chunk = AudioChunk(0, 0.1, 0.9, samples[1600:14400], vad_group_index=0)
                return [chunk], [chunk], [(0.1, 0.9)]

            with (
                mock.patch("subtitler.backends.existing_pipeline.segment_speech_with_groups", side_effect=deterministic_vad),
                mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", _DeterministicAlignmentPool),
                mock.patch("subtitler.backends.existing_pipeline.ExistingPipelineBackend._build_transcriber",
                           return_value=_FakeTranscriber()),
                mock.patch("subtitler.subtitle_stage.build_refiner", side_effect=AssertionError("unexpected cleanup")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = run_transcript_workflow(
                    source_path=source, config=config, workspace=root / "work", audio_track=0, glossary=[],
                )
            document = load_timed_text(result.document_path, require_complete_raw=True)
            self.assertEqual([span.text for span in document.spans], ["テスト音声"])
            self.assertEqual(document.source_path, str(source.resolve()))
            self.assertTrue(result.transcript_path.is_file())
            self.assertEqual(result.api_cost_usd, 0)
            self.assertFalse(list(root.rglob("*.exo")))

            # A second workflow consumes the artifact with all expensive stages disabled.
            # CSV exports can be removed; they are no longer communication contracts.
            for sidecar in (root / "work").glob("*.csv"):
                sidecar.unlink()
            reuse_config = root / "reuse-config.json"
            reuse_settings = load_workflow_config("hosted")
            reuse_settings["cleanup"]["backend"] = "none"
            reuse_settings["cleanup"]["llm_split_planning"] = False
            reuse_config.write_text(json.dumps(reuse_settings), encoding="utf-8")
            output = root / "reused.exo"
            argv = [
                "aviutl_subtitle.py", str(source), "--workflow", "hosted", "--config", str(reuse_config),
                "--output", str(output), "--audio-track", "0", "--sidecar-dir", str(root / "reuse"),
                "--transcript-artifact", str(result.transcript_path), "--no-glossary",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch("subtitler.transcription_stage.extract_audio", side_effect=AssertionError("unexpected extraction")),
                mock.patch("subtitler.transcription_stage.build_backend", side_effect=AssertionError("unexpected model")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(aviutl_subtitle.main(), 0)
            self.assertIn("テスト音声".encode("utf-16-le").hex(), output.read_text(encoding="cp932"))
            reused = load_timed_text(root / "reuse/reused.subtitles.json")
            self.assertEqual(reused.input_revision_id, document.input_revision_id)

    def test_cli_converts_audio_and_runs_pipeline_to_exo(self) -> None:
        with tempfile.TemporaryDirectory(prefix="subtitler-e2e-") as temp_name:
            root = Path(temp_name)
            source = root / "generated-stereo-8khz.wav"
            output = root / "generated.exo"
            config = root / "config.json"
            model = root / "fake.gguf"
            model.write_bytes(b"test seam")
            _write_generated_tone(source)
            config.write_text(
                json.dumps(
                    {
                        "backend": {"model": str(model)},
                        "audio": {"track": 0},
                        "alignment": {"device": "cpu", "workers": 1},
                        "cleanup": {"backend": "local-llama", "model": str(model), "llm_split_planning": False},
                        "diagnostics": {"profile": False, "llm_split_diagnostics": False},
                    }
                ),
                encoding="utf-8",
            )

            observed_audio: dict[str, object] = {}
            transcriber = _FakeTranscriber()

            def deterministic_vad(*, samples, sample_rate, temp_dir, **_kwargs):
                observed_audio.update(sample_rate=sample_rate, sample_count=len(samples))
                chunk = AudioChunk(index=0, start=0.10, end=0.90, samples=samples[1600:14400], vad_group_index=0)
                return [chunk], [chunk]

            argv = [
                "aviutl_subtitle.py",
                str(source),
                "--workflow",
                "local",
                "--config",
                str(config),
                "--output",
                str(output),
                "--no-sidecars",
                "--no-glossary",
            ]
            console = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch("subtitler.backends.existing_pipeline.segment_speech_with_groups", side_effect=deterministic_vad),
                mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", _DeterministicAlignmentPool),
                mock.patch(
                    "subtitler.backends.existing_pipeline.ExistingPipelineBackend._build_transcriber",
                    return_value=transcriber,
                ),
                mock.patch("subtitler.subtitle_stage.build_refiner", return_value=None),
                contextlib.redirect_stdout(console),
            ):
                result = aviutl_subtitle.main()

            self.assertEqual(result, 0, console.getvalue())
            self.assertEqual(observed_audio["sample_rate"], 16000)
            self.assertGreaterEqual(int(observed_audio["sample_count"]), 15900)
            self.assertEqual(len(transcriber.seen_chunks), 1)
            self.assertTrue(output.is_file())
            exo = output.read_bytes().decode("cp932")
            self.assertIn("[exedit]", exo)
            self.assertIn("テスト音声".encode("utf-16le").hex(), exo)
            self.assertIn("Successfully generated", console.getvalue())


def _write_generated_tone(path: Path) -> None:
    sample_rate = 8000
    frames = bytearray()
    for index in range(sample_rate):
        value = int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<hh", value, -value))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(frames)


if __name__ == "__main__":
    unittest.main()
