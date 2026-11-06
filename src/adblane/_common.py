"""Validation and cancellation shared by the two wire transports."""
from __future__ import annotations

import math
import re
import time
from threading import Event

from .errors import AdbTimeoutError, CancelledError

MAX_OUTPUT = 64 * 1024 * 1024


def timeout_value(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return float(value)


def output_limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_OUTPUT:
        raise ValueError(f"max_bytes must be an integer in 0..{MAX_OUTPUT}")
    return value


class Cancellation:
    def __init__(self, closed: Event, caller: Event | None = None):
        self.closed, self.caller = closed, caller
        self.io_started = False

    def is_set(self) -> bool:
        return self.closed.is_set() or (self.caller is not None and self.caller.is_set())

    def check(self, deadline: float) -> None:
        if self.is_set():
            raise CancelledError("Operation cancelled")
        if time.monotonic() >= deadline:
            raise AdbTimeoutError("ADB operation timed out")

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            if self.is_set():
                raise CancelledError("Operation cancelled")
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self.closed.wait(min(remaining, 0.05))


def connection_error(message: bytes) -> bool:
    text = message.decode("utf8", "replace").lower()
    return any(part in text for part in (
        "10055", "10054", "10053", "cannot connect", "cannot read",
        "connection reset", "connection refused", "device offline",
        "device not found", "no devices/emulators", "error: closed",
        "protocol fault", "failed to start daemon", "daemon not running",
        "could not read ok from adb",
    )) or bool(re.search(r"device ['\"].+['\"] not found", text))
