"""Opt-in read-only device smoke; outputs no serial, path, properties or payloads."""
import argparse
import json

from adblane import AdbSession, CommandError


def check(session):
    if session.run("get-state", timeout=3, max_bytes=128).strip() != b"device":
        raise AssertionError("Device is not ready")
    binary = r"printf '\000\001\177\200\377'"
    expected = bytes([0, 1, 127, 128, 255])
    for mode in ("stream", "raw"):
        if session.read(binary, mode=mode, max_bytes=5, expected_bytes=5, timeout=3) != expected:
            raise AssertionError("Binary bytes changed")
        data = session.read("head -c 131071 /dev/zero", mode=mode,
                            max_bytes=131071, expected_bytes=131071, timeout=3)
        if data != bytes(131071):
            raise AssertionError("Large read changed")
        try:
            session.read("false", mode=mode, timeout=3)
        except CommandError as error:
            if not error.remote or error.returncode != 1:
                raise AssertionError("Unexpected remote status") from error
        else:
            raise AssertionError("Remote failure was hidden")
    if session.read("printf ready", max_bytes=5, timeout=3) != b"ready":
        raise AssertionError("Read after command failure did not recover")
    return dict(passed=True, binary_modes=2, large_read_bytes=131071,
                remote_failure_modes=2, calls=session.stats()["total"]["calls"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    try:
        with AdbSession(args.serial, adb=args.adb) as session:
            result = check(session)
    except Exception as error:
        print(json.dumps(dict(passed=False, error_type=type(error).__name__)))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
