"""Read a caller-selected device property; no device identifier is stored."""
import argparse
import re
import shlex

from adblane import AdbSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("property", nargs="?", default="ro.build.version.sdk")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.property):
        parser.error("invalid property name")
    with AdbSession(args.serial, adb=args.adb) as device:
        result = device.read("getprop " + shlex.quote(args.property), max_bytes=4096)
        print(result.decode("utf8", "replace").strip())


if __name__ == "__main__":
    main()
