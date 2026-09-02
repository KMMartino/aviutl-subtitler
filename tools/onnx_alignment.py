"""Developer harness for exporting and validating the alignment acoustic model."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


SAMPLE_RATE = 16000
WINDOW_SECONDS = 30
CONTEXT_SECONDS = 2
DIRECTML_ONNX_OPSET = 20


@dataclass(frozen=True)
class NumericComparison:
    shape: tuple[int, ...]
    max_abs_error: float
    mean_abs_error: float
    root_mean_square_error: float
    cosine_similarity: float


def compare_arrays(reference: np.ndarray, candidate: np.ndarray) -> NumericComparison:
    if reference.shape != candidate.shape:
        raise ValueError(f"Shape mismatch: {reference.shape} != {candidate.shape}")
    reference64 = reference.astype(np.float64, copy=False).reshape(-1)
    candidate64 = candidate.astype(np.float64, copy=False).reshape(-1)
    difference = candidate64 - reference64
    denominator = float(np.linalg.norm(reference64) * np.linalg.norm(candidate64))
    cosine = float(np.dot(reference64, candidate64) / denominator) if denominator else 1.0
    return NumericComparison(
        shape=reference.shape,
        max_abs_error=float(np.max(np.abs(difference))),
        mean_abs_error=float(np.mean(np.abs(difference))),
        root_mean_square_error=float(np.sqrt(np.mean(difference * difference))),
        cosine_similarity=cosine,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export and validate the forced-alignment model with ONNX Runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export the cached PyTorch model to ONNX")
    export_parser.add_argument("output", type=Path)
    export_parser.add_argument(
        "--model",
        default="MahmoudAshraf/mms-300m-1130-forced-aligner",
    )
    export_parser.add_argument("--opset", type=int, default=DIRECTML_ONNX_OPSET)
    export_parser.add_argument(
        "--fixed-batch-size",
        type=int,
        default=0,
        help="Developer diagnostic; zero exports the dynamic production layout",
    )

    compare_parser = subparsers.add_parser("compare", help="Compare PyTorch and DirectML on a replay job")
    compare_parser.add_argument("model", type=Path)
    compare_parser.add_argument("bundle", type=Path)
    compare_parser.add_argument("--job", type=int, default=0, help="Zero-based replay-job position")
    compare_parser.add_argument("--batch-size", type=int, default=4)
    compare_parser.add_argument("--disable-metacommands", action="store_true")
    compare_parser.add_argument("--output", type=Path)

    probe_parser = subparsers.add_parser("probe", help="Create a DirectML session without running inference")
    probe_parser.add_argument("model", type=Path)
    probe_parser.add_argument("--disable-optimization", action="store_true")
    probe_parser.add_argument("--disable-metacommands", action="store_true")

    rewrite_parser = subparsers.add_parser(
        "rewrite-directml",
        help="Replace symbolic attention reshape inputs with DirectML-compatible constants",
    )
    rewrite_parser.add_argument("source", type=Path)
    rewrite_parser.add_argument("output", type=Path)
    return parser.parse_args(argv)


def export_model(model_name: str, output: Path, opset: int, fixed_batch_size: int) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForCTC

    class LogitsOnly(torch.nn.Module):
        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.model = model

        def forward(self, input_values):  # type: ignore[no-untyped-def]
            return self.model(input_values).logits

    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Loading PyTorch alignment model: {model_name}", flush=True)
    model = AutoModelForCTC.from_pretrained(
        model_name,
        dtype=torch.float32,
        attn_implementation="eager",
    ).eval()
    wrapped = LogitsOnly(model).eval()
    fixed_audio_samples = (WINDOW_SECONDS + 2 * CONTEXT_SECONDS) * SAMPLE_RATE
    example_batch_size = max(1, fixed_batch_size)
    example = torch.zeros((example_batch_size, fixed_audio_samples), dtype=torch.float32)
    dynamic_shapes = None
    input_shape: list[int | str] = [example_batch_size, fixed_audio_samples]
    if fixed_batch_size <= 0:
        dynamic_shapes = (
            {
                0: torch.export.Dim("batch", min=1, max=4),
                1: torch.export.Dim("samples", min=400),
            },
        )
        input_shape = ["batch", "samples"]
    started = time.perf_counter()
    print(f"Exporting ONNX opset {opset}: {output}", flush=True)
    with torch.inference_mode():
        torch.onnx.export(
            wrapped,
            (example,),
            str(output),
            input_names=["input_values"],
            output_names=["logits"],
            opset_version=opset,
            do_constant_folding=True,
            dynamo=True,
            external_data=True,
            dynamic_shapes=dynamic_shapes,
        )
    elapsed = time.perf_counter() - started

    import onnx

    onnx.checker.check_model(str(output))
    metadata = {
        "format_version": 1,
        "source_model": model_name,
        "opset": opset,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "input_shape": input_shape,
        "export_elapsed_sec": elapsed,
        "onnx_bytes": output.stat().st_size,
        "external_data_files": sorted(path.name for path in output.parent.glob(f"{output.name}.data*")),
    }
    metadata_path = output.with_suffix(".export.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2), flush=True)
    return metadata


def _window_audio(audio_waveform, batch_size: int):  # type: ignore[no-untyped-def]
    import torch

    window = WINDOW_SECONDS * SAMPLE_RATE
    if audio_waveform.size(0) < window:
        extension = 0
        context = 0
        input_tensor = audio_waveform.unsqueeze(0)
    else:
        context = CONTEXT_SECONDS * SAMPLE_RATE
        extension = math.ceil(audio_waveform.size(0) / window) * window - audio_waveform.size(0)
        padded = torch.nn.functional.pad(audio_waveform, (context, context + extension))
        input_tensor = padded.unfold(0, window + 2 * context, window)
    return input_tensor, context, extension, max(1, batch_size)


def _finalize_emissions(logits, audio_sample_count: int, context: int, extension: int):  # type: ignore[no-untyped-def]
    import torch

    emissions = torch.cat(logits, dim=0)
    if context > 0:
        context_frames = int(CONTEXT_SECONDS * 50)
        emissions = emissions[:, context_frames : -context_frames + 1]
    emissions = emissions.flatten(0, 1)
    extension_frames = int((extension / SAMPLE_RATE) * 50)
    if extension_frames > 0:
        emissions = emissions[:-extension_frames]
    emissions = torch.log_softmax(emissions, dim=-1)
    emissions = torch.cat([emissions, torch.zeros(emissions.size(0), 1)], dim=1)
    stride_ms = float(audio_sample_count * 1000 / emissions.size(0) / SAMPLE_RATE)
    return emissions, stride_ms


def generate_onnx_emissions(session, audio_waveform, batch_size: int):  # type: ignore[no-untyped-def]
    import torch

    input_tensor, context, extension, batch_size = _window_audio(audio_waveform, batch_size)
    logits = []
    inference_seconds = 0.0
    expected_shape = session.get_inputs()[0].shape
    fixed_batch = int(expected_shape[0]) if isinstance(expected_shape[0], int) else None
    fixed_samples = int(expected_shape[1]) if isinstance(expected_shape[1], int) else None
    if fixed_samples is not None and input_tensor.size(1) != fixed_samples:
        raise ValueError(
            f"Fixed-window ONNX model requires {fixed_samples} samples, got {input_tensor.size(1)}; "
            "short jobs should retain the CPU fallback"
        )
    run_batch_size = fixed_batch or batch_size
    for index in range(0, input_tensor.size(0), run_batch_size):
        real_batch = input_tensor[index : index + run_batch_size]
        real_count = real_batch.size(0)
        if fixed_batch is not None and real_count < fixed_batch:
            padding = torch.zeros(
                (fixed_batch - real_count, real_batch.size(1)),
                dtype=real_batch.dtype,
            )
            real_batch = torch.cat([real_batch, padding], dim=0)
        input_batch = np.ascontiguousarray(real_batch.numpy())
        started = time.perf_counter()
        output = session.run(["logits"], {"input_values": input_batch})[0]
        inference_seconds += time.perf_counter() - started
        logits.append(torch.from_numpy(output[:real_count]))
    emissions, stride_ms = _finalize_emissions(logits, audio_waveform.size(0), context, extension)
    return emissions, stride_ms, inference_seconds


def _token_signature(results: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
    return [
        (str(result.get("text", "")), float(result.get("start", 0.0)), float(result.get("end", 0.0)))
        for result in results
        if str(result.get("text", "")).strip()
    ]


def _align_emissions(emissions, stride_ms: float, text: str, tokenizer, language: str):  # type: ignore[no-untyped-def]
    from ctc_forced_aligner import get_alignments, get_spans, postprocess_results, preprocess_text

    tokens_starred, text_starred = preprocess_text(
        text,
        romanize=True,
        language=language,
        split_size="char",
        star_frequency="edges",
    )
    segments, scores, blank_token = get_alignments(emissions, tokens_starred, tokenizer)
    spans = get_spans(tokens_starred, segments, blank_token)
    return _token_signature(postprocess_results(text_starred, spans, stride_ms, scores))


def _provider_counts(profile_path: Path) -> dict[str, int]:
    events = json.loads(profile_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for event in events:
        provider = event.get("args", {}).get("provider")
        if provider:
            counts[str(provider)] = counts.get(str(provider), 0) + 1
    return counts


def compare_replay_job(
    onnx_model: Path,
    bundle: Path,
    job_position: int,
    batch_size: int,
    output: Path,
    disable_metacommands: bool = False,
) -> dict[str, Any]:
    import onnxruntime as ort
    import torch
    from ctc_forced_aligner import generate_emissions, load_alignment_model, load_audio
    from tools.alignment_benchmark import load_replay

    manifest, jobs = load_replay(bundle)
    if not 0 <= job_position < len(jobs):
        raise IndexError(f"Replay job {job_position} is outside 0..{len(jobs) - 1}")
    job = jobs[job_position]
    model_name = str(manifest["alignment"]["model_name"])
    language = str(manifest["alignment"]["language"])
    ctc_language = "jpn" if language.lower() in {"ja", "jp", "jpn"} else language

    torch.set_num_threads(24)
    print(f"Loading CPU reference model: {model_name}", flush=True)
    model, tokenizer = load_alignment_model("cpu", model_path=model_name)
    waveform = load_audio(str(job.chunk.wav_path), model.dtype, "cpu")
    cpu_started = time.perf_counter()
    cpu_emissions, _rounded_stride = generate_emissions(model, waveform, batch_size=batch_size)
    cpu_seconds = time.perf_counter() - cpu_started
    cpu_stride = float(waveform.size(0)) * 1000.0 / float(cpu_emissions.size(0)) / SAMPLE_RATE

    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_profiling = True
    session_started = time.perf_counter()
    directml_options = {"disable_metacommands": "True"} if disable_metacommands else {}
    session = ort.InferenceSession(
        str(onnx_model),
        sess_options=options,
        providers=[("DmlExecutionProvider", directml_options), "CPUExecutionProvider"],
    )
    session_seconds = time.perf_counter() - session_started
    if "DmlExecutionProvider" not in session.get_providers():
        raise RuntimeError("DirectML initialization failed and ONNX Runtime fell back to CPU")
    gpu_emissions, gpu_stride, gpu_first_seconds = generate_onnx_emissions(session, waveform, batch_size)
    _warm_emissions, _warm_stride, gpu_warm_seconds = generate_onnx_emissions(session, waveform, batch_size)
    profile_path = Path(session.end_profiling())

    numeric = compare_arrays(cpu_emissions.numpy(), gpu_emissions.numpy())
    cpu_tokens = _align_emissions(cpu_emissions, cpu_stride, job.text, tokenizer, ctc_language)
    gpu_tokens = _align_emissions(gpu_emissions, gpu_stride, job.text, tokenizer, ctc_language)
    token_text_equal = [token[0] for token in cpu_tokens] == [token[0] for token in gpu_tokens]
    if len(cpu_tokens) == len(gpu_tokens):
        timestamp_differences = [
            max(abs(cpu[1] - gpu[1]), abs(cpu[2] - gpu[2])) for cpu, gpu in zip(cpu_tokens, gpu_tokens, strict=True)
        ]
    else:
        timestamp_differences = []

    result = {
        "format_version": 1,
        "onnx_model": str(onnx_model),
        "bundle": str(bundle),
        "job_position": job_position,
        "chunk_index": job.chunk.index,
        "duration_sec": job.chunk.end - job.chunk.start,
        "batch_size": batch_size,
        "directml_metacommands_disabled": disable_metacommands,
        "providers": session.get_providers(),
        "provider_event_counts": _provider_counts(profile_path),
        "session_create_sec": session_seconds,
        "cpu_inference_sec": cpu_seconds,
        "directml_first_inference_sec": gpu_first_seconds,
        "directml_warm_inference_sec": gpu_warm_seconds,
        "emissions": asdict(numeric),
        "cpu_token_count": len(cpu_tokens),
        "directml_token_count": len(gpu_tokens),
        "token_text_equal": token_text_equal,
        "max_timestamp_delta_sec": max(timestamp_differences, default=None),
        "mean_timestamp_delta_sec": (
            sum(timestamp_differences) / len(timestamp_differences) if timestamp_differences else None
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    profile_destination = output.with_suffix(".ort-profile.json")
    shutil.move(str(profile_path), profile_destination)
    print(json.dumps(result, indent=2), flush=True)
    return result


def probe_directml(
    onnx_model: Path,
    disable_optimization: bool = False,
    disable_metacommands: bool = False,
) -> list[str]:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    if disable_optimization:
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    directml_options = {"disable_metacommands": "True"} if disable_metacommands else {}
    session = ort.InferenceSession(
        str(onnx_model),
        sess_options=options,
        providers=[("DmlExecutionProvider", directml_options), "CPUExecutionProvider"],
    )
    providers = session.get_providers()
    print(json.dumps({"providers": providers}), flush=True)
    return providers


def rewrite_directml_attention_shapes(source: Path, output: Path) -> dict[str, int]:
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
        raise ValueError(
            f"Unexpected attention reshape layout: query={query_rewrites}, output={output_rewrites}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(output))
    onnx.checker.check_model(str(output))
    result = {"query_rewrites": query_rewrites, "output_rewrites": output_rewrites}
    print(json.dumps(result), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = parse_args(argv)
    if args.command == "export":
        export_model(args.model, args.output.resolve(), args.opset, args.fixed_batch_size)
        return 0
    if args.command == "probe":
        providers = probe_directml(
            args.model.resolve(),
            args.disable_optimization,
            args.disable_metacommands,
        )
        return 0 if "DmlExecutionProvider" in providers else 1
    if args.command == "rewrite-directml":
        rewrite_directml_attention_shapes(args.source.resolve(), args.output.resolve())
        return 0
    compare_replay_job(
        args.model.resolve(),
        args.bundle.resolve(),
        args.job,
        args.batch_size,
        (args.output or args.bundle / "benchmarks" / f"directml-job-{args.job}.json").resolve(),
        args.disable_metacommands,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
