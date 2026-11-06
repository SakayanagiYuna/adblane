"""Synthetic launcher whose descendant keeps the output pipes open."""
from pathlib import Path
import subprocess
import sys
import time


def main():
    work = Path(sys.argv[1])
    if sys.argv[2:] == ["--holder"]:
        (work / "ready").touch()
        deadline = time.monotonic() + 15
        while not (work / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        (work / "exited").touch()
        return
    subprocess.Popen([sys.executable, "-I", "-B", __file__, str(work), "--holder"])
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    # Isolated Python omits this directory from sys.path.
    sys.path.insert(0, str(Path(__file__).parent))
    from fake_adb import main as fake_main
    fake_main()


if __name__ == "__main__":
    main()
