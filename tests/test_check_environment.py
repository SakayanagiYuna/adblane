"""The installed-package check accepts nested venvs but rejects source imports."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


tree = ast.parse((Path(__file__).resolve().parents[1] / "tools/check.py").read_text(encoding="utf8"))
CONSUMER = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "CONSUMER"
                        for target in node.targets))


class CheckEnvironmentTests(unittest.TestCase):
    def check_environment(self, source, injected=None):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            tests = work / "tests"
            tests.mkdir()
            program = CONSUMER
            if injected is not None:
                program = f"import sys; sys.path.insert(0, {str(injected)!r})\n" + program
            return subprocess.run(
                [sys.executable, "-I", "-B", "-c", program, str(tests), str(source)],
                cwd=work, capture_output=True, text=True, timeout=10,
            )

    def test_environment_inside_checkout_is_allowed(self):
        result = self.check_environment(Path(sys.prefix).resolve().parent)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_environment_outside_checkout_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.check_environment(Path(directory) / "checkout")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_checkout_and_source_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "checkout"
            for injected in (source, source / "src"):
                with self.subTest(path=injected.name):
                    result = self.check_environment(source, injected)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("source checkout on import path", result.stderr)
