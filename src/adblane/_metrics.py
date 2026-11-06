"""Numeric counters only: no device identifiers, commands, or payloads."""
import threading
from typing import Mapping, cast

from .stats import OperationStats, SessionStats

FIELDS = (
    "calls", "completed", "failed", "cancelled", "connection_errors", "bytes",
    "received_bytes", "client_starts", "retries", "queue_seconds",
    "channel_seconds", "io_seconds", "elapsed_seconds",
)


def empty() -> dict[str, int | float]:
    return dict.fromkeys(FIELDS, 0)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = empty()
        self._modes: dict[str, dict[str, int | float]] = {}

    def add(self, mode: str, values: Mapping[str, int | float]) -> None:
        with self._lock:
            bucket = self._modes.setdefault(mode, empty())
            for key in FIELDS:
                amount = values.get(key, 0)
                self._total[key] += amount
                bucket[key] += amount

    def snapshot(self) -> SessionStats:
        with self._lock:
            # All buckets share FIELDS; integer counters and duration fields
            # are populated by the session and transport implementations.
            return {"total": cast(OperationStats, dict(self._total)),
                    "modes": {key: cast(OperationStats, dict(value))
                              for key, value in self._modes.items()}}
