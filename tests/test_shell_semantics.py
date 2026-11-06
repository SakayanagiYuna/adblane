"""Execute the generated programs in a local shell; never invokes ADB."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from adblane import AdbSession, CommandError


class ShellSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell = os.environ.get("ADBLANE_TEST_SHELL")
        if not shell and os.name == "nt":
            git = shutil.which("git")
            candidate = Path(git).resolve().parent.parent / "usr/bin/bash.exe" if git else None
            shell = str(candidate) if candidate and candidate.is_file() else None
        elif not shell:
            shell = shutil.which("bash")
        if not shell:
            raise unittest.SkipTest("requires a local bash with base64 -w 0")
        cls.shell = shell
        cls.env = dict(os.environ)
        if os.name == "nt":
            cls.env["PATH"] = str(Path(shell).parent) + os.pathsep + os.environ.get("PATH", "")
        cls.env.pop("BASH_ENV", None)
        probe = subprocess.run([shell, "-c", "printf x | base64 -w 0"], env=cls.env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if probe.returncode or probe.stdout != b"eA==":
            raise unittest.SkipTest("requires base64 -w 0")

    def setUp(self):
        original = subprocess.Popen
        self.prelude = ""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        startup = Path(temporary.name) / "startup.sh"

        def launch(argv, **kwargs):
            startup.write_text(self.prelude + "\n", encoding="utf8")
            kwargs["env"] = dict(self.env, BASH_ENV=startup.as_posix())
            if argv[-3:] == ["shell", "-T", "sh"]:
                return original([self.shell], **kwargs)
            self.assertEqual(argv[-2], "exec-out")
            parts = shlex.split(argv[-1])
            self.assertEqual(parts[:2], ["sh", "-c"])
            return original([self.shell, "-c", parts[2]], **kwargs)

        self.launch = patch("subprocess.Popen", side_effect=launch)
        self.launch.start()
        self.addCleanup(self.launch.stop)
        self.session = AdbSession("synthetic", adb="mapped-to-local-shell")
        self.addCleanup(self.session.close)

    def test_pipeline_status_is_independent_of_transport_and_auto_threshold(self):
        for mode, limit in (("stream", 1024), ("raw", 1024),
                            ("auto", 1024), ("auto", 4 * 1024 * 1024)):
            with self.subTest(mode=mode, limit=limit):
                self.assertEqual(self.session.read("set -o pipefail; printf ready", mode=mode, max_bytes=limit), b"ready")
                self.assertEqual(self.session.read("false | printf ready", mode=mode, max_bytes=limit), b"ready")

    def test_explicit_command_pipefail_and_last_command_failure_are_preserved(self):
        for mode in ("stream", "raw"):
            for command in ("set -o pipefail; false | printf ready", "printf ready | false"):
                with self.subTest(mode=mode, command=command):
                    with self.assertRaises(CommandError) as error:
                        self.session.read(command, mode=mode)
                    self.assertEqual(error.exception.returncode, 1)
            self.assertEqual(self.session.read("printf ready", mode=mode), b"ready")

    def test_encoder_failure_is_still_reported(self):
        self.prelude = 'base64() { command base64 "$@"; return 23; }'
        with self.assertRaises(CommandError) as error:
            self.session.read("printf ready", mode="stream")
        self.assertEqual(error.exception.returncode, 23)
