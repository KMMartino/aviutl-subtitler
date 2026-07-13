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
from subtitler.models import AlignedChunk, AlignedToken, AudioChunk, TranscriptChunk


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
                mock.patch.object(aviutl_subtitle, "_build_refiner", return_value=None),
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
