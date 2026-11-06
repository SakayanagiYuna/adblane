# adblane

ADB sessions for Python with cancellation, output limits, and foreground/background
scheduling. Python 3.10+, with no third-party runtime dependencies.

[中文说明](https://github.com/SakayanagiYuna/adblane/blob/master/README.zh-CN.md)

## Installation

Install [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)
and make `adb` available on your `PATH`. Then install adblane from this checkout:

```sh
python -m pip install .
```

## Quick start

Choose a device serial from `adb devices`:

```python
from adblane import AdbSession

with AdbSession("YOUR_DEVICE_SERIAL") as device:
    sdk = device.read(
        "getprop ro.build.version.sdk",
        timeout=5,
        max_bytes=4096,
    )
    print(sdk.decode().strip())
```

`read()` returns bytes and checks the remote command's exit status. Share one
session between threads working with the same device. The context manager closes
the session and its local ADB clients when you are done.

## Features

- Foreground and background priorities, with a shared concurrency limit.
- Cancellation through `threading.Event` and timeouts for queued or active calls.
- Output size limits and optional exact-length checks.
- Reusable shell connections for small reads and `exec-out` for larger reads.
- Explicit, bounded retries for read-only operations.

## Documentation

- [API reference and examples](https://github.com/SakayanagiYuna/adblane/blob/master/docs/api.md)
- [Development and testing](https://github.com/SakayanagiYuna/adblane/blob/master/docs/development.md)

## License

[MIT](https://github.com/SakayanagiYuna/adblane/blob/master/LICENSE). Install Android Platform Tools separately.
