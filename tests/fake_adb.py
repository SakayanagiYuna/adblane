"""Synthetic ADB process double. No network, Android device, or source fixtures.

This implements only the protocol emitted by the library, not an Android shell.
It exercises real subprocess pipes on every supported host platform.
"""
import base64
import re
import shlex
import sys
import time


def payload(command, count):
    if command == "hang":
        time.sleep(60)
    if command.startswith("host-"):
        message = "error: device offline" if command == "host-offline" else "permission denied"
        sys.stderr.write(message)
        sys.stderr.flush()
        raise SystemExit(1)
    if command == "stderr-flood":
        sys.stderr.write("x" * 8192)
        sys.stderr.flush()
    if command == "disconnect":
        sys.stdout.buffer.write(b"partial")
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    if command.startswith(("bytes:", "split:")):
        size = int(command.partition(":")[2])
        data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        return data, 0
    if command.startswith("fail:"):
        return b"synthetic private output", int(command.partition(":")[2])
    if command.startswith("text:"):
        return command.partition(":")[2].encode("utf8"), 0
    if command == "counter":
        return str(count).encode("ascii"), 0
    if command == "get-state":
        return b"device\n", 0
    if command == "stdin-read":
        return sys.stdin.buffer.read(), 0
    return b"abc", 0


def reply(script, count, raw=False):
    token = re.findall(rb"@[0-9a-f]{32}:", script)[-1]
    command = re.search(rb"\( (.*?)\n\)", script, re.DOTALL)[1].decode("utf8")
    command = command.removeprefix("set +o pipefail;\n")
    data, code = payload(command, count)
    status = b"bad" if command == "bad-status" else str(code).encode("ascii")
    if raw:
        tail = b"" if command == "missing-trailer" else b"\n" + token + status + b"\n"
        sys.stdout.buffer.write(data + tail)
    else:
        if command == "bad-base64":
            encoded = b"!@#$"
        elif command == "bad-padding":
            encoded = b"YQ==\nYg=="
        elif command == "incomplete-base64":
            encoded = b"Y"
        else:
            encoded = base64.b64encode(data)
        if command.startswith("split:"):
            encoded = b"\n".join(encoded[index:index+3] for index in range(0, len(encoded), 3))
        sys.stdout.buffer.write(encoded + b"\n")
        if command != "missing-trailer":
            sys.stdout.buffer.write(token + status + b"\n")
    sys.stdout.buffer.flush()


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "-s":
        raise SystemExit(2)
    args = args[2:]
    if args == ["shell", "-T", "sh"]:
        buffer = b""
        count = 0
        for line in sys.stdin.buffer:
            buffer += line
            if re.search(rb"@[0-9a-f]{32}:", line):
                count += 1
                reply(buffer, count)
                buffer = b""
    elif args[0] == "exec-out":
        parts = shlex.split(args[1])
        if parts[:2] != ["sh", "-c"]:
            raise SystemExit(2)
        reply(parts[2].encode("utf8"), 1, raw=True)
    else:
        data, code = payload(args[0], 1)
        sys.stdout.buffer.write(data)
        raise SystemExit(code)


if __name__ == "__main__":
    main()
