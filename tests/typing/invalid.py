"""Each ignore must suppress a real error, or --strict fails the check."""
from adblane import AdbSession, RetryPolicy


def reject_invalid_api_usage(session: AdbSession) -> None:
    AdbSession(123)  # type: ignore[arg-type]
    AdbSession("example", adb=[123])  # type: ignore[list-item]
    RetryPolicy(max_attempts="3")  # type: ignore[arg-type]
    session.read("id", mode="invalid")  # type: ignore[arg-type]
    session.read(b"id")  # type: ignore[arg-type]
    session.read("id", cancel=True)  # type: ignore[arg-type]
    session.run(1)  # type: ignore[arg-type]
    text: str = session.read("id")  # type: ignore[assignment]
    session.stats()["total"]["calls"] = "one"  # type: ignore[typeddict-item]
    number = session.retry_read(lambda: 1)
    number_text: str = number  # type: ignore[assignment]
