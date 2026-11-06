"""Framed reads over a reusable Android shell; never caches response bytes."""
from __future__ import annotations

import base64
import binascii
import io
import queue
import subprocess
import threading
import time
import uuid

from ._process import shutdown
from ._pipes import PipeReader, read_lines
from .errors import AdbError, CommandError, OutputLimitError, ProtocolError


class ShellStream:
    def __init__(self, argv):
        self.argv = argv
        self.process = None
        self.lock = threading.Lock()

    def _open(self):
        self.process = subprocess.Popen(
            [*self.argv, "shell", "-T", "sh"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.closed = threading.Event()
        self.lines = queue.Queue(maxsize=16)
        process, closed, lines = self.process, self.closed, self.lines

        def drain():
            try:
                for line in read_lines(process.stdout, closed):
                    while not closed.is_set():
                        try:
                            lines.put(line, timeout=0.05)
                            break
                        except queue.Full:
                            pass
                    if not line:
                        break
            except (OSError, ValueError):
                while not closed.is_set():
                    try:
                        lines.put(b"", timeout=0.05)
                        break
                    except queue.Full:
                        pass

        self.reader = PipeReader(process.stdout, target=drain, name="adblane-shell")
        self.reader.start()

    def close(self):
        """Caller owns lock, including when shutting down an idle session."""
        process = self.process
        if process is None:
            return
        self.process = None
        self.closed.set()
        shutdown(process, (self.reader,))

    def read(self, command, cancellation, deadline, limit, metrics):
        started = time.perf_counter()
        try:
            while True:
                cancellation.check(deadline)
                if self.lock.acquire(timeout=0.05):
                    break
        finally:
            metrics["channel_seconds"] += time.perf_counter() - started
        try:
            cancellation.check(deadline)
            cancellation.io_started = True
            if self.process is None:
                self._open()
                metrics["client_starts"] += 1
            token = ("@" + uuid.uuid4().hex + ":").encode("ascii")
            # Check both sides of the encoding pipeline without imposing
            # pipefail on the caller's program. Its subshell also isolates exit
            # and environment changes from the reusable shell.
            script = ('set -o pipefail; ( set +o pipefail;\n' + command + '\n) 2>/dev/null | base64 -w 0; '
                      'printf "\\n' + token.decode("ascii") + '%s\\n" "$?"\n')
            self.process.stdin.write(script.encode("utf8"))
            self.process.stdin.flush()
            decoded = io.BytesIO()
            pending, padded, encoded_size = b"", False, 0
            while True:
                cancellation.check(deadline)
                try:
                    line = self.lines.get(timeout=0.05)
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise ProtocolError("ADB shell disconnected")
                    continue
                if not line:
                    raise ProtocolError("ADB shell disconnected")
                metrics["received_bytes"] += len(line)
                line = line.rstrip(b"\r\n")
                if line.startswith(token):
                    status = line[len(token):]
                    if not status.isdigit() or not 1 <= len(status) <= 3 or int(status) > 255:
                        raise ProtocolError("Invalid ADB response status")
                    break
                encoded_size += len(line)
                if encoded_size > ((limit + 2) // 3) * 4:
                    raise OutputLimitError("ADB stdout exceeded max_bytes")
                if not line:
                    continue
                if padded:
                    raise ProtocolError("Data after base64 padding")
                block = pending + line
                end = len(block) // 4 * 4
                pending = block[end:]
                if end:
                    piece = block[:end]
                    try:
                        decoded.write(base64.b64decode(piece, validate=True))
                    except binascii.Error as error:
                        raise ProtocolError("Invalid base64 response") from error
                    padded = b"=" in piece
                if decoded.tell() > limit:
                    raise OutputLimitError("ADB stdout exceeded max_bytes")
            if pending:
                raise ProtocolError("Incomplete base64 response")
            if status != b"0":
                raise CommandError(int(status))
            return decoded.getvalue()
        except AdbError:
            self.close()
            raise
        except OSError as error:
            self.close()
            raise ProtocolError("ADB shell pipe failed") from error
        except BaseException:
            self.close()
            raise
        finally:
            self.lock.release()
