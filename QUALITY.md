# Code quality baseline

Run the repository quality gates with:

```powershell
.\.venv-win\Scripts\python.exe -m ruff check aviutl_subtitle.py subtitler tests
.\.venv-win\Scripts\python.exe -m mypy
.\.venv-win\Scripts\python.exe -m unittest discover -s tests
cd frontend
npm run quality
npm test
```

Ruff currently enforces Python syntax, undefined-name, unused-import, and related correctness rules across the CLI, backend, and tests. Mypy starts with the typed configuration, cost, and backend-contract modules listed in `pyproject.toml`; add modules as their boundaries are made type-clean.

Frontend ESLint covers all TypeScript and TSX sources. Prettier initially checks the package/configuration files and scripts listed in `frontend/package.json`; application-source formatting remains existing-style to avoid a repository-wide mechanical rewrite. Expand the Prettier file set when a component boundary is deliberately reformatted.

## Coverage baseline

Coverage is reported as a diagnostic baseline and does not currently enforce a
percentage threshold. Generate both reports with:

```powershell
.\.venv-win\Scripts\python.exe -m coverage run -m unittest discover -s tests
.\.venv-win\Scripts\python.exe -m coverage report
.\.venv-win\Scripts\python.exe -m coverage html
cd frontend
npm run test:coverage
```

The Python HTML report is written to `coverage-html/`; the frontend report is
written to `frontend/coverage/`. Use the missing-line reports to choose useful
tests around changed or high-risk boundaries. Do not add repository-wide
thresholds until the legacy baseline and architecture have stabilized.

## Transcription and split trial baseline

The July 2026 segment-length trials use `testing-grounds/1069.mkv` and run jobs serially.

- GPT Transcribe passed with three larger VAD groups instead of 21 fine chunks. Hosted split planning now selects compact legal boundary IDs rather than reproducing the transcript, validates selected spans against aligned tokens, and recursively handles any remainder.
- Gemini transcription stays on 30-second fine chunks; longer groups changed spoken numeric ranges and reduced useful subtitle structure.
- Local Gemma is hard-capped at 30 seconds. The 60-second trial made worse long-context proper-name substitutions on the 16 GB profile, so its packing, density heuristic, and retained-atom fallback were removed rather than carried as an experimental mode; the 12 GB gate was not run.
- The boundary-ID smoke used the same 108-character punctuation-free Japanese passage for both planners. `gpt-5.4-mini` selected three hosted zones in one request (450 input / 12 output tokens, $0.0003915) and produced lengths `39/21/34/14`; the installed local 12B cleanup model used three one-ID requests and produced `36/28/28/16`. Both preserved the source exactly and met the 40-character/6-second limits.
- The Japanese-boundary regression set covers contiguous katakana/kanji runs, auxiliary phrases, numeric ranges and units, Japanese dates, and colon/slash/hyphen date-time forms. Re-audit the fixed aligned GPT artifact when changing candidate classification so previously observed seams cannot silently return.
- Duplicate hosted IDs from the same frontier zone are alternatives, not additional cuts. The regression suite verifies that whole-path scoring preserves the source, respects limits, and selects balanced boundaries instead of retaining whichever same-zone ID appeared first.

When evaluating a segmentation change, compare transcript completeness and proper names before cleanup, final subtitle character/duration limits, recovery count, wall-clock time, request count, and recorded API cost. A lower segment or request count is not a quality improvement by itself.
