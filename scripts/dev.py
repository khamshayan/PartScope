"""Run the ml-service, the API and the web dev server together.

Services that do not exist yet are skipped with a note rather than crashing --
they arrive in Phases 3 and 4, and `make dev` should stay useful before then.
Ctrl-C stops everything.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"
VENV_PYTHON = REPO_ROOT / ".venv" / ("Scripts" if IS_WINDOWS else "bin") / \
    ("python.exe" if IS_WINDOWS else "python")


def _python() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


class Service:
    def __init__(self, name: str, command: list[str], cwd: Path, exists: Path):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.exists = exists
        self.process: subprocess.Popen | None = None

    def available(self) -> bool:
        return self.exists.exists()

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=IS_WINDOWS,  # npm is a .cmd shim on Windows
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            print(f"[{self.name}] {line.rstrip()}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


def build_services() -> list[Service]:
    return [
        Service(
            "ml",
            [_python(), "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
            REPO_ROOT / "ml-service",
            REPO_ROOT / "ml-service" / "app" / "main.py",
        ),
        Service(
            "api",
            ["npm", "run", "dev"],
            REPO_ROOT / "api",
            REPO_ROOT / "api" / "package.json",
        ),
        Service(
            "web",
            ["npm", "run", "dev"],
            REPO_ROOT / "web",
            REPO_ROOT / "web" / "package.json",
        ),
    ]


def main() -> int:
    services = build_services()
    running: list[Service] = []

    for service in services:
        if service.available():
            print(f"starting {service.name}")
            service.start()
            running.append(service)
        else:
            print(f"skipping {service.name} -- not built yet ({service.exists.name} missing)")

    if not running:
        print("\nnothing to run yet.")
        return 0

    print("\nctrl-c to stop\n")
    stop = threading.Event()

    def handle_signal(*_args):
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    try:
        stop.wait()
    finally:
        print("\nstopping...")
        for service in running:
            service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
