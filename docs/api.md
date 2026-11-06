# API reference

Import the public API from `adblane`.

## AdbSession

```text
AdbSession(
    serial, *, adb="adb", max_concurrent=2, max_background=1,
    retry_policy=None,
)
```

`serial` selects the device. `adb` accepts an executable path or an argument
sequence for a launcher. `retry_policy` defaults to `RetryPolicy()`.

Share the same instance across threads that should share scheduling. The default
limits allow two concurrent calls, including at most one background call.
`Priority.FOREGROUND` takes precedence over queued `Priority.BACKGROUND` work;
running commands are not preempted. Separate sessions have independent state.

Use a context manager or call `close(timeout=5)`. Closing cancels pending and active
calls and reaps the session's local clients. It is idempotent; new calls afterward
raise `SessionClosedError`.

## Reading shell output

```text
session.read(
    command, *, timeout=12, max_bytes=1048576, expected_bytes=None,
    mode="auto", priority=Priority.FOREGROUND, cancel=None,
)
```

Returns `bytes` after a complete, successful response. Remote stderr is discarded.
Pass trusted shell commands; quote dynamic values with `shlex.quote`.

| Option | Meaning |
| --- | --- |
| `timeout` | Positive seconds, including queue and channel waiting. Process startup and cleanup may add OS latency. |
| `max_bytes` | Maximum returned payload, from 0 to 64 MiB. Exceeding it raises `OutputLimitError`. |
| `expected_bytes` | Optional exact length, within `max_bytes`. A mismatch raises `UnexpectedLengthError`. |
| `mode` | `"auto"`, `"stream"`, or `"raw"`; see below. |
| `priority` | `Priority.FOREGROUND` or `Priority.BACKGROUND`. |
| `cancel` | A `threading.Event`; setting it cancels a waiting or active call. |

`auto` uses a reusable shell for small requests and one-shot `exec-out` for larger
ones. `stream` requires device-side `sh` with `pipefail` and `base64 -w 0`;
choose `raw` if those utilities are unavailable. Shell commands are limited to
65536 UTF-8 bytes, or 3500 in explicit stream mode. The host may impose a smaller
command-line limit. In both modes, pipelines use the last command's exit status
unless the command explicitly enables `pipefail`.

## Running ADB arguments

```text
session.run(
    *args, timeout=12, max_bytes=1048576,
    priority=Priority.FOREGROUND, cancel=None,
)
```

Runs ADB arguments once and returns bounded stdout as `bytes`, for example
`session.run("get-state")`. It checks the local ADB client's exit status; use
`read()` when you need independent validation of a remote command's status.
Local stderr has a separate 4096-byte limit.

## Retrying a read

Calls are not retried automatically. Wrap a read-only action with `retry_read()`:

```python
from adblane import AdbSession, RetryPolicy

with AdbSession("YOUR_DEVICE_SERIAL") as device:
    data = device.retry_read(
        lambda: device.read("getprop ro.product.cpu.abi", timeout=5),
        policy=RetryPolicy(max_attempts=3),
    )
```

`RetryPolicy(max_attempts=3, initial_delay=0.5, max_delay=5)` controls the total
number of attempts, including the first, and exponential delays in seconds.
`retry_read(action, *, policy=None, cancel=None, on_retry=None)` uses the session's
policy by default. The optional callback receives `(error, next_attempt, delay)`.

Only `AdbConnectionError` and its subclasses trigger another attempt. The entire
action is replayed, so use this only for operations that are safe to repeat.
Each call inside the action has its own timeout. For cancellation during both an
attempt and the delay between attempts, pass the same event to `retry_read()`
and the calls inside the action.

## Statistics

`stats()` returns a detached `SessionStats` snapshot. `total` is an
`OperationStats` dictionary; `modes` maps transport/priority names to dictionaries
of the same type. Both types are exported from `adblane`.
Counters include calls, completions, failures, cancellations, retries, client
starts, completed payload bytes, received transport bytes, and timings in seconds.
Changing the snapshot does not affect the session.

## Exceptions

All library errors inherit from `AdbError`.

| Exception | Meaning |
| --- | --- |
| `AdbConnectionError` | A transport failure eligible for explicit retry. |
| `ProtocolError` | An incomplete or malformed response; inherits from `AdbConnectionError`. |
| `AdbTimeoutError` | The call's deadline expired; also inherits from `AdbConnectionError` and `TimeoutError`. |
| `CommandError` | A nonzero exit status. `returncode` holds the status; `remote` identifies a remote command failure. |
| `OutputLimitError` | Output exceeded its configured limit. |
| `UnexpectedLengthError` | A successful response differed from `expected_bytes`. |
| `CancelledError` | The caller cancelled the operation or closed its session. |
| `SessionClosedError` | A new operation was submitted to a closed session. |

Invalid arguments raise `ValueError` or `TypeError`. Launching the ADB executable
can also raise `OSError`.
