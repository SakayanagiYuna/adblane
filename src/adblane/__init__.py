"""Bounded ADB reads and cancellable per-device scheduling."""
from ._version import __version__ as __version__
from .errors import (
    AdbError, AdbConnectionError, AdbTimeoutError, CancelledError, CommandError,
    OutputLimitError, ProtocolError, SessionClosedError, UnexpectedLengthError,
)
from .session import AdbSession, Priority, RetryPolicy
from .stats import OperationStats, SessionStats

__all__ = [
    "AdbSession", "Priority", "RetryPolicy", "AdbError", "AdbConnectionError",
    "AdbTimeoutError", "CancelledError", "CommandError", "OutputLimitError",
    "ProtocolError", "SessionClosedError", "UnexpectedLengthError",
    "OperationStats", "SessionStats",
]
