"""Deterministic scheduling, retry contracts, and argument boundaries."""
import math
import threading
import time
import unittest
from unittest.mock import Mock, patch

from adblane import (
    AdbConnectionError, AdbSession, AdbTimeoutError, CancelledError,
    CommandError, OutputLimitError, Priority, RetryPolicy, SessionClosedError,
)


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError("test condition timed out")
        time.sleep(0.005)


class SessionTests(unittest.TestCase):
    def session(self, **kwargs):
        session = AdbSession("synthetic", adb="never-invoked", **kwargs)
        self.addCleanup(session.close)
        return session

    def test_background_queue_reserves_foreground_capacity(self):
        session = self.session()
        entered, release = threading.Event(), threading.Event()
        errors, results = [], []

        def operation(token, end, values, lane):
            entered.set()
            while not release.wait(0.01):
                token.check(end)
            return b"bulk"

        def background():
            try:
                results.append(session._call("test", operation, timeout=2,
                                             priority=Priority.BACKGROUND, cancel=None))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=background) for _ in range(2)]
        threads[0].start()
        try:
            self.assertTrue(entered.wait(1))
            threads[1].start()
            wait_for(lambda: len(session._pending) == 1)
            with patch("adblane.session.collect", return_value=b"interactive"):
                self.assertEqual(session.run("get-state", timeout=0.5), b"interactive")
            self.assertEqual(session._active, 1)
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(results, [b"bulk", b"bulk"])
        self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_fifo_within_priority_and_foreground_overtakes_waiting_background(self):
        session = self.session(max_concurrent=1)
        gate, started = threading.Event(), threading.Event()
        order, errors = [], []

        def run(label, lane, hold=False):
            def operation(token, end, values, priority):
                order.append(label)
                if hold:
                    started.set()
                    while not gate.wait(0.01):
                        token.check(end)
                return b"ok"
            try:
                session._call("test", operation, timeout=3, priority=lane, cancel=None)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run, args=("held", Priority.BACKGROUND, True))]
        threads[0].start()
        try:
            self.assertTrue(started.wait(1))
            for count, (label, lane) in enumerate((
                ("background-1", Priority.BACKGROUND),
                ("background-2", Priority.BACKGROUND),
                ("foreground-1", Priority.FOREGROUND),
                ("foreground-2", Priority.FOREGROUND),
            ), 1):
                thread = threading.Thread(target=run, args=(label, lane))
                threads.append(thread)
                thread.start()
                wait_for(lambda: len(session._pending) == count)
        finally:
            gate.set()
            for thread in threads:
                thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(order, ["held", "foreground-1", "foreground-2", "background-1", "background-2"])
        self.assertEqual((session._active, session._background, session._pending), (0, 0, []))

    def test_queue_timeout_starts_no_process_and_uses_original_deadline(self):
        session = self.session()
        with session._condition:
            session._retry_at = time.monotonic() + 1
        with patch("adblane.session.collect") as process:
            with self.assertRaises(AdbTimeoutError):
                session.run("get-state", timeout=0.03)
            process.assert_not_called()
        self.assertEqual(session._pending, [])
        self.assertEqual(session._failures, 0)
        self.assertEqual(session.stats()["total"]["client_starts"], 0)
        self.assertEqual(session.stats()["total"]["connection_errors"], 0)

    def test_channel_wait_timeout_does_not_degrade_healthy_session(self):
        session = self.session(retry_policy=RetryPolicy(3, 1, 1))
        stream = session._streams[Priority.FOREGROUND]
        with stream.lock:
            with self.assertRaises(AdbTimeoutError):
                session.read("unused", timeout=0.03)
            self.assertEqual((session._failures, session._retry_at), (0, 0))
            self.assertEqual(session.stats()["total"]["connection_errors"], 0)
            with patch("adblane.session.collect", return_value=b"ready") as process:
                self.assertEqual(session.run("get-state", priority=Priority.BACKGROUND,
                                             timeout=0.1), b"ready")
                process.assert_called_once()
        self.assertIsNone(stream.process)

    def test_cancelling_queued_work_removes_its_ticket(self):
        session = self.session()
        session._retry_at = time.monotonic() + 10
        stop, errors = threading.Event(), []

        def run():
            try:
                session.run("get-state", cancel=stop)
            except Exception as error:
                errors.append(error)

        with patch("adblane.session.collect") as process:
            thread = threading.Thread(target=run)
            thread.start()
            try:
                wait_for(lambda: len(session._pending) == 1)
                stop.set()
                thread.join(2)
                process.assert_not_called()
            finally:
                stop.set()
                session.close()
                thread.join(2)
        self.assertIsInstance(errors[0], CancelledError)
        self.assertEqual(session._pending, [])

    def test_retry_discards_entire_failed_result_and_is_finite(self):
        session = self.session(retry_policy=RetryPolicy(3, 0, 0))
        action = Mock(side_effect=[AdbConnectionError("broken"), b"complete"])
        callback = Mock()
        self.assertEqual(session.retry_read(action, on_retry=callback), b"complete")
        self.assertEqual(action.call_count, 2)
        self.assertEqual(callback.call_args.args[1:], (2, 0))
        action = Mock(side_effect=AdbConnectionError("still broken"))
        with self.assertRaises(AdbConnectionError):
            session.retry_read(action)
        self.assertEqual(action.call_count, 3)
        self.assertEqual(session.stats()["total"]["retries"], 3)

    def test_nontransport_failures_and_writes_are_never_replayed(self):
        session = self.session(retry_policy=RetryPolicy(3, 0, 0))
        for error in (CommandError(1), OutputLimitError("too large"), ValueError("invalid"), CancelledError("stop")):
            action = Mock(side_effect=error)
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                session.retry_read(action)
            action.assert_called_once()
        with patch("adblane.session.collect", side_effect=AdbConnectionError("offline")) as command:
            with self.assertRaises(AdbConnectionError):
                session.run("shell", "true")
            command.assert_called_once()

    def test_cancel_during_backoff_prevents_the_next_attempt(self):
        session = self.session()
        cancel = threading.Event()
        action = Mock(side_effect=AdbConnectionError("offline"))
        with self.assertRaises(CancelledError):
            session.retry_read(action, cancel=cancel,
                               on_retry=lambda *args: cancel.set())
        action.assert_called_once()
        self.assertEqual(session.stats()["total"]["retries"], 0)

    def test_shared_cooldown_does_not_multiply_simultaneous_failures(self):
        session = self.session(retry_policy=RetryPolicy(3, 1, 4))
        now = [100.0]
        with patch("adblane.session.time.monotonic", side_effect=lambda: now[0]):
            for delay in (1, 2, 4, 4):
                session._failed()
                self.assertEqual(session._retry_at - now[0], delay)
                failures = session._failures
                session._failed()
                self.assertEqual(session._failures, failures)
                now[0] = session._retry_at
            session._recovered()
            self.assertEqual((session._failures, session._retry_at), (0, 0))

    def test_independent_sessions_do_not_share_close_or_state(self):
        first, second = self.session(), self.session()
        first.close()
        with self.assertRaises(SessionClosedError):
            first.retry_read(lambda: b"unused")
        with patch("adblane.session.collect", return_value=b"ready"):
            self.assertEqual(second.run("get-state"), b"ready")

    def test_auto_selects_raw_for_large_bounds_or_long_commands(self):
        session = self.session()
        with patch("adblane.session.raw_read", return_value=b"raw") as raw:
            self.assertEqual(session.read("x" * 3501, max_bytes=3), b"raw")
            self.assertEqual(session.read("id", max_bytes=4 * 1024 * 1024), b"raw")
            self.assertEqual(raw.call_count, 2)

    def test_invalid_inputs_never_start_a_child(self):
        session = self.session()
        operations = [
            lambda value=value: session.read("id", max_bytes=value)
            for value in (-1, True, 1.5, 64 * 1024 * 1024 + 1)
        ] + [lambda value=value: session.read("id", timeout=value)
             for value in (0, -1, True, math.inf, math.nan)] + [
            lambda: session.read(""), lambda: session.read("a\0b"),
            lambda: session.read("x" * 65537), lambda: session.read("id", mode="other"),
            lambda: session.read("x" * 3501, mode="stream"),
            lambda: session.read("id", expected_bytes=True),
            lambda: session.read("id", max_bytes=1, expected_bytes=2),
            lambda: session.read("id", priority=True), lambda: session.read("id", priority=99),
            lambda: session.read("id", priority=0.0),
            lambda: session.run(), lambda: session.run("a\0b"),
        ]
        with patch("subprocess.Popen") as process:
            for operation in operations:
                with self.assertRaises(ValueError):
                    operation()
            process.assert_not_called()
        with self.assertRaises(TypeError):
            session.retry_read(lambda: b"ok", on_retry=123)

    def test_invalid_constructor_and_retry_policy(self):
        for kwargs in ({"max_concurrent": 0}, {"max_background": 3}, {"max_background": False},
                       {"adb": []}, {"adb": [""]}, {"adb": "a\0b"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AdbSession("synthetic", **kwargs)
        for serial in ("", "a\0b", None):
            with self.assertRaises(ValueError):
                AdbSession(serial)
        for kwargs in ({"max_attempts": 0}, {"max_attempts": True},
                       {"initial_delay": -1}, {"max_delay": math.inf},
                       {"initial_delay": 2, "max_delay": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RetryPolicy(**kwargs)


if __name__ == "__main__":
    unittest.main()
