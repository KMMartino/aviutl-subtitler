#!/usr/bin/env python3
"""CLI adapter for the packaged subtitle workflows."""

from __future__ import annotations

import argparse

from subtitler.config import WORKFLOWS
from subtitler.run_context import CliArguments
from subtitler.silence_cut import ENCODER_ARGS
from subtitler.subtitle_workflow import run_subtitle_workflow


def parse_args() -> CliArguments:
    parser = argparse.ArgumentParser(
        description="Generate AviUtl .exo subtitles using one of the supported workflows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Input audio or video file")
    parser.add_argument(
        "--workflow",
        choices=sorted(WORKFLOWS),
        default="local",
        help="Supported workflow to run",
    )
    parser.add_argument("--output", "-o", help="Output .exo file")
    parser.add_argument("--config", help="Workflow config JSON. Defaults to configs/<workflow>.json")
    parser.add_argument("--env-file", default=".env", help="Dotenv-style API key file")
    parser.add_argument("--profile", action="store_true", help="Write diagnostics even if config disables them")
    parser.add_argument("--audio-track", type=int, help="Override config audio track")
    parser.add_argument("--sidecar-dir", help="Diagnostics/intermediate output directory")
    parser.add_argument("--no-sidecars", action="store_true", help="Do not create diagnostic or intermediate sidecar files")
    parser.add_argument("--glossary", help="Glossary text file. Defaults to auto-discovery beside input or project")
    parser.add_argument("--no-glossary", action="store_true", help="Disable glossary loading")
    parser.add_argument("--frontend-protocol", choices=["stdio-v1"], help=argparse.SUPPRESS)
    parser.add_argument("--cut-silence-encoder", choices=sorted(ENCODER_ARGS), help="Encoder preset for Cut silence")
    parser.add_argument("--media-library-db", help="Managed media-library SQLite database")
    parser.add_argument("--transcript-artifact", help="Reuse a matching complete .transcript.json; skip ASR and alignment")
    parser.add_argument("--fresh-run", action="store_true", help="Ignore an interrupted subtitle review and process from scratch")
    return CliArguments(**vars(parser.parse_args()))



def main() -> int:
    return run_subtitle_workflow(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
