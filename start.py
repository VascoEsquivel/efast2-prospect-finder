"""Set up and launch the draft assistant in one step:  python start.py

First run: creates .venv next to this file and installs requirements.txt into it.
Later runs: reinstalls only if requirements.txt changed, then starts the app.
Any extra arguments go to `streamlit run` (e.g. python start.py --server.port 8502).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".requirements.sha256"


def main() -> int:
    if sys.version_info < (3, 11):
        print(f"Python 3.11 or newer is required (this is {sys.version.split()[0]}).")
        print("Install it from https://www.python.org/downloads/ and run this again.")
        return 1

    if not PY.exists():
        print("Creating a virtual environment in .venv ...")
        try:
            venv.create(VENV, with_pip=True)
        except Exception as exc:  # noqa: BLE001 - e.g. Debian/Ubuntu without python3-venv
            print(f"Could not create the virtual environment: {exc}")
            print("On Debian/Ubuntu run: sudo apt install python3-venv   then try again.")
            return 1

    digest = hashlib.sha256((HERE / "requirements.txt").read_bytes()).hexdigest()
    if not STAMP.exists() or STAMP.read_text().strip() != digest:
        print("Installing requirements (the first run takes a minute or two) ...")
        subprocess.check_call([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"])
        subprocess.check_call([str(PY), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(HERE / "requirements.txt")])
        STAMP.write_text(digest)

    print("Starting the draft assistant. Close this window (or press Ctrl+C) to stop it.")
    cmd = [str(PY), "-m", "streamlit", "run", str(HERE / "app.py"),
           "--server.showEmailPrompt", "false", "--browser.gatherUsageStats", "false", *sys.argv[1:]]
    try:
        return subprocess.call(cmd, cwd=HERE)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
