"""Static consumer checks; no commands or devices are accessed."""
from pathlib import Path
from threading import Event
from typing import Literal

from adblane import (
    AdbConnectionError, AdbSession, CommandError, OperationStats, Priority,
    RetryPolicy, SessionStats, __version__,
)


class NamedSession(AdbSession):
    def label(self) -> str:
        return "example"


def on_retry(error: AdbConnectionError, attempt: int, delay: float) -> None:
    message: str = str(error)
    number: int = attempt
    seconds: float = delay


def use_public_api(mode: Literal["auto", "stream", "raw"]) -> None:
    version: str = __version__
    policy = RetryPolicy(max_attempts=2, initial_delay=0.1, max_delay=1)
    attempts: int = policy.max_attempts
    delay: float = policy.delay(1)
    stop = Event()
    with NamedSession("example", adb=Path("adb"), retry_policy=policy) as session:
        label: str = session.label()
        data: bytes = session.read("id", mode=mode, timeout=1, max_bytes=64,
                                   expected_bytes=3, priority=Priority.BACKGROUND, cancel=stop)
        state: bytes = session.run("get-state", cancel=stop)
        retried: bytes = session.retry_read(lambda: data, on_retry=on_retry, cancel=stop)
        count: int = session.retry_read(lambda: 42, policy=policy)
        path: Path = session.retry_read(lambda: Path("example"))
        stats: SessionStats = session.stats()
        total: OperationStats = stats["total"]
        calls: int = total["calls"]
        seconds: float = total["elapsed_seconds"]
        for name, bucket in stats["modes"].items():
            mode_name: str = name
            completed: int = bucket["completed"]
        session.close(timeout=1)
    with AdbSession("example", adb=["python", "fake_adb.py"]) as launched:
        result: bytes = launched.run("version")
    error = CommandError(1, remote=False)
    code: int = error.returncode
    remote: bool = error.remote
