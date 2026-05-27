"""Per-request hosted API usage ledger."""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .api_costs import token_cost


@dataclass
class ApiUsageRow:
    provider: str
    model: str
    operation: str
    request_index: int
    chunk_index: int | str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    audio_input_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ApiUsageLedger:
    rows: list[ApiUsageRow] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        chunk_index: int | str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        audio_input_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        if not total_tokens:
            total_tokens = input_tokens + output_tokens
        row = ApiUsageRow(
            provider=provider,
            model=model,
            operation=operation,
            request_index=0,
            chunk_index=chunk_index,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_input_tokens=audio_input_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd
            if cost_usd is not None
            else token_cost(
                provider,
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                audio_input_tokens=audio_input_tokens,
            ),
        )
        with self._lock:
            row.request_index = len(self.rows) + 1
            self.rows.append(row)

    def total_cost_by_provider(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        with self._lock:
            rows = list(self.rows)
        for row in rows:
            totals[row.provider] = totals.get(row.provider, 0.0) + row.cost_usd
        return totals

    def total_cost_by_operation(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        with self._lock:
            rows = list(self.rows)
        for row in rows:
            totals[row.operation] = totals.get(row.operation, 0.0) + row.cost_usd
        return totals

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return sum(row.cost_usd for row in self.rows)

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return sum(row.total_tokens for row in self.rows)

    def by_provider_model(self) -> list[dict[str, int | float | str]]:
        totals: dict[tuple[str, str], dict[str, int | float | str]] = {}
        with self._lock:
            rows = list(self.rows)
        for row in rows:
            key = (row.provider, row.model)
            current = totals.setdefault(
                key,
                {
                    "provider": row.provider,
                    "model": row.model,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "audio_input_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            current["requests"] = int(current["requests"]) + 1
            current["input_tokens"] = int(current["input_tokens"]) + row.input_tokens
            current["output_tokens"] = int(current["output_tokens"]) + row.output_tokens
            current["audio_input_tokens"] = int(current["audio_input_tokens"]) + row.audio_input_tokens
            current["total_tokens"] = int(current["total_tokens"]) + row.total_tokens
            current["cost_usd"] = float(current["cost_usd"]) + row.cost_usd
        return list(totals.values())

    def write_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "provider",
                    "model",
                    "operation",
                    "request_index",
                    "chunk_index",
                    "input_tokens",
                    "output_tokens",
                    "audio_input_tokens",
                    "total_tokens",
                    "cost_usd",
                ],
            )
            writer.writeheader()
            with self._lock:
                rows = list(self.rows)
            for row in rows:
                data = row.__dict__.copy()
                data["cost_usd"] = f"{row.cost_usd:.8f}"
                writer.writerow(data)
