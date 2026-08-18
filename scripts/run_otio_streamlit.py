#!/usr/bin/env python3
"""Hauptthread-Supervisor für Streamlit.

Startet ``streamlit run app.py`` als Kindprozess und beendet die gesamte
Prozessgruppe bei Ctrl+C, SIGTERM oder SIGHUP (Terminal geschlossen).
Der Launcher startet dieses Skript abgekoppelt, damit das Schließen des
Launcher-Fensters die App nicht mitnimmt — Stoppen geht über den Launcher.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8501

_child: subprocess.Popen[bytes] | None = None
_shutting_down = False


def _shutdown(signum: int, _frame: object) -> None:
    global _shutting_down
    if _shutting_down:
        _kill_child(signal.SIGKILL)
        os._exit(0)
    _shutting_down = True
    _kill_child(signal.SIGTERM)
    deadline = time.time() + 3
    while _child is not None and _child.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if _child is not None and _child.poll() is None:
        _kill_child(signal.SIGKILL)
    os._exit(0)


def _kill_child(sig: int) -> None:
    if _child is None or _child.poll() is not None:
        return
    try:
        _child.send_signal(sig)
    except (ProcessLookupError, OSError):
        return


def main(argv: list[str] | None = None) -> int:
    global _child
    parser = argparse.ArgumentParser(description="OTIO Streamlit-Supervisor")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    python = sys.executable
    command = [
        python,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.port",
        str(args.port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Gleiche Prozessgruppe wie der Supervisor, damit killpg(Supervisor)
    # Streamlit mitnimmt. start_new_session nur im Launcher um den Supervisor.
    _child = subprocess.Popen(command, cwd=str(ROOT), close_fds=True)
    return_code = _child.wait()
    return int(return_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
