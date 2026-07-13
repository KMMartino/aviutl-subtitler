# Code quality baseline

Run the repository quality gates with:

```powershell
.\.venv-win\Scripts\python.exe -m ruff check aviutl_subtitle.py subtitler tests
.\.venv-win\Scripts\python.exe -m mypy
cd frontend
npm run quality
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
