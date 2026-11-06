"""Inherited writers must not hold cancellation or shutdown hostage."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from adblane import AdbSession, AdbTimeoutError, CancelledError


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic child did not become ready")
        time.sleep(0.005)


class PipeCleanupTests(unittest.TestCase):
    def check_cleanup(self, mode, trigger):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            launcher = [sys.executable, "-I", "-B",
                        str(Path(__file__).with_name("holding_adb.py")), str(work)]
            session = AdbSession("synthetic", adb=launcher)
            cancel, errors, children = threading.Event(), [], []
            original = subprocess.Popen

            def launch(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child

            def action():
                try:
                    if trigger == "close":
                        session.close()
                    elif mode == "run":
                        session.run("hang", timeout=1 if trigger == "timeout" else 5,
                                    cancel=cancel)
                    else:
                        session.read("hang", mode=mode,
                                     timeout=1 if trigger == "timeout" else 5, cancel=cancel)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=action, daemon=True)
            with patch("subprocess.Popen", side_effect=launch):
                try:
                    if trigger == "close":
                        self.assertEqual(session.read("counter"), b"1")
                    worker.start()
                    wait_for(lambda: (work / "ready").exists())
                    if trigger == "cancel":
                        cancel.set()
                    worker.join(2)
                    self.assertFalse(worker.is_alive(), "cleanup is waiting on an inherited writer")
                    self.assertFalse((work / "exited").exists(), "test writer exited too early")
                    expected = {"timeout": AdbTimeoutError, "cancel": CancelledError}
                    if trigger in expected:
                        self.assertEqual(len(errors), 1)
                        self.assertIsInstance(errors[0], expected[trigger])
                    else:
                        self.assertEqual(errors, [])
                    self.assertTrue(children)
                    self.assertTrue(all(child.poll() is not None for child in children))
                    self.assertTrue(all(child.stdout.closed for child in children))
                    self.assertFalse(any(t.name.startswith("adblane-") for t in threading.enumerate()))
                finally:
                    cancel.set()
                    (work / "release").touch()
                    worker.join(5)
                    session.close()
                    wait_for(lambda: (work / "exited").exists())

    def test_timeout_with_inherited_output_pipes(self):
        for mode in ("run", "raw", "stream"):
            with self.subTest(mode=mode):
                self.check_cleanup(mode, "timeout")

    def test_cancellation_with_inherited_output_pipes(self):
        for mode in ("run", "raw", "stream"):
            with self.subTest(mode=mode):
                self.check_cleanup(mode, "cancel")

    def test_close_idle_stream_with_inherited_output_pipe(self):
        self.check_cleanup("stream", "close")
