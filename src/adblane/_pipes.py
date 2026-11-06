"""Owned pipe readers with interruptible shutdown, including inherited writers."""
from __future__ import annotations

import os
import selectors
import threading

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel.OpenThread.restype = wintypes.HANDLE
    _kernel.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
    _kernel.CancelSynchronousIo.restype = wintypes.BOOL
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.CloseHandle.restype = wintypes.BOOL


class PipeReader(threading.Thread):
    def __init__(self, pipe, *, target, args=(), name):
        super().__init__(target=target, args=args, name=name, daemon=True)
        self.pipe = pipe
        self._handle = None
        self._handle_lock = threading.Lock()

    def prepare(self):
        if os.name == "nt":
            # Open from the worker itself, so an exited thread's reused native
            # ID can never identify another application's thread by mistake.
            with self._handle_lock:
                self._handle = _kernel.OpenThread(0x0001, False, threading.get_native_id())
                if not self._handle:
                    raise ctypes.WinError(ctypes.get_last_error())

    def cancel_io(self):
        if os.name == "nt":
            with self._handle_lock:
                if self._handle and not _kernel.CancelSynchronousIo(self._handle):
                    error = ctypes.get_last_error()
                    if error != 1168:  # ERROR_NOT_FOUND: no read is pending yet/already finished.
                        raise ctypes.WinError(error)

    def run(self):
        try:
            super().run()
        finally:
            # The worker closes its own pipe after leaving read(), never while
            # another thread owns the buffered I/O lock.
            try:
                self.pipe.close()
            except OSError:
                pass
            finally:
                with self._handle_lock:
                    if self._handle is not None:
                        _kernel.CloseHandle(self._handle)
                        self._handle = None


def read_chunks(pipe, stopped):
    if os.name == "nt":
        threading.current_thread().prepare()
        while not stopped.is_set():
            data = pipe.read1(65536)
            yield data
            if not data:
                return
    else:
        fd = pipe.fileno()
        os.set_blocking(fd, False)
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while not stopped.is_set():
                if not selector.select(0.05):
                    continue
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                yield data
                if not data:
                    return


def read_lines(pipe, stopped):
    pending = bytearray()
    for data in read_chunks(pipe, stopped):
        if not data:
            if pending:
                yield bytes(pending)
            yield b""
            return
        pending.extend(data)
        start = 0
        while start < len(pending):
            end = pending.find(b"\n", start, start + 65536)
            if end >= 0:
                end += 1
            elif len(pending) - start >= 65536:
                end = start + 65536
            else:
                break
            yield bytes(pending[start:end])
            start = end
        del pending[:start]
