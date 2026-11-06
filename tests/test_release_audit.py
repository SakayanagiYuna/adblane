"""Synthetic release-audit failures, independent of any private denylist."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "release_audit", Path(__file__).resolve().parents[1] / "tools/audit_release.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class AuditTests(unittest.TestCase):
    def test_machine_paths_private_keys_and_caller_terms_are_rejected(self):
        samples = [
            ("Z:" + chr(92) + "Users" + chr(92) + "synthetic" + chr(92) + "file", ()),
            ("-" * 5 + "BEGIN RSA" + " PRIVATE KEY" + "-" * 5, ()),
            ("synthetic forbidden text", ("forbidden",)),
        ]
        for text, deny in samples:
            with self.subTest(text=text), self.assertRaises(AUDIT.AuditError):
                AUDIT.check_text("fixture.txt", text.encode(), deny)

    def test_assets_escapes_and_private_directories_are_rejected(self):
        for name in ("../escape.py", "/absolute.py", "folder/../escape.py", ".local/env/file",
                     "asset.sqlite3", "asset.png", "cache.pyc", "dir\\file.py"):
            with self.subTest(name=name), self.assertRaises(AUDIT.AuditError):
                AUDIT.check_name(name)

    def test_external_runtime_imports_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src/adblane"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("import external_application\n", encoding="utf8")
            with self.assertRaises(AUDIT.AuditError):
                AUDIT.audit_source(root)
            (source / "__init__.py").write_text("import threading\nfrom . import session\n", encoding="utf8")
            self.assertEqual(AUDIT.audit_source(root), 1)

    def test_wheel_members_are_scanned_without_extracting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "synthetic.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("adblane/__init__.py", "# synthetic forbidden identifier\n")
            with self.assertRaises(AUDIT.AuditError):
                AUDIT.audit_archive(wheel, ("forbidden",))


if __name__ == "__main__":
    unittest.main()
