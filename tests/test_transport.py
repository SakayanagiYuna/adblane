"""Real OS pipes with a synthetic Python child; no ADB binary required."""
import sys
import io
import subprocess
import threading
import time
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import Mock, patch

from adblane import (
    AdbConnectionError, AdbSession, AdbTimeoutError, CancelledError, CommandError,
    OperationStats, OutputLimitError, Priority, ProtocolError, RetryPolicy,
    SessionClosedError, SessionStats, UnexpectedLengthError,
)

FAKE = [sys.executable, "-I", "-B", str(Path(__file__).with_name("fake_adb.py"))]


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError("test condition timed out")
        time.sleep(0.01)


class TransportTests(unittest.TestCase):
    def session(self):
        session = AdbSession("synthetic-device", adb=FAKE,
                             retry_policy=RetryPolicy(3, 0, 0))
        self.addCleanup(session.close)
        return session

    def test_200_reads_share_child_but_never_cache_results(self):
        session = self.session()
        for index in range(1, 201):
            self.assertEqual(session.read("counter"), str(index).encode())
        stats = session.stats()["total"]
        self.assertEqual((stats["completed"], stats["client_starts"]), (200, 1))
        stream = session._streams[Priority.FOREGROUND]
        process = stream.process
        session.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(stream.reader.is_alive())
        self.assertTrue(process.stdout.closed)

    def test_binary_bytes_and_empty_payloads_in_both_modes(self):
        session = self.session()
        for mode in ("stream", "raw"):
            for size in (0, 1, 2, 3, 255, 65536, 150001):
                with self.subTest(mode=mode, size=size):
                    expected = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
                    self.assertEqual(session.read(f"bytes:{size}", mode=mode,
                                                  max_bytes=size, expected_bytes=size), expected)

    def test_base64_quartets_may_span_transport_lines(self):
        session = self.session()
        self.assertEqual(session.read("split:513", max_bytes=513), bytes(range(256)) * 2 + b"\0")

    def test_corrupt_stream_closes_and_next_read_gets_fresh_channel(self):
        for command in ("bad-base64", "bad-padding", "incomplete-base64", "bad-status", "disconnect"):
            with self.subTest(command=command):
                session = self.session()
                with self.assertRaises(ProtocolError):
                    session.read(command)
                self.assertIsNone(session._streams[Priority.FOREGROUND].process)
                self.assertEqual(session.read("text:ok"), b"ok")

    def test_raw_requires_complete_status_trailer(self):
        session = self.session()
        for command in ("missing-trailer", "bad-status", "disconnect"):
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                session.read(command, mode="raw")
        self.assertEqual(session.stats()["total"]["bytes"], 0)

    def test_remote_failure_reports_code_without_output_or_retry(self):
        session = self.session()
        for mode in ("stream", "raw"):
            with self.subTest(mode=mode), self.assertRaises(CommandError) as caught:
                session.retry_read(lambda: session.read("fail:17", mode=mode))
            self.assertEqual(caught.exception.returncode, 17)
            self.assertTrue(caught.exception.remote)
            self.assertNotIn("private", str(caught.exception))
        self.assertEqual(session.stats()["total"]["retries"], 0)
        self.assertEqual(session.stats()["total"]["calls"], 2)

    def test_bounds_and_length_failures_do_not_count_completed_bytes(self):
        session = self.session()
        for mode in ("stream", "raw"):
            with self.subTest(mode=mode), self.assertRaises(OutputLimitError):
                session.read("bytes:4", max_bytes=3, mode=mode)
            with self.assertRaises(UnexpectedLengthError):
                session.read("bytes:2", max_bytes=3, expected_bytes=3, mode=mode)
        self.assertEqual(session.stats()["total"]["bytes"], 0)
        self.assertEqual(session.stats()["total"]["completed"], 0)

    def test_client_connection_failure_is_distinct_from_command_failure(self):
        session = self.session()
        with self.assertRaises(AdbConnectionError):
            session.run("host-offline")
        with self.assertRaises(CommandError) as caught:
            session.run("host-denied")
        self.assertFalse(caught.exception.remote)
        self.assertEqual(session.stats()["total"]["connection_errors"], 1)

    def test_run_is_bounded_has_closed_stdin_and_no_automatic_retry(self):
        session = self.session()
        self.assertEqual(session.run("get-state"), b"device\n")
        self.assertEqual(session.run("stdin-read"), b"")
        with self.assertRaises(OutputLimitError):
            session.run("bytes:1024", max_bytes=10)
        with self.assertRaises(OutputLimitError):
            session.run("stderr-flood")
        self.assertEqual(session.stats()["total"]["client_starts"], 4)

    def test_cancel_before_start_spawns_nothing(self):
        session = self.session()
        event = threading.Event()
        event.set()
        with self.assertRaises(CancelledError):
            session.read("hang", cancel=event)
        self.assertEqual(session.stats()["total"]["client_starts"], 0)

    def test_timeouts_reap_children_in_all_modes(self):
        session = self.session()
        for mode in ("stream", "raw", "run"):
            with self.subTest(mode=mode), self.assertRaises(AdbTimeoutError):
                if mode == "run":
                    session.run("hang", timeout=0.2)
                else:
                    session.read("hang", mode=mode, timeout=0.2)
        self.assertEqual(session.stats()["total"]["completed"], 0)
        self.assertEqual(session.stats()["total"]["connection_errors"], 3)
        self.assertGreater(session._failures, 0)

    def test_background_cancellation_does_not_kill_foreground_channel(self):
        session = self.session()
        stop = threading.Event()
        errors = []

        def background():
            try:
                session.read("hang", priority=Priority.BACKGROUND, cancel=stop)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=background)
        thread.start()
        try:
            wait_for(lambda: session._streams[Priority.BACKGROUND].process is not None)
            self.assertEqual(session.read("counter"), b"1")
            process = session._streams[Priority.FOREGROUND].process
            stop.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CancelledError)
            self.assertIsNone(process.poll())
            self.assertEqual(session.read("counter"), b"2")
        finally:
            stop.set()
            session.close()
            thread.join(3)

    def test_close_cancels_active_read_and_rejects_future_work(self):
        session = self.session()
        errors = []

        def read():
            try:
                session.read("hang")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=read)
        thread.start()
        try:
            wait_for(lambda: session._streams[Priority.FOREGROUND].process is not None)
            session.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0], CancelledError)
            with self.assertRaises(SessionClosedError):
                session.read("counter")
            session.close()
        finally:
            session.close()
            thread.join(3)

    def test_metrics_are_detached_numeric_and_success_accounted_once(self):
        session = self.session()
        initial = session.stats()
        session.run("get-state")
        session.read("text:abc")
        session.read("text:defg", mode="raw")
        stats = session.stats()
        self.assertEqual((stats["total"]["completed"], stats["total"]["bytes"]), (3, 14))
        self.assertEqual(stats["total"]["client_starts"], 3)
        self.assertGreater(stats["total"]["received_bytes"], 14)
        self.assertAlmostEqual(stats["total"]["elapsed_seconds"],
                               sum(stats["total"][key] for key in (
                                   "queue_seconds", "channel_seconds", "io_seconds")))
        for snapshot in (initial, stats):
            self.assertEqual(set(snapshot), set(get_type_hints(SessionStats)))
            for bucket in (snapshot["total"], *snapshot["modes"].values()):
                fields = get_type_hints(OperationStats)
                self.assertEqual(set(bucket), set(fields))
                for key, kind in fields.items():
                    allowed = (int, float) if kind is float else (kind,)
                    self.assertIn(type(bucket[key]), allowed)
        stats["total"]["calls"] = 999
        self.assertEqual(session.stats()["total"]["calls"], 3)
        mode = next(iter(stats["modes"]))
        stats["modes"][mode]["calls"] = 999
        self.assertEqual(session.stats()["modes"][mode]["calls"], 1)

    def test_partial_raw_response_is_discarded_before_replay(self):
        session = self.session()
        launch = subprocess.Popen
        children = []

        def start(argv, **kwargs):
            if not children:
                argv = [*argv[:-1], argv[-1].replace("text:complete", "disconnect")]
            process = launch(argv, **kwargs)
            children.append(process)
            return process

        with patch("adblane._process.subprocess.Popen", side_effect=start):
            result = session.retry_read(lambda: session.read("text:complete", mode="raw"))
        self.assertEqual(result, b"complete")
        stats = session.stats()["total"]
        self.assertEqual((stats["calls"], stats["failed"], stats["completed"], stats["bytes"]), (2, 1, 1, 8))
        self.assertEqual(stats["retries"], 1)
        self.assertTrue(all(child.poll() is not None and child.stdout.closed for child in children))

    def test_broken_windows_pipe_close_preserves_original_transport_error(self):
        session = self.session()
        child = Mock()
        child.stdout = io.BytesIO(b"")
        child.stderr = None
        child.stdin.flush.side_effect = OSError(22, "synthetic pipe failure")
        child.stdin.close.side_effect = OSError(22, "synthetic close failure")
        child.poll.return_value = None
        with patch("adblane._stream.subprocess.Popen", return_value=child):
            with self.assertRaises(ProtocolError):
                session.read("text:ok")
        child.kill.assert_called_once()
        child.wait.assert_called_once()
        self.assertTrue(child.stdout.closed)
        self.assertFalse(session._streams[Priority.FOREGROUND].reader.is_alive())


if __name__ == "__main__":
    unittest.main()
