"""Audit an allowlisted source tree and distribution contents without printing secrets."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile

ROOT_FILES = {"pyproject.toml", "MANIFEST.in", "LICENSE", "README.md", "README.zh-CN.md",
              "CHANGELOG.md", "requirements-dev.txt", ".gitignore"}
ROOT_DIRS = {"src", "tests", "tools", "docs", "examples", ".github"}
IGNORED_DIRS = {".git", ".local"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".apk", ".so", ".dll", ".exe",
                      ".png", ".jpg", ".wem", ".bnk", ".pyc", ".pyo", ".log"}
MACHINE_PATH = re.compile(r"[A-Za-z]:[\\/](?:Users|Coding)[\\/]|/(?:Users|home)/[^/\s]+/", re.I)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")


class AuditError(ValueError):
    pass


def check_text(name: str, data: bytes, deny=()):
    try:
        text = data.decode("utf8")
    except UnicodeDecodeError as error:
        raise AuditError(f"{name}: unexpected non-text content") from error
    if MACHINE_PATH.search(text):
        raise AuditError(f"{name}: machine-specific path")
    if PRIVATE_KEY.search(text):
        raise AuditError(f"{name}: private key marker")
    if any(term.casefold() in text.casefold() for term in deny):
        raise AuditError(f"{name}: forbidden identifier")
    return text


def check_name(name: str):
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or any(part in IGNORED_DIRS or part == "__pycache__" for part in path.parts)):
        raise AuditError("Unsafe or private archive path")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise AuditError(f"{name}: excluded asset/cache format")


def audit_source(root: Path, deny=()):
    root = root.resolve(strict=True)
    count = 0
    for entry in root.iterdir():
        if entry.name in IGNORED_DIRS:
            continue
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            raise AuditError("Source tree contains a link")
        if entry.is_dir():
            if entry.name not in ROOT_DIRS:
                raise AuditError(f"{entry.name}: unexpected source directory")
            paths = entry.rglob("*")
        else:
            if entry.name not in ROOT_FILES:
                raise AuditError(f"{entry.name}: unexpected source file")
            paths = [entry]
        for path in paths:
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise AuditError("Source tree contains a link")
            if path.is_dir():
                continue
            name = path.relative_to(root).as_posix()
            check_name(name)
            text = check_text(name, path.read_bytes(), deny)
            if name.startswith("src/") and path.suffix == ".py":
                for node in ast.walk(ast.parse(text, filename=name)):
                    if isinstance(node, ast.Import):
                        modules = [alias.name.split(".")[0] for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and not node.level:
                        modules = [(node.module or "").split(".")[0]]
                    else:
                        continue
                    if any(module not in sys.stdlib_module_names and module != "adblane" for module in modules):
                        raise AuditError(f"{name}: external runtime import")
            count += 1
    return count


def audit_archive(path: Path, deny=()):
    count = 0
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                check_name(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise AuditError("Wheel contains a link")
                if not info.is_dir():
                    check_text(info.filename, archive.read(info), deny)
                    count += 1
    else:
        with tarfile.open(path, "r:gz") as archive:
            for info in archive:
                check_name(info.name)
                if info.isdir():
                    continue
                if not info.isfile():
                    raise AuditError("Source distribution contains a non-regular entry")
                with archive.extractfile(info) as member:
                    check_text(info.name, member.read(), deny)
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--deny", action="append", default=[], help="Additional private term; never stored")
    args = parser.parse_args()
    print(f"Source audit: {audit_source(args.root, args.deny)} files, stdlib-only runtime")
    for archive in args.archive:
        print(f"Distribution audit: {archive.name}: {audit_archive(archive, args.deny)} text members")


if __name__ == "__main__":
    main()
