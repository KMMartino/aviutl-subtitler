import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from subtitler.aligner import ForcedAligner
from subtitler.errors import AlignmentError
from subtitler.models import AudioChunk, TranscriptChunk
from subtitler.directml_alignment import (
    DIRECTML_CACHE_FORMAT,
    DIRECTML_FP32_OPS,
    DIRECTML_IO_PRECISION,
    DIRECTML_MIXED_MAX_ABS_ERROR,
    DIRECTML_MIXED_MAX_MEAN_ABS_ERROR,
    DIRECTML_ONNX_OPSET,
    DIRECTML_PRECISION,
    _metadata_matches,
    _directml_input_dtype,
    _topologically_sort_graph,
    _validate_logits,
    directml_provider_configuration,
)


class DirectMLAlignmentTests(unittest.TestCase):
    def test_directml_input_dtype_matches_converted_graph_io(self) -> None:
        self.assertIs(_directml_input_dtype("tensor(float16)"), np.float16)
        self.assertIs(_directml_input_dtype("tensor(float)"), np.float32)
        with self.assertRaisesRegex(RuntimeError, "Unsupported DirectML alignment input type"):
            _directml_input_dtype("tensor(double)")

    def test_provider_configuration_disables_inaccurate_metacommands(self) -> None:
        self.assertEqual(
            directml_provider_configuration(),
            [
                ("DmlExecutionProvider", {"disable_metacommands": "True"}),
                "CPUExecutionProvider",
            ],
        )

    def test_auto_device_prefers_directml_when_cuda_is_unavailable(self) -> None:
        aligner = ForcedAligner.__new__(ForcedAligner)
        aligner.device = "auto"
        with (
            mock.patch("torch.cuda.is_available", return_value=False),
            mock.patch("subtitler.directml_alignment.directml_available", return_value=True),
        ):
            self.assertEqual(aligner._resolve_device(), "directml")

    def test_cached_model_metadata_is_tied_to_source_and_conversion_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            metadata = Path(temp_name) / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "format_version": DIRECTML_CACHE_FORMAT,
                        "source_model": "example/model",
                        "opset": DIRECTML_ONNX_OPSET,
                        "precision": DIRECTML_PRECISION,
                        "io_precision": DIRECTML_IO_PRECISION,
                        "fp32_ops": list(DIRECTML_FP32_OPS),
                        "precision_graph": {
                            "input_type": DIRECTML_IO_PRECISION,
                            "output_type": DIRECTML_IO_PRECISION,
                            "fp16_initializers": 10,
                            "fp32_initializers": 5,
                        },
                        "metacommands_disabled": True,
                        "validation": {
                            "max_abs_error": 0.01,
                            "mean_abs_error": 0.0001,
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_metadata_matches(metadata, "example/model"))
            self.assertFalse(_metadata_matches(metadata, "different/model"))

            value = json.loads(metadata.read_text(encoding="utf-8"))
            value["fp32_ops"] = ["Conv"]
            metadata.write_text(json.dumps(value), encoding="utf-8")
            self.assertFalse(_metadata_matches(metadata, "example/model"))

    def test_validation_rejects_the_observed_inaccurate_kernel_scale(self) -> None:
        reference = np.zeros((1, 10, 4), dtype=np.float32)
        accurate = np.full_like(reference, 0.0001)
        inaccurate = np.full_like(reference, 0.08)

        self.assertLess(_validate_logits(reference, accurate)["max_abs_error"], 0.05)
        with self.assertRaisesRegex(RuntimeError, "accuracy threshold"):
            _validate_logits(reference, inaccurate)

    def test_mixed_precision_validation_uses_measured_error_envelope(self) -> None:
        reference = np.zeros((1, 10, 4), dtype=np.float32)
        measured_scale = np.full_like(reference, 0.03)

        with self.assertRaisesRegex(RuntimeError, "accuracy threshold"):
            _validate_logits(reference, measured_scale)
        result = _validate_logits(
            reference,
            measured_scale,
            max_abs_error_limit=DIRECTML_MIXED_MAX_ABS_ERROR,
            max_mean_abs_error_limit=DIRECTML_MIXED_MAX_MEAN_ABS_ERROR,
        )
        self.assertAlmostEqual(result["mean_abs_error"], 0.03, places=6)

    def test_converted_graph_is_topologically_sorted(self) -> None:
        import onnx

        first = onnx.helper.make_node("Identity", ["input"], ["middle"], name="first")
        second = onnx.helper.make_node("Identity", ["middle"], ["output"], name="second")
        graph = onnx.helper.make_graph(
            [second, first],
            "unsorted",
            [onnx.helper.make_tensor_value_info("input", onnx.TensorProto.FLOAT, [1])],
            [onnx.helper.make_tensor_value_info("output", onnx.TensorProto.FLOAT, [1])],
        )
        model = onnx.helper.make_model(graph)

        _topologically_sort_graph(model)

        self.assertEqual([node.name for node in model.graph.node], ["first", "second"])

    def test_directml_runtime_failure_never_uses_proportional_timestamps(self) -> None:
        aligner = ForcedAligner.__new__(ForcedAligner)
        aligner.language = "eng"
        aligner.ctc_language = "eng"
        aligner.device = "directml"
        aligner.split_size = "word"
        aligner.temp_dir = Path(".")
        aligner.sample_rate = 16000
        aligner.emission_batch_size = 1
        aligner.alignment_model = mock.Mock(dtype="float32", device="cpu")
        aligner.alignment_tokenizer = mock.Mock()
        item = TranscriptChunk(
            AudioChunk(0, 0.0, 1.0, [], wav_path=Path("unused.wav")),
            "hello",
        )

        with mock.patch("ctc_forced_aligner.load_audio", side_effect=RuntimeError("device removed")):
            with self.assertRaisesRegex(AlignmentError, "DirectML alignment failed"):
                aligner.align(item)


if __name__ == "__main__":
    unittest.main()
