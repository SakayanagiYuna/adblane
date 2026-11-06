"""Public failure categories. Messages do not echo commands or device output."""


class AdbError(Exception):
    """Base class for session and execution failures."""


class CancelledError(AdbError):
    """The caller cancelled an operation, or closed its session."""


class SessionClosedError(AdbError):
    """An operation was submitted after the session was closed."""


class AdbConnectionError(AdbError):
    """The ADB transport failed; an explicitly read-only operation may retry."""


class AdbTimeoutError(AdbConnectionError, TimeoutError):
    """The deadline expired, including queue and channel waiting."""


class ProtocolError(AdbConnectionError):
    """The response was incomplete or malformed; its partial bytes are discarded."""


class CommandError(AdbError):
    """A local ADB or remote shell command returned a nonzero status."""

    def __init__(self, returncode: int, *, remote: bool = True) -> None:
        self.returncode = returncode
        self.remote = remote
        origin = "Remote command" if remote else "ADB client"
        super().__init__(f"{origin} failed (exit {returncode})")


class OutputLimitError(AdbError):
    """Output exceeded a configured bound. This is not retried."""


class UnexpectedLengthError(AdbError):
    """A successful response did not have the expected byte count."""
