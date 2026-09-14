"""Durable B-roll execution services; prompts and editorial policy stay in broll."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from .api_usage import ApiUsageLedger
from .broll import BrollPlanningProvider, CatalogAsset, CatalogSegment, MissingAssetNeed, build_broll_provider, load_catalog
from .operation_store import ArtifactError, OperationStore
from .web_assets import WebAssetCandidate, discover_web_assets

T = TypeVar("T")
BROLL_OPERATION_VERSION = 1


class BrollSession:
    def __init__(self, config: dict[str, Any], usage: ApiUsageLedger, store: OperationStore | None = None):
        self.config, self.usage, self.store = config, usage, store
        self.provider = str(config["cleanup"]["backend"])
        self.model = str(config["cleanup"]["api_model"])
        self._provider: BrollPlanningProvider | None = None

    def execute(self, operation: str, inputs: Any, produce: Callable[[], Any], decode: Callable[[Any], T]) -> T:
        if self.store is None:
            return decode(produce())
        return self.store.execute(operation, BROLL_OPERATION_VERSION, inputs, produce, decode)

    def complete(self, prompt: str, *, operation: str, response_schema: dict[str, Any] | None = None) -> str:
        def produce() -> str:
            if self._provider is None:
                self._provider = build_broll_provider(self.config, self.usage)
            return self._provider.complete(prompt, operation=operation, response_schema=response_schema)
        return self.execute(operation, {
            "prompt": prompt, "schema": response_schema, "cleanup": self.config["cleanup"],
        }, produce, _text)

    def catalog(self, database_path: Path | None, *, operation: str = "broll_catalog") -> list[CatalogAsset]:
        def produce() -> list[dict[str, Any]]:
            return [{"asset": {**asdict(asset), "path": str(asset.path.resolve())},
                     "size": asset.path.stat().st_size, "modified_ns": asset.path.stat().st_mtime_ns}
                    for asset in load_catalog(database_path)] if database_path else []
        if self.store is None:
            return load_catalog(database_path) if database_path else []
        assets = self.execute(operation, {"database": str(database_path.resolve()) if database_path else None},
                              produce, _catalog)
        return assets

    def discover(self, needs: Sequence[MissingAssetNeed]) -> list[WebAssetCandidate]:
        model = str(self.config["broll"]["web_search_model"])
        return self.execute("broll_web_search", {"needs": [asdict(need) for need in needs], "model": model},
                            lambda: [asdict(item) for item in discover_web_assets(needs, model=model, usage=self.usage)],
                            _web_candidates)

    def close(self) -> None:
        if self._provider is not None:
            self._provider.close()


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a model text response")
    return value


def _catalog(value: Any) -> list[CatalogAsset]:
    if not isinstance(value, list):
        raise ValueError("Expected a catalog snapshot")
    assets = []
    for item in value:
        raw = item["asset"]
        path = Path(raw["path"])
        try:
            stat = path.stat()
        except OSError as exc:
            raise ArtifactError(f"B-roll source is no longer available: {path}") from exc
        if stat.st_size != item["size"] or stat.st_mtime_ns != item["modified_ns"]:
            raise ArtifactError(f"B-roll source changed since planning: {path}; start a fresh run to use the new media")
        assets.append(CatalogAsset(**{
            **raw, "path": path, "tags": tuple(raw["tags"]),
            "segments": tuple(CatalogSegment(**{**segment, "tags": tuple(segment["tags"])}) for segment in raw["segments"]),
        }))
    return assets


def _web_candidates(value: Any) -> list[WebAssetCandidate]:
    if not isinstance(value, list) or any(not isinstance(item, dict) or any(not isinstance(v, str) for v in item.values()) for item in value):
        raise ValueError("Expected web asset candidates")
    return [WebAssetCandidate(**item) for item in value]
