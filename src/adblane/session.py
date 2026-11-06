"""Explicitly owned, thread-safe ADB sessions with two scheduling priorities."""
from __future__ import annotations

import contextlib
import math
import os
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from types import TracebackType
from typing import Callable, Literal, Sequence, TypeVar

from ._common import Cancellation, output_limit, timeout_value
from ._metrics import Metrics, empty
from ._process import collect, raw_read
from ._stream import ShellStream
from .errors import (
    AdbConnectionError, AdbTimeoutError, CancelledError,
    SessionClosedError, UnexpectedLengthError,
)
from .stats import SessionStats

T = TypeVar("T")
_SessionT = TypeVar("_SessionT", bound="AdbSession")


class Priority(IntEnum):
    FOREGROUND = 0
    BACKGROUND = 1


@dataclass(frozen=True)
class RetryPolicy:
    """Finite retries, including the initial attempt. Delay is in seconds."""
    max_attempts: int = 3
    initial_delay: float = 0.5
    max_delay: float = 5.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for value in (self.initial_delay, self.max_delay):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("retry delays must be finite and nonnegative")
        if self.max_delay < self.initial_delay:
            raise ValueError("max_delay must be at least initial_delay")

    def delay(self, failures: int) -> float:
        return min(self.max_delay, self.initial_delay * 2 ** min(max(0, failures - 1), 60))


class AdbSession:
    """Share one instance among operations on one device; close it explicitly.

    No global registry, device discovery, implicit reconnect, or root changes.
    A sequence for ``adb`` supports launchers, e.g. a test double invoked by Python.
    """

    def __init__(self, serial: str, *, adb: str | os.PathLike[str] | Sequence[str] = "adb",
                 max_concurrent: int = 2, max_background: int = 1,
                 retry_policy: RetryPolicy | None = None) -> None:
        if not isinstance(serial, str) or not serial or "\0" in serial:
            raise ValueError("serial must be a nonempty string without NUL")
        if (type(max_concurrent) is not int or max_concurrent < 1
                or type(max_background) is not int or not 1 <= max_background <= max_concurrent):
            raise ValueError("require 1 <= max_background <= max_concurrent")
        prefix = [os.fspath(adb)] if isinstance(adb, (str, os.PathLike)) else list(adb)
        if not prefix or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in prefix):
            raise ValueError("adb must be an executable path or a nonempty argument sequence")
        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):
            raise TypeError("retry_policy must be a RetryPolicy")
        self._argv = [*prefix, "-s", serial]
        self._capacity, self._background_limit = max_concurrent, max_background
        self._policy = retry_policy or RetryPolicy()
        self._condition = threading.Condition()
        self._closed = threading.Event()
        self._sequence = self._active = self._background = 0
        self._pending = []
        self._failures = 0
        self._retry_at = 0.0
        self._streams = {priority: ShellStream(self._argv) for priority in Priority}
        self._metrics = Metrics()

    def __enter__(self: _SessionT) -> _SessionT:
        if self._closed.is_set():
            raise SessionClosedError("Session is closed")
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> None:
        try:
            self.close()
        except Exception:
            if kind is None:
                raise

    def close(self, *, timeout: float = 5.0) -> None:
        """Cancel queued/active calls and reap our shell children. Idempotent."""
        deadline = time.monotonic() + timeout_value(timeout)
        with self._condition:
            self._closed.set()
            self._condition.notify_all()
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdbTimeoutError("Timed out closing active operations")
                self._condition.wait(min(remaining, 0.05))
        for stream in self._streams.values():
            remaining = max(0, deadline - time.monotonic())
            if not stream.lock.acquire(timeout=remaining):
                raise AdbTimeoutError("Timed out closing a shell channel")
            try:
                stream.close()
            finally:
                stream.lock.release()

    def stats(self) -> SessionStats:
        """Return detached numeric snapshots without commands or device output."""
        return self._metrics.snapshot()

    @contextlib.contextmanager
    def _slot(self, cancellation, priority, deadline):
        with self._condition:
            self._sequence += 1
            ticket = (priority, self._sequence)
            self._pending.append(ticket)
            try:
                while True:
                    cancellation.check(deadline)
                    eligible = [item for item in self._pending
                                if item[0] == Priority.FOREGROUND
                                or self._background < self._background_limit]
                    capacity = 1 if self._failures else self._capacity
                    if (time.monotonic() >= self._retry_at and self._active < capacity
                            and eligible and ticket == min(eligible)):
                        self._active += 1
                        self._background += priority == Priority.BACKGROUND
                        break
                    self._condition.wait(min(0.05, max(0, deadline - time.monotonic())))
            finally:
                self._pending.remove(ticket)
                self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._background -= priority == Priority.BACKGROUND
                self._condition.notify_all()

    def _failed(self):
        with self._condition:
            now = time.monotonic()
            # Simultaneous failures share one cooldown interval.
            if now >= self._retry_at:
                self._failures += 1
                self._retry_at = now + self._policy.delay(self._failures)
            self._condition.notify_all()

    def _recovered(self):
        with self._condition:
            self._failures, self._retry_at = 0, 0.0
            self._condition.notify_all()

    def _call(self, mode, action, *, timeout, priority, cancel):
        timeout = timeout_value(timeout)
        if (not isinstance(priority, Priority) and type(priority) is not int
                or priority not in (Priority.FOREGROUND, Priority.BACKGROUND)):
            raise ValueError("priority must be FOREGROUND or BACKGROUND")
        priority = Priority(priority)
        if cancel is not None and not isinstance(cancel, threading.Event):
            raise TypeError("cancel must be a threading.Event")
        if self._closed.is_set():
            raise SessionClosedError("Session is closed")
        started = time.perf_counter()
        deadline = time.monotonic() + timeout
        cancellation = Cancellation(self._closed, cancel)
        values = empty()
        values["calls"] = 1
        acquired = None
        try:
            with self._slot(cancellation, priority, deadline):
                acquired = time.perf_counter()
                values["queue_seconds"] = acquired - started
                result = action(cancellation, deadline, values, priority)
                cancellation.check(deadline)
                self._recovered()
            values["completed"], values["bytes"] = 1, len(result)
            return result
        except CancelledError:
            values["cancelled"] = 1
            raise
        except BaseException as error:
            values["failed"] = 1
            if isinstance(error, AdbConnectionError) and cancellation.io_started:
                values["connection_errors"] = 1
                # Neither queue nor channel-lock waiting establishes a broken connection.
                self._failed()
            raise
        finally:
            elapsed = time.perf_counter() - started
            values["elapsed_seconds"] = elapsed
            if acquired is None:
                values["queue_seconds"] = elapsed
            else:
                values["io_seconds"] = max(0, elapsed - values["queue_seconds"] - values["channel_seconds"])
            self._metrics.add(f"{mode}.{priority.name.lower()}", values)

    def run(self, *args: str, timeout: float = 12.0, max_bytes: int = 1024 * 1024,
            priority: Priority = Priority.FOREGROUND, cancel: threading.Event | None = None) -> bytes:
        """Run ADB arguments once, with bounded stdout/stderr. Never auto-retry.

        For remote exit-status validation, use read() with a read-only shell command.
        run() reports the ADB client's status; older clients may hide remote status.
        """
        limit = output_limit(max_bytes)
        if not args or any(not isinstance(arg, str) or "\0" in arg for arg in args):
            raise ValueError("provide ADB arguments without NUL")
        return self._call("run", lambda token, end, values, _p:
                          collect([*self._argv, *args], token, end, limit, values),
                          timeout=timeout, priority=priority, cancel=cancel)

    def read(self, command: str, *, timeout: float = 12.0, max_bytes: int = 1024 * 1024,
             expected_bytes: int | None = None, mode: Literal["auto", "stream", "raw"] = "auto",
             priority: Priority = Priority.FOREGROUND, cancel: threading.Event | None = None) -> bytes:
        """Read a trusted shell command, returning only a complete valid response.

        The caller is responsible for read-only semantics and shell quoting.
        stderr is excluded from payloads. Command failures are never auto-retried.
        """
        limit = output_limit(max_bytes)
        if not isinstance(command, str) or not command.strip() or "\0" in command:
            raise ValueError("command must be a nonempty shell command without NUL")
        length = len(command.encode("utf8"))
        if length > 65536:
            raise ValueError("shell command exceeds 65536 UTF-8 bytes")
        if expected_bytes is not None:
            if type(expected_bytes) is not int or not 0 <= expected_bytes <= limit:
                raise ValueError("expected_bytes must be an integer within max_bytes")
        if mode not in ("auto", "stream", "raw"):
            raise ValueError("mode must be auto, stream, or raw")
        if mode == "auto":
            mode = "raw" if limit >= 4 * 1024 * 1024 or length > 3500 else "stream"
        if mode == "stream" and length > 3500:
            raise ValueError("stream mode commands are limited to 3500 UTF-8 bytes")

        def action(token, end, values, lane):
            if mode == "raw":
                data = raw_read(self._argv, command, token, end, limit, values)
            else:
                data = self._streams[lane].read(command, token, end, limit, values)
            if expected_bytes is not None and len(data) != expected_bytes:
                raise UnexpectedLengthError("Response length differs from expected_bytes")
            return data

        return self._call(mode, action, timeout=timeout, priority=priority, cancel=cancel)

    def retry_read(self, action: Callable[[], T], *, policy: RetryPolicy | None = None,
                   cancel: threading.Event | None = None,
                   on_retry: Callable[[AdbConnectionError, int, float], None] | None = None) -> T:
        """Replay a caller-declared read-only action after transport errors only.

        Each attempt must have its own bounded timeout. No reconnect or writes
        are injected. on_retry receives (error, next_attempt_number, delay).
        """
        if not callable(action):
            raise TypeError("action must be callable")
        if on_retry is not None and not callable(on_retry):
            raise TypeError("on_retry must be callable")
        if policy is not None and not isinstance(policy, RetryPolicy):
            raise TypeError("policy must be a RetryPolicy")
        if cancel is not None and not isinstance(cancel, threading.Event):
            raise TypeError("cancel must be a threading.Event")
        if self._closed.is_set():
            raise SessionClosedError("Session is closed")
        policy = policy or self._policy
        cancellation = Cancellation(self._closed, cancel)
        for attempt in range(1, policy.max_attempts + 1):
            cancellation.wait(0)
            try:
                return action()
            except AdbConnectionError as error:
                if attempt == policy.max_attempts:
                    raise
                delay = policy.delay(attempt)
                if on_retry is not None:
                    on_retry(error, attempt + 1, delay)
                cancellation.wait(delay)
                self._metrics.add("retry", {"retries": 1})
        raise AssertionError("unreachable")
