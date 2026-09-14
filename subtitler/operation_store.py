"""Versioned operation results and usage within one durable run revision."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar
from uuid import uuid4

from .api_usage import ApiUsageLedger, ApiUsageRow
from .artifact_io import write_json_artifact
from .errors import SubtitlerError

T = TypeVar("T")


class ArtifactError(SubtitlerError):
    """Saved work cannot be trusted; do not hide the failure or repeat paid work."""


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class OperationStore:
    def __init__(self, directory: Path, input_revision: str, usage: ApiUsageLedger, *, restore_usage: bool = True) -> None:
        self.directory = directory
        self.input_revision = input_revision
        self.usage = usage
        directory.mkdir(parents=True, exist_ok=True)
        # Include all completed and failed attempts, even when later inputs change.
        for path in sorted(directory.glob("*.json")) if restore_usage else ():
            record = self._read(path)
            for row in record["usage"]:
                restored = ApiUsageRow(**row)
                self.usage.add(**{key: value for key, value in asdict(restored).items() if key != "request_index"})

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("type") != "operation_result"
                    or value.get("schema_version") != 1 or value.get("input_revision") != self.input_revision
                    or value.get("state") not in ("complete", "failed")
                    or not isinstance(value.get("usage"), list)):
                raise ValueError("unsupported operation artifact")
            if value.get("integrity") != content_digest({k: v for k, v in value.items() if k != "integrity"}):
                raise ValueError("operation artifact contents changed")
            for dependency in value.get("files", []):
                if file_digest(Path(dependency["path"])) != dependency["sha256"]:
                    raise ValueError(f"operation output file changed: {dependency['path']}")
            return value
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise ArtifactError(f"Could not load operation artifact {path}: {exc}") from exc

    def execute(
        self, operation: str, version: int, inputs: Any,
        produce: Callable[[], Any], decode: Callable[[Any], T],
        *, output_files: Callable[[Any], Iterable[Path]] | None = None,
        failure_payload: Callable[[BaseException], Any] | None = None,
    ) -> T:
        identity = json.loads(json.dumps({"operation": operation, "version": version, "inputs": inputs}, allow_nan=False))
        key = content_digest(identity)
        path = self.directory / f"{key}.json"
        if path.exists():
            record = self._read(path)
            try:
                if record["identity"] != identity or record["state"] != "complete":
                    raise ValueError("operation inputs do not match")
                return decode(record["payload"])
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ArtifactError(f"Invalid {operation} result in {path}: {exc}") from exc
        first_row = len(self.usage.rows)
        record = {"type": "operation_result", "schema_version": 1, "input_revision": self.input_revision,
                  "revision_id": uuid4().hex, "identity": identity, "state": "complete"}
        try:
            payload = produce()
            result = decode(payload)
            record["payload"] = payload
            record["files"] = [{"path": str(file.resolve()), "sha256": file_digest(file)}
                               for file in output_files(payload)] if output_files else []
        except BaseException as exc:
            record["state"] = "failed"
            record["failure"] = failure_payload(exc) if failure_payload else None
            self._write(self.directory / f"{key}.failed.{record['revision_id']}.json", record, first_row)
            raise
        self._write(path, record, first_row)
        return result

    def reference(self, operation: str, version: int, inputs: Any) -> dict[str, str]:
        """Reference a completed immutable result without embedding its payload."""
        key = content_digest({"operation": operation, "version": version, "inputs": inputs})
        path = self.directory / f"{key}.json"
        record = self._read(path)
        if record["state"] != "complete":
            raise ArtifactError(f"Operation {operation} has no complete result")
        return {"file": path.name, "revision_id": record["revision_id"], "integrity": record["integrity"]}

    def resolve(self, reference: dict[str, str]) -> Any:
        """Validate both the reference and stored contents before consuming a result."""
        if not isinstance(reference, dict):
            raise ArtifactError("Invalid operation result reference")
        name = reference.get("file", "")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ArtifactError("Invalid operation result path")
        record = self._read(self.directory / name)
        if (record["state"] != "complete" or record["revision_id"] != reference.get("revision_id")
                or record["integrity"] != reference.get("integrity")):
            raise ArtifactError("Operation result reference was replaced")
        return record["payload"]

    def _write(self, path: Path, record: dict[str, Any], first_row: int) -> None:
        record["usage"] = [asdict(row) for row in self.usage.rows[first_row:]]
        record["integrity"] = content_digest(record)
        try:
            write_json_artifact(path, record)
        except (OSError, ValueError, TypeError) as exc:
            raise ArtifactError(f"Could not save operation artifact {path}: {exc}") from exc
