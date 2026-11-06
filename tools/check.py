"""Build, audit, install and test independently; clean temporary artifacts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from audit_release import ROOT_DIRS, ROOT_FILES, audit_archive, audit_source

ROOT = Path(__file__).resolve().parents[1]


def ensure_plain(path, boundary):
    path, boundary = path.absolute(), boundary.resolve(strict=True)
    if not path.is_relative_to(boundary) or path == boundary:
        raise ValueError("Path leaves the intended boundary")
    for part in (path, *path.parents):
        if part == boundary:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Refusing a link/junction in the work path")


def run(args, *, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, encoding="utf8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        raise RuntimeError(f"Verification command failed with exit {result.returncode}")
    return result.stdout


CONSUMER = '''import pathlib, sys, unittest, adblane
module = pathlib.Path(adblane.__file__).resolve()
environment = pathlib.Path(sys.prefix).resolve()
assert module.is_relative_to(environment), "package not installed in clean environment"
source = pathlib.Path(sys.argv[2]).resolve()
paths = (pathlib.Path(p).resolve() for p in sys.path if p)
assert not any(p.is_relative_to(source) and not p.is_relative_to(environment) for p in paths), "source checkout on import path"
print("Installed consumer: Python", sys.version.split()[0], "adblane", adblane.__version__, flush=True)
suite = unittest.defaultTestLoader.discover(sys.argv[1])
result = unittest.TextTestRunner(verbosity=1).run(suite)
skips_failed = "--fail-on-skip" in sys.argv[3:] and bool(result.skipped)
if skips_failed:
    print("Skipped tests are not allowed in this run.", file=sys.stderr)
sys.exit(not result.wasSuccessful() or skips_failed)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter used to create the consumer environment")
    parser.add_argument("--environment", type=Path, help="Reusable isolated environment (outside temporary experiments)")
    parser.add_argument("--deny", action="append", default=[], help="Additional private term for source/distribution audit")
    parser.add_argument("--fail-on-skip", action="store_true", help="Fail if any test is skipped (used by CI)")
    args = parser.parse_args()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    for key in ("PYTHONPATH", "PYTHONHOME", "MYPYPATH"):
        env.pop(key, None)
    version_probe = "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor))"
    expected_version = run([args.python, "-I", "-B", "-c", version_probe], cwd=ROOT, env=env).strip()
    environment = (args.environment or ROOT / f".local/runtime/consumer-{expected_version}").resolve()
    experiments = ROOT / ".local/experiments/independent-check"
    ensure_plain(experiments, ROOT)
    if environment.is_relative_to(ROOT / ".local/experiments"):
        raise ValueError("Reusable dependencies must be outside the experiment tree")
    print(f"Source audit: {audit_source(ROOT, args.deny)} files; stdlib-only runtime", flush=True)
    experiments.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=experiments))
    ensure_plain(work, experiments)
    try:
        source, dist, consumer = (work / name for name in ("source", "dist", "consumer"))
        source.mkdir()
        consumer.mkdir()
        for name in ROOT_FILES:
            if (ROOT / name).is_file():
                shutil.copyfile(ROOT / name, source / name)
        for name in ROOT_DIRS:
            if (ROOT / name).is_dir():
                shutil.copytree(ROOT / name, source / name)
        temporary = work / "temp"
        temporary.mkdir()
        env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
        run([sys.executable, "-B", "-m", "build", "--no-isolation", "--outdir", str(dist), str(source)],
            cwd=work, env=env)
        wheels, sources = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sources) != 1:
            raise RuntimeError("Expected exactly one wheel and one source distribution")
        for artifact in (*wheels, *sources):
            print(f"Distribution audit: {artifact.name}: {audit_archive(artifact, args.deny)} members", flush=True)
        run([sys.executable, "-B", "-m", "twine", "check", "--strict",
             str(wheels[0]), str(sources[0])], cwd=work, env=env)
        print("Distribution metadata and README: passed", flush=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            run([args.python, "-B", "-m", "venv", str(environment)], cwd=consumer, env=env)
        actual_version = run([str(python), "-I", "-B", "-c", version_probe], cwd=consumer, env=env).strip()
        if actual_version != expected_version:
            raise RuntimeError("Existing consumer environment has the wrong Python version; choose a new --environment")
        run([str(python), "-B", "-m", "pip", "install", "--no-deps", "--no-compile",
             "--no-index", "--force-reinstall", str(wheels[0])], cwd=consumer, env=env)
        output = run([str(python), "-I", "-B", "-X", "utf8", "-c", CONSUMER,
                      str(source / "tests"), str(ROOT),
                      *(["--fail-on-skip"] if args.fail_on_skip else [])], cwd=consumer, env=env)
        print(output.strip())
        output = run([sys.executable, "-B", "-m", "mypy", "--strict", "--disallow-any-expr",
                      "--python-version", "3.10", "--python-executable", str(python),
                      "--cache-dir", str(work / "mypy-cache"), str(source / "tests/typing")],
                     cwd=consumer, env=env)
        print("Installed consumer typing: " + output.strip(), flush=True)
    finally:
        ensure_plain(work, experiments)
        for path in work.rglob("*"):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise RuntimeError("Cleanup refused: linked work entry remains")
        shutil.rmtree(work)
        print("Temporary build and test artifacts removed.", flush=True)


if __name__ == "__main__":
    main()
