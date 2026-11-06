"""Types for the detached snapshots returned by AdbSession.stats()."""
from typing import TypedDict


class OperationStats(TypedDict):
    """Counters and durations for a session or one transport/priority bucket."""
    calls: int
    completed: int
    failed: int
    cancelled: int
    connection_errors: int
    bytes: int
    received_bytes: int
    client_starts: int
    retries: int
    queue_seconds: float
    channel_seconds: float
    io_seconds: float
    elapsed_seconds: float


class SessionStats(TypedDict):
    """A snapshot whose dictionaries can be modified without changing the session."""
    total: OperationStats
    modes: dict[str, OperationStats]
