"""Extract requested moments from one recording, optionally with synchronized facecam."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .artifact_io import write_json_artifact
from .config import load_workflow_config
from .editorial_hosted import _analyze_editorial_visual_windows
from .env import load_env_file
from .errors import SubtitlerError
from .external_transcribers import require_api_key
from .evidence import load_transcript_evidence
from .moment_export import export_moments, write_moment_report
from .moment_selection import SELECTION_VERSION, select_moments
from .source_inspection import SourceInspectionRequest, inspect_recording
from .subtitle_stage import build_refiner
from .transcript_workflow import run_transcript_workflow


def probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    data = json.loads(result.stdout)
    video = next((stream for stream in data["streams"] if stream["codec_type"] == "video"), None)
    if video is None:
        raise SubtitlerError(f"A video recording is required: {path}")
    from fractions import Fraction
    fps = float(Fraction(video.get("avg_frame_rate") or video["r_frame_rate"]))
    if not math.isfinite(fps) or fps <= 0:
        raise SubtitlerError(f"Invalid frame rate: {path}")
    return {"path": str(path.resolve()), "fps": fps,
            "audio_tracks": len([s for s in data["streams"] if s["codec_type"] == "audio"]),
            "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
            "duration_sec": float(data["format"]["duration"]),
            "possible_vfr": video.get("avg_frame_rate") != video.get("r_frame_rate")}


def prepare_range(source: Path, output: Path, start: float, end: float) -> Path:
    """Decode an exact bounded analysis copy; model calls never receive the surrounding source."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-ss", str(start),
                    "-i", str(source), "-t", str(end - start), "-map", "0:v:0", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(output)], check=True)
    actual = probe_media(output)["duration_sec"]
    if abs(actual - (end - start)) > 0.25:
        raise SubtitlerError("Prepared range duration differs from the selected scope; no model requests were sent")
    return output


def run_moments(spec: dict[str, Any], config_path: Path, env_file: Path, output: Path, audio_track: int = 0) -> None:
    query = str(spec.get("query", "")).strip()
    if not query or len(query) > 20000:
        raise SubtitlerError("Describe the moments to extract (up to 20,000 characters)")
    config = load_workflow_config("hosted-long-stream", config_path)
    if config.get("cost", {}).get("estimate_cost_only", False):
        raise SubtitlerError("Moment extraction does not support estimate-only runs. Disable 'Estimate cost only' in Settings to run extraction.")
    source = Path(spec["sourcePath"]).resolve()
    facecam = Path(spec["facecamPath"]).resolve() if spec.get("facecamPath") else None
    if not source.is_file() or (facecam and not facecam.is_file()) or source == facecam:
        raise SubtitlerError("Choose an existing recording and a distinct optional facecam")
    inspection = inspect_recording(SourceInspectionRequest(facecam or source, source, paired=facecam is not None))
    media = [probe_media(path) for path in [source, *([facecam] if facecam else [])]]
    duration = min(row["duration_sec"] for row in media)
    manifest_path = source.parent / "source.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("type") == "source_media" and Path(manifest.get("path", "")).resolve() == source:
            origin_info = manifest.get("origin", {})
            acquired_start = float(manifest["sourceStartSec"])
            acquired_end = float(manifest["sourceEndSec"])
            partial = acquired_start > 0 or acquired_end < float(origin_info.get("durationSec") or acquired_end)
            if partial and abs(media[0]["duration_sec"] - (acquired_end - acquired_start)) > .25:
                raise SubtitlerError("Downloaded range duration does not match the VOD range; no model requests were sent")
            spec = {**spec, "originOffsetSec": acquired_start,
                    "sourceUrl": origin_info.get("sourcePageUrl", ""),
                    "boundedEnd": acquired_end < float(origin_info.get("durationSec") or acquired_end)}
    start = float(spec.get("startSec", 0))
    end = float(spec["endSec"]) if spec.get("endSec") is not None else duration
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + 0.05:
        raise SubtitlerError("Choose a non-empty range inside the recording")
    end = min(end, duration)
    origin = float(spec.get("originOffsetSec", 0))
    if not math.isfinite(origin) or origin < 0:
        raise SubtitlerError("Invalid VOD time offset")
    output = output.resolve()
    if any(output.with_suffix(suffix).exists() for suffix in (".json", ".exo", ".html")):
        raise SubtitlerError("Choose a new output name to preserve earlier results")
    workspace = output.with_suffix(".evidence")
    workspace.mkdir(parents=True, exist_ok=False)
    load_env_file(env_file)
    require_api_key("OPENAI_API_KEY")
    if config["backend"]["transcriber"] == "gemini":
        require_api_key("GEMINI_API_KEY")
    editorial = config.get("editorial", {})
    model = str(editorial.get("analysis_model") or "gpt-5.6-luna")
    config["cleanup"].update(backend="openai", api_model=model,
                              reasoning_effort=editorial.get("reasoning_effort") or "medium", thinking_level=None)
    locale = spec.get("locale", "en")
    usage = ApiUsageLedger()
    artifact: dict[str, Any] = {"type": "moment_extraction", "version": SELECTION_VERSION, "query": query,
        "sources": media, "range_start_ms": round(start * 1000), "range_end_ms": round(end * 1000),
        "origin_offset_ms": round(origin * 1000), "source_url": spec.get("sourceUrl", ""),
        "matches": [], "evidence": [], "api_cost_usd": 0.0, "status": "running"}
    write_json_artifact(workspace / "request.json", {"spec": spec, "config": config})
    try:
        analysis_paths = []
        for index, original in enumerate(media):
            path = Path(original["path"])
            if start > 0 or end < original["duration_sec"] - 0.05:
                print(f"Preparing selected range: {path.name}", flush=True)
                path = prepare_range(path, workspace / f"range-{index}.mkv", start, end)
            analysis_paths.append(path)
        duration_ms = round((end - start) * 1000)
        speech_index = 1 if facecam and spec.get("speechSource", "facecam") == "facecam" else 0
        if audio_track < 0 or (media[speech_index]["has_audio"] and audio_track >= media[speech_index]["audio_tracks"]):
            raise SubtitlerError("Selected speech audio track is unavailable")
        if media[speech_index]["has_audio"]:
            print("Transcribing selected scope...", flush=True)
            transcript = run_transcript_workflow(source_path=analysis_paths[speech_index], config=config,
                workspace=workspace / "transcription", audio_track=audio_track, glossary=[])
            artifact["api_cost_usd"] += transcript.api_cost_usd
            for index, row in enumerate(load_transcript_evidence(transcript.document_path)):
                lower, upper = max(0, row.start_ms), min(duration_ms, row.end_ms)
                if upper > lower:
                    artifact["evidence"].append({"id": f"speech-{index}", "kind": "speech", "start_ms": lower,
                                                 "end_ms": upper, "text": row.text})
        for index, path in enumerate(analysis_paths):
            print(f"Collecting visual events ({index + 1}/{len(analysis_paths)})...", flush=True)
            result = _analyze_editorial_visual_windows(media_path=path, duration_sec=end - start,
                detail=str(editorial.get("visual_detail") or "detailed"), ffmpeg="ffmpeg",
                sampling_scale=float(editorial.get("visual_sampling_scale") or 1.5), model=model,
                reasoning_effort=str(editorial.get("visual_reasoning_effort") or "low"), output_locale=locale,
                editorial_context=f"Exhaustive moment extraction. Describe observed events relevant to: {query}. "
                                  + ("This is the facecam." if index else "This is the primary recording."),
                progress_path=workspace / f"visual-{index}.json", diagnostics_path=workspace / f"visual-{index}.jsonl",
                progress=lambda complete, total, count: print(f"Visual windows: {complete}/{total}; {count} events", flush=True))
            artifact["api_cost_usd"] += result.cost_usd
            write_json_artifact(workspace / f"visual-{index}-result.json", asdict(result))
            for number, segment in enumerate(result.segments):
                lower, upper = max(0, segment.start_ms), min(duration_ms, segment.end_ms)
                if upper > lower:
                    artifact["evidence"].append({"id": f"visual-{index}-{number}", "kind": "visual", "start_ms": lower,
                        "end_ms": upper, "text": segment.description, "observed_label": segment.observed_label,
                        "uncertainty": segment.handoff_reason, "source": "facecam" if index else "primary"})
        provider = build_refiner(config, [], usage, workspace / "selection")
        if provider is None or not hasattr(provider, "complete_structured"):
            raise SubtitlerError("Moment selection requires a structured hosted model")
        try:
            matches = select_moments(provider, query, artifact["evidence"], duration_ms, locale=locale,
                record=lambda index, value: write_json_artifact(workspace / f"selection-{index}.json", value))
        finally:
            provider.close()
        for match in matches:
            if (start > 0 or origin > 0) and match["start_ms"] == 0 or (end < duration or spec.get("boundedEnd")) and match["end_ms"] == duration_ms:
                match["boundary_warning"] = "Passage reaches the selected scope boundary; expand the range and rerun for more context."
            for item in [match, *match["detours"]]:
                item["start_ms"] += round(start * 1000)
                item["end_ms"] += round(start * 1000)
        for row in artifact["evidence"]:
            row["start_ms"] += round(start * 1000)
            row["end_ms"] += round(start * 1000)
        artifact["matches"] = matches
        # AviUtl defaults to track zero. Materialize another selected speech track for correct export.
        if audio_track > 0 and media[speech_index]["has_audio"]:
            audio = workspace / "selected-audio.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-ss", str(start), "-i", media[speech_index]["path"],
                            "-t", str(end - start), "-map", f"0:a:{audio_track}", "-vn", "-c:a", "pcm_s16le", str(audio)], check=True)
            media[speech_index].update(audio_path=str(audio), audio_offset_sec=start)
        if any(row["possible_vfr"] for row in media):
            artifact["frame_rate_warning"] = "Source may use variable frame rate; check EXO cut positions in AviUtl."
            print("Warning: " + artifact["frame_rate_warning"], flush=True)
        export_moments(output.with_suffix(".exo"), artifact, inspection, media, int(config["exo"]["fps"]))
        artifact["status"] = "complete"
    except Exception as exc:
        artifact["status"] = "failed"
        artifact["error"] = str(exc)
        artifact["cost_accounting_complete"] = False
        failed = getattr(exc, "editorial_failure_output", {})
        artifact["api_cost_usd"] += float(failed.get("api_cost_usd", failed.get("cost_usd", 0)))
        raise
    finally:
        artifact["api_cost_usd"] += usage.total_cost_usd
        usage.write_csv(workspace / "selection-usage.csv")
        write_json_artifact(output.with_suffix(".json"), artifact)
        print(f"Recorded API cost: ${artifact['api_cost_usd']:.4f}", flush=True)
        if artifact["status"] == "failed":
            print("Failed-run cost may exclude unfinished visual requests; preserved diagnostics are in the evidence folder.", flush=True)
    write_moment_report(output.with_suffix(".html"), artifact)
    print(f"Complete: {len(artifact['matches'])} excerpts. Report: {output.with_suffix('.html')}\nEXO: {output.with_suffix('.exo')}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audio-track", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        run_moments(json.loads(args.spec), Path(args.config), Path(args.env_file), Path(args.output), args.audio_track)
        return 0
    except (SubtitlerError, ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
