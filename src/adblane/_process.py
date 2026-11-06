"""Bounded one-shot subprocess I/O; never manages the shared ADB server."""
from __future__ import annotations

import io
import queue
import shlex
import subprocess
import sys
import threading
import time
import uuid

from ._common import connection_error
from ._pipes import PipeReader, read_chunks
from .errors import AdbConnectionError, CommandError, OutputLimitError, ProtocolError


def shutdown(process, threads=()):
    """Reap only our child. Broken Windows stdin flushes must not mask errors."""
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
    finally:
        end = time.monotonic() + 3
        while any(thread.is_alive() for thread in threads) and time.monotonic() < end:
            for thread in threads:
                if thread.is_alive():
                    try:
                        thread.cancel_io()
                    except OSError:
                        pass  # Still bound the wait if Windows rejects cancellation.
                    thread.join(timeout=min(0.05, max(0, end - time.monotonic())))
        live_pipes = {thread.pipe for thread in threads if thread.is_alive()}
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and pipe not in live_pipes:
                try:
                    pipe.close()
                except OSError:
                    pass
        if live_pipes and sys.exc_info()[0] is None:
            raise ProtocolError("Timed out stopping ADB pipe readers")


def collect(argv, cancellation, deadline, limit, metrics):
    cancellation.check(deadline)
    cancellation.io_started = True
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    metrics["client_starts"] += 1
    chunks, closed = queue.Queue(maxsize=16), threading.Event()

    def drain(pipe, channel):
        try:
            for data in read_chunks(pipe, closed):
                while not closed.is_set():
                    try:
                        chunks.put((channel, data), timeout=0.05)
                        break
                    except queue.Full:
                        pass
                if not data:
                    break
        except (OSError, ValueError):
            while not closed.is_set():
                try:
                    chunks.put((channel, None), timeout=0.05)
                    break
                except queue.Full:
                    pass

    threads = [PipeReader(pipe, target=drain, args=(pipe, channel), name="adblane-output")
               for channel, pipe in enumerate((process.stdout, process.stderr))]
    output, errors, ended = io.BytesIO(), bytearray(), set()
    try:
        for thread in threads:
            thread.start()
        while len(ended) < 2 or process.poll() is None:
            cancellation.check(deadline)
            try:
                channel, data = chunks.get(timeout=0.05)
            except queue.Empty:
                continue
            if data is None:
                raise ProtocolError("ADB output pipe failed")
            if not data:
                ended.add(channel)
                continue
            metrics["received_bytes"] += len(data)
            if channel:
                if len(errors) + len(data) > 4096:
                    raise OutputLimitError("ADB stderr exceeded 4096 bytes")
                errors.extend(data)
            else:
                if output.tell() + len(data) > limit:
                    raise OutputLimitError("ADB stdout exceeded max_bytes")
                output.write(data)
        cancellation.check(deadline)
        if process.returncode:
            if connection_error(bytes(errors)):
                raise AdbConnectionError("ADB transport failed")
            raise CommandError(process.returncode, remote=False)
        return output.getvalue()
    finally:
        closed.set()
        shutdown(process, threads)


def raw_read(argv, command, cancellation, deadline, limit, metrics):
    token = ("\n@" + uuid.uuid4().hex + ":").encode("ascii")
    # Remote stderr is deliberately excluded from binary payloads. A command
    # failure is represented by its exit status, without echoing output in errors.
    marker = token.decode("ascii").replace("\n", "\\n")
    script = f'( {command}\n) 2>/dev/null; result=$?; printf "{marker}%s\\n" "$result"'
    data = collect([*argv, "exec-out", shlex.join(["sh", "-c", script])],
                   cancellation, deadline, limit + len(token) + 4, metrics)
    payload, separator, status = data.rpartition(token)
    if (not separator or not status.endswith(b"\n") or not status[:-1].isdigit()
            or not 2 <= len(status) <= 4 or int(status[:-1]) > 255):
        raise ProtocolError("Incomplete ADB response trailer")
    if status != b"0\n":
        raise CommandError(int(status[:-1]))
    if len(payload) > limit:
        raise OutputLimitError("ADB stdout exceeded max_bytes")
    return payload
