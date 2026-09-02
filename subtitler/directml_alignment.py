"""DirectML inference support for the forced-alignment acoustic model."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np


DIRECTML_CACHE_FORMAT = 2
DIRECTML_ONNX_OPSET = 20
DIRECTML_PRECISION = "mixed-fp16"
DIRECTML_IO_PRECISION = "fp16"
DIRECTML_FP32_OPS = (
    "Conv",
    "LayerNormalization",
    "Softmax",
    "ReduceL2",
    "Div",
)
DIRECTML_MIXED_MAX_ABS_ERROR = 0.35
DIRECTML_MIXED_MAX_MEAN_ABS_ERROR = 0.04
SAMPLE_RATE = 16000
WINDOW_SECONDS = 30
CONTEXT_SECONDS = 2


def directml_available() -> bool:
    """Return whether this Python runtime exposes a usable DirectML provider."""
    if sys.platform != "win32":
        return False
    try:
        import onnxruntime as ort

        return "DmlExecutionProvider" in ort.get_available_providers()
    except (ImportError, OSError, RuntimeError):
        return False


def directml_provider_configuration() -> list[Any]:
    """Use accurate DirectML kernels and retain CPU for unsupported shape operators."""
    return [
        ("DmlExecutionProvider", {"disable_metacommands": "True"}),
        "CPUExecutionProvider",
    ]


class DirectMLAlignmentModel:
    """Small model adapter compatible with ctc-forced-aligner's emission loop."""

    def __init__(self, onnx_path: Path) -> None:
        import onnxruntime as ort
        import torch

        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=options,
            providers=directml_provider_configuration(),
        )
        if "DmlExecutionProvider" not in self._session.get_providers():
            raise RuntimeError("ONNX Runtime did not activate the DirectML execution provider")
        self._input_dtype = _directml_input_dtype(self._session.get_inputs()[0].type)
        self.dtype = torch.float32
        self.device = "cpu"

    def __call__(self, input_values):  # type: ignore[no-untyped-def]
        import torch

        input_batch = np.ascontiguousarray(
            input_values.detach().cpu().numpy(),
            dtype=self._input_dtype,
        )
        logits = self._session.run(["logits"], {"input_values": input_batch})[0]
        return SimpleNamespace(logits=torch.from_numpy(logits))


@dataclass(frozen=True)
class DirectMLPreparedModel:
    model: DirectMLAlignmentModel
    tokenizer: Any
    onnx_path: Path
    precision: str


def _directml_input_dtype(input_type: str) -> type[np.float16] | type[np.float32]:
    if input_type == "tensor(float16)":
        return np.float16
    if input_type == "tensor(float)":
        return np.float32
    raise RuntimeError(f"Unsupported DirectML alignment input type: {input_type}")


def load_directml_alignment_model(model_name: str) -> DirectMLPreparedModel:
    from transformers import AutoTokenizer

    override = os.environ.get("SUBTITLER_DIRECTML_MODEL", "").strip()
    if override:
        onnx_path = Path(override).resolve()
        if not onnx_path.is_file():
            raise FileNotFoundError(f"SUBTITLER_DIRECTML_MODEL does not exist: {onnx_path}")
        precision = "developer override"
    else:
        onnx_path = prepare_directml_model(model_name)
        precision = DIRECTML_PRECISION
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    return DirectMLPreparedModel(
        model=DirectMLAlignmentModel(onnx_path),
        tokenizer=tokenizer,
        onnx_path=onnx_path,
        precision=precision,
    )


def prepare_directml_model(model_name: str) -> Path:
    """Create and cache a DirectML-compatible ONNX graph beside the source model."""
    from filelock import FileLock

    cache_dir = _directml_cache_dir(model_name)
    lock = FileLock(f"{cache_dir}.lock", timeout=900)
    with lock:
        return _prepare_directml_model_locked(model_name, cache_dir)


def _prepare_directml_model_locked(model_name: str, cache_dir: Path) -> Path:
    output = cache_dir / "alignment.onnx"
    weights = cache_dir / "model.onnx.data"
    metadata = cache_dir / "metadata.json"
    if output.is_file() and weights.is_file() and _metadata_matches(metadata, model_name):
        return output

    staging = cache_dir.with_name(f"{cache_dir.name}.part-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        print("Preparing the DirectML alignment model (one-time conversion)...", flush=True)
        fp32_staging = staging / "fp32"
        fp32_staging.mkdir()
        raw_model = fp32_staging / "model.onnx"
        probe_input, reference_logits = _export_dynamic_onnx(model_name, raw_model)
        rewritten_model = fp32_staging / "alignment.onnx"
        _rewrite_attention_shapes(raw_model, rewritten_model)
        mixed_model = staging / "alignment.onnx"
        precision_graph = _convert_to_mixed_fp16(rewritten_model, mixed_model)
        validation = _validate_directml_export(
            mixed_model,
            probe_input,
            reference_logits,
            max_abs_error_limit=DIRECTML_MIXED_MAX_ABS_ERROR,
            max_mean_abs_error_limit=DIRECTML_MIXED_MAX_MEAN_ABS_ERROR,
        )
        shutil.rmtree(fp32_staging)
        (staging / "metadata.json").write_text(
            json.dumps(
                {
                    "format_version": DIRECTML_CACHE_FORMAT,
                    "source_model": model_name,
                    "opset": DIRECTML_ONNX_OPSET,
                    "precision": DIRECTML_PRECISION,
                    "io_precision": DIRECTML_IO_PRECISION,
                    "fp32_ops": list(DIRECTML_FP32_OPS),
                    "precision_graph": precision_graph,
                    "metacommands_disabled": True,
                    "validation": validation,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        staging.rename(cache_dir)
        print(f"DirectML alignment model ready: {output}", flush=True)
        return output
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _directml_cache_dir(model_name: str) -> Path:
    model_path = Path(model_name).expanduser()
    if model_path.is_dir():
        return model_path.resolve() / f"subtitler-directml-v{DIRECTML_CACHE_FORMAT}"

    from transformers.utils.hub import cached_file

    config_path = cached_file(model_name, "config.json")
    if not config_path:
        raise FileNotFoundError(f"Could not resolve the alignment model cache for {model_name}")
    return Path(config_path).resolve().parent / f"subtitler-directml-v{DIRECTML_CACHE_FORMAT}"


def _metadata_matches(metadata_path: Path, model_name: str) -> bool:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    validation = metadata.get("validation")
    precision_graph = metadata.get("precision_graph")
    return (
        metadata.get("format_version") == DIRECTML_CACHE_FORMAT
        and metadata.get("source_model") == model_name
        and metadata.get("opset") == DIRECTML_ONNX_OPSET
        and metadata.get("precision") == DIRECTML_PRECISION
        and metadata.get("io_precision") == DIRECTML_IO_PRECISION
        and metadata.get("fp32_ops") == list(DIRECTML_FP32_OPS)
        and isinstance(precision_graph, dict)
        and precision_graph.get("input_type") == DIRECTML_IO_PRECISION
        and precision_graph.get("output_type") == DIRECTML_IO_PRECISION
        and isinstance(precision_graph.get("fp16_initializers"), int)
        and precision_graph["fp16_initializers"] > 0
        and isinstance(precision_graph.get("fp32_initializers"), int)
        and precision_graph["fp32_initializers"] > 0
        and metadata.get("metacommands_disabled") is True
        and isinstance(validation, dict)
        and isinstance(validation.get("max_abs_error"), (int, float))
        and isinstance(validation.get("mean_abs_error"), (int, float))
    )


def _export_dynamic_onnx(model_name: str, output: Path) -> tuple[np.ndarray, np.ndarray]:
    import torch
    from transformers import AutoModelForCTC

    # PyTorch's exporter prints Unicode status glyphs. Windows Japanese console
    # streams otherwise raise before export completes even though the model is valid.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")

    class LogitsOnly(torch.nn.Module):
        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.model = model

        def forward(self, input_values):  # type: ignore[no-untyped-def]
            return self.model(input_values).logits

    model = AutoModelForCTC.from_pretrained(
        model_name,
        dtype=torch.float32,
        attn_implementation="eager",
    ).eval()
    wrapped = LogitsOnly(model).eval()
    example_samples = (WINDOW_SECONDS + 2 * CONTEXT_SECONDS) * SAMPLE_RATE
    example = torch.zeros((1, example_samples), dtype=torch.float32)
    batch_dimension = torch.export.Dim("batch", min=1, max=4)
    sample_dimension = torch.export.Dim("samples", min=400)
    with torch.inference_mode():
        torch.onnx.export(
            wrapped,
            (example,),
            str(output),
            input_names=["input_values"],
            output_names=["logits"],
            opset_version=DIRECTML_ONNX_OPSET,
            dynamo=True,
            external_data=True,
            dynamic_shapes=({0: batch_dimension, 1: sample_dimension},),
        )
        probe_samples = 5 * SAMPLE_RATE
        positions = torch.arange(probe_samples, dtype=torch.float32) / SAMPLE_RATE
        probe = (
            0.18 * torch.sin(2 * torch.pi * 173.0 * positions)
            + 0.07 * torch.sin(2 * torch.pi * 997.0 * positions)
        ).unsqueeze(0)
        reference_logits = model(probe).logits.detach().cpu().numpy()
    return np.ascontiguousarray(probe.numpy()), reference_logits


def _validate_directml_export(
    model_path: Path,
    probe_input: np.ndarray,
    reference_logits: np.ndarray,
    *,
    max_abs_error_limit: float = 0.05,
    max_mean_abs_error_limit: float = 0.001,
) -> dict[str, float]:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=directml_provider_configuration(),
    )
    if "DmlExecutionProvider" not in session.get_providers():
        raise RuntimeError("DirectML was not activated while validating the converted model")
    input_dtype = _directml_input_dtype(session.get_inputs()[0].type)
    candidate = session.run(
        ["logits"],
        {"input_values": np.ascontiguousarray(probe_input, dtype=input_dtype)},
    )[0]
    return _validate_logits(
        reference_logits,
        candidate,
        max_abs_error_limit=max_abs_error_limit,
        max_mean_abs_error_limit=max_mean_abs_error_limit,
    )


def _validate_logits(
    reference_logits: np.ndarray,
    candidate: np.ndarray,
    *,
    max_abs_error_limit: float = 0.05,
    max_mean_abs_error_limit: float = 0.001,
) -> dict[str, float]:
    if candidate.shape != reference_logits.shape:
        raise RuntimeError(
            f"DirectML validation shape mismatch: {candidate.shape} != {reference_logits.shape}"
        )
    difference = np.abs(candidate.astype(np.float64) - reference_logits.astype(np.float64))
    max_abs_error = float(np.max(difference))
    mean_abs_error = float(np.mean(difference))
    if (
        not np.isfinite(difference).all()
        or max_abs_error > max_abs_error_limit
        or mean_abs_error > max_mean_abs_error_limit
    ):
        raise RuntimeError(
            "DirectML validation exceeded the accuracy threshold "
            f"(max={max_abs_error:.6f}, mean={mean_abs_error:.6f})"
        )
    return {
        "max_abs_error": max_abs_error,
        "mean_abs_error": mean_abs_error,
    }


def _convert_to_mixed_fp16(source: Path, output: Path) -> dict[str, int | str]:
    import onnx
    from onnx.external_data_helper import convert_model_from_external_data
    from onnxruntime.transformers.float16 import convert_float_to_float16

    model = onnx.load(str(source), load_external_data=True)
    # Blocked FP32 tensors retain their original external-data descriptors.
    # Normalize every loaded tensor before saving into the separate mixed cache.
    convert_model_from_external_data(model)
    converted = convert_float_to_float16(
        model,
        keep_io_types=False,
        op_block_list=list(DIRECTML_FP32_OPS),
    )
    _topologically_sort_graph(converted)
    onnx.save_model(
        converted,
        str(output),
        save_as_external_data=True,
        all_tensors_to_one_file=True,
        location="model.onnx.data",
        size_threshold=1024,
    )
    onnx.checker.check_model(str(output))
    return _mixed_precision_graph_summary(converted)


def _mixed_precision_graph_summary(model: Any) -> dict[str, int | str]:
    import onnx

    input_type = model.graph.input[0].type.tensor_type.elem_type
    output_type = model.graph.output[0].type.tensor_type.elem_type
    fp16_initializers = sum(
        initializer.data_type == onnx.TensorProto.FLOAT16 for initializer in model.graph.initializer
    )
    fp32_initializers = sum(
        initializer.data_type == onnx.TensorProto.FLOAT for initializer in model.graph.initializer
    )
    if input_type != onnx.TensorProto.FLOAT16 or output_type != onnx.TensorProto.FLOAT16:
        raise RuntimeError("Mixed DirectML graph did not retain FP16 model I/O")
    if not fp16_initializers or not fp32_initializers:
        raise RuntimeError("Mixed DirectML graph did not retain both FP16 and FP32 initializers")
    return {
        "input_type": DIRECTML_IO_PRECISION,
        "output_type": DIRECTML_IO_PRECISION,
        "fp16_initializers": fp16_initializers,
        "fp32_initializers": fp32_initializers,
    }


def _topologically_sort_graph(model: Any) -> None:
    nodes = list(model.graph.node)
    producers = {
        output: index
        for index, node in enumerate(nodes)
        for output in node.output
        if output
    }
    children: dict[int, list[int]] = defaultdict(list)
    indegree = [0] * len(nodes)
    for index, node in enumerate(nodes):
        dependencies = {producers[value] for value in node.input if value in producers}
        indegree[index] = len(dependencies)
        for dependency in dependencies:
            children[dependency].append(index)
    ready = deque(index for index, count in enumerate(indegree) if count == 0)
    ordered = []
    while ready:
        index = ready.popleft()
        ordered.append(nodes[index])
        for child in children[index]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ordered) != len(nodes):
        raise RuntimeError("Converted DirectML graph contains a cycle")
    del model.graph.node[:]
    model.graph.node.extend(ordered)


def _rewrite_attention_shapes(source: Path, output: Path) -> None:
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(source), load_external_data=False)
    query_shape_name = "directml_attention_query_shape"
    output_shape_name = "directml_attention_output_shape"
    model.graph.initializer.extend(
        [
            numpy_helper.from_array(np.array([0, 0, -1, 64], dtype=np.int64), query_shape_name),
            numpy_helper.from_array(np.array([0, 0, -1], dtype=np.int64), output_shape_name),
        ]
    )
    query_rewrites = 0
    output_rewrites = 0
    for node in model.graph.node:
        if node.op_type != "Reshape" or len(node.input) < 2:
            continue
        if node.input[1] == "val_73":
            node.input[1] = query_shape_name
            node.attribute.clear()
            node.attribute.extend([onnx.helper.make_attribute("allowzero", 0)])
            query_rewrites += 1
        elif node.input[1] == "val_95":
            node.input[1] = output_shape_name
            node.attribute.clear()
            node.attribute.extend([onnx.helper.make_attribute("allowzero", 0)])
            output_rewrites += 1
    if query_rewrites != 72 or output_rewrites != 24:
        raise RuntimeError(
            "The exported alignment graph did not match the validated DirectML layout "
            f"(query={query_rewrites}, output={output_rewrites})"
        )
    onnx.save(model, str(output))
    onnx.checker.check_model(str(output))
