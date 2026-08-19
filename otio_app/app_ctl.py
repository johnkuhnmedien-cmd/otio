"""Lokaler App-Start/Stop — räumt Port 8501 und Altprozesse, optional git pull.

Wird vom Finder-Launcher und von ``python -m otio_app.app_ctl`` genutzt.
Kein Streamlit-Import, damit Stop/Start unabhängig von hängenden LLM-Calls bleibt.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 8501
HEALTH_PATH = "/_stcore/health"
PID_FILE_NAME = "otio-streamlit.pid"
LOG_FILE_NAME = "otio-streamlit.log"
START_WAIT_SECONDS = 30
STOP_WAIT_SECONDS = 4

_BRANCH_OK = re.compile(r"^[\w./+-]+$")


class AppCtlError(RuntimeError):
    """Benutzerfehler beim Starten, Stoppen oder bei Git."""


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str
    cwd: str | None = None


@dataclass
class AppStatus:
    port: int
    healthy: bool
    pids: list[int] = field(default_factory=list)
    processes: list[ProcessInfo] = field(default_factory=list)
    pid_file: int | None = None
    branch: str | None = None
    revision: str | None = None
    log_path: Path | None = None


@dataclass
class CommandResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)
    payload: object | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def logs_dir(root: Path | None = None) -> Path:
    path = (root or repo_root()) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_file_path(root: Path | None = None) -> Path:
    return logs_dir(root) / PID_FILE_NAME


def log_file_path(root: Path | None = None) -> Path:
    return logs_dir(root) / LOG_FILE_NAME


def venv_python(root: Path | None = None) -> Path:
    base = root or repo_root()
    for candidate in (
        base / ".venv" / "bin" / "python",
        base / ".venv" / "bin" / "python3",
        base / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def supervisor_script(root: Path | None = None) -> Path:
    return (root or repo_root()) / "scripts" / "run_otio_streamlit.py"


def app_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def health_url(port: int = DEFAULT_PORT) -> str:
    return f"{app_url(port)}{HEALTH_PATH}"


def is_valid_branch_name(name: str) -> bool:
    value = (name or "").strip()
    if not value or value in {".", ".."} or value.startswith("-"):
        return False
    if value.endswith(".") or value.endswith("/"):
        return False
    if ".." in value or "\\" in value:
        return False
    return bool(_BRANCH_OK.fullmatch(value))


def is_otio_streamlit_command(command: str, root: Path) -> bool:
    """Erkennt Streamlit/Supervisor dieser Repo-Kopie, nicht den Launcher selbst."""
    return is_otio_streamlit_process(command, root, cwd=None)


def is_otio_streamlit_process(
    command: str,
    root: Path,
    cwd: str | None = None,
) -> bool:
    text = " ".join((command or "").split()).lower()
    if not text:
        return False
    if "otio_launcher.py" in text or "otio_app.app_ctl" in text:
        return False
    if "pytest" in text:
        return False
    repo = str(root).lower()
    mentions_repo = repo in text
    streamlit_app = "streamlit" in text and "app.py" in text
    supervisor = "run_otio_streamlit.py" in text
    if mentions_repo and (streamlit_app or supervisor):
        return True
    if supervisor:
        return True
    if streamlit_app and cwd and _cwd_in_repo(cwd, root):
        return True
    return False


def _cwd_in_repo(cwd: str, root: Path) -> bool:
    try:
        Path(cwd).resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def is_killable_listener_command(command: str) -> bool:
    text = (command or "").lower()
    return any(
        token in text
        for token in ("python", "streamlit", "run_otio_streamlit")
    )


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 60,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_command(pid: int) -> str:
    proc_cmd = Path(f"/proc/{pid}/cmdline")
    if proc_cmd.is_file():
        raw = proc_cmd.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        return raw.strip()
    try:
        result = _run(
            ["ps", "-p", str(pid), "-o", "command="],
            cwd=repo_root(),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def _read_cwd(pid: int) -> str | None:
    proc_cwd = Path(f"/proc/{pid}/cwd")
    try:
        if proc_cwd.exists():
            return str(proc_cwd.resolve())
    except OSError:
        pass
    try:
        result = _run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            cwd=repo_root(),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("n"):
            return line[1:] or None
    return None


def _process_info(pid: int) -> ProcessInfo:
    return ProcessInfo(pid=pid, command=_read_command(pid), cwd=_read_cwd(pid))


def pids_listening_on_port(port: int) -> list[int]:
    pids: set[int] = set()
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    if pids:
        return sorted(pids)
    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    needle = f":{port} "
    for line in (result.stdout or "").splitlines():
        if needle not in line and not line.rstrip().endswith(f":{port}"):
            continue
        for match in re.finditer(r"pid=(\d+)", line):
            pids.add(int(match.group(1)))
    return sorted(pids)


def _read_pid_file(root: Path) -> int | None:
    path = pid_file_path(root)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    pid = int(raw)
    if not _pid_is_alive(pid):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return pid


def _write_pid_file(root: Path, pid: int) -> None:
    pid_file_path(root).write_text(f"{pid}\n", encoding="utf-8")


def _clear_pid_file(root: Path) -> None:
    try:
        pid_file_path(root).unlink(missing_ok=True)
    except OSError:
        pass


def collect_app_pids(root: Path | None = None, port: int = DEFAULT_PORT) -> list[ProcessInfo]:
    base = root or repo_root()
    found: dict[int, ProcessInfo] = {}
    mine = {os.getpid(), os.getppid()}

    stored = _read_pid_file(base)
    if stored is not None and stored not in mine:
        found[stored] = _process_info(stored)

    for pid in pids_listening_on_port(port):
        if pid in mine:
            continue
        found[pid] = _process_info(pid)

    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None:
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(None, 1)
            if not parts or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid in mine or pid in found:
                continue
            command = parts[1] if len(parts) > 1 else ""
            cwd = None
            if "streamlit" in command.lower() or "run_otio_streamlit" in command.lower():
                cwd = _read_cwd(pid)
            if is_otio_streamlit_process(command, base, cwd=cwd):
                found[pid] = ProcessInfo(pid=pid, command=command, cwd=cwd)

    return [found[pid] for pid in sorted(found)]


def http_is_healthy(port: int = DEFAULT_PORT, timeout: float = 1.5) -> bool:
    request = urllib.request.Request(
        health_url(port),
        method="GET",
        headers={"Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace").strip().lower()
            return 200 <= response.status < 300 and (not body or "ok" in body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def git_current_branch(root: Path | None = None) -> str | None:
    result = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root or repo_root(),
        timeout=8,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def git_revision(root: Path | None = None) -> str | None:
    result = _run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root or repo_root(),
        timeout=8,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_dirty_tracked_files(root: Path | None = None) -> list[str]:
    result = _run(
        ["git", "status", "--porcelain"],
        cwd=root or repo_root(),
        timeout=15,
    )
    if result.returncode != 0:
        raise AppCtlError(
            "Git-Status konnte nicht gelesen werden:\n"
            + (result.stderr or result.stdout or "").strip()
        )
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        if code == "??" or code.strip() == "":
            continue
        dirty.append(line[3:].strip())
    return dirty


def normalize_branch_name(name: str) -> str:
    value = (name or "").strip()
    if value.startswith("remotes/"):
        value = value[len("remotes/") :]
    if value.startswith("origin/"):
        value = value[len("origin/") :]
    return value


def list_git_branches(root: Path | None = None, *, fetch: bool = False) -> list[str]:
    base = root or repo_root()
    if fetch:
        fetch_origin(base)
    result = _run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"],
        cwd=base,
        timeout=15,
    )
    if result.returncode != 0:
        raise AppCtlError(
            "Branches konnten nicht gelesen werden:\n"
            + (result.stderr or result.stdout or "").strip()
        )
    names: list[str] = []
    seen: set[str] = set()
    current = git_current_branch(base)
    for raw in result.stdout.splitlines():
        item = normalize_branch_name(raw.strip())
        if not item or item == "HEAD" or item == "origin":
            continue
        if item in seen or not is_valid_branch_name(item):
            continue
        seen.add(item)
        names.append(item)
    names.sort(key=lambda value: (value != current, value.lower()))
    return names


def fetch_origin(root: Path | None = None) -> None:
    base = root or repo_root()
    result = _run(["git", "fetch", "origin", "--prune"], cwd=base, timeout=120)
    if result.returncode != 0:
        raise AppCtlError(
            "git fetch origin ist fehlgeschlagen:\n"
            + (result.stderr or result.stdout or "").strip()
        )


def _require_clean_tracked(root: Path) -> None:
    dirty = git_dirty_tracked_files(root)
    if dirty:
        preview = ", ".join(dirty[:8])
        extra = f" (+{len(dirty) - 8})" if len(dirty) > 8 else ""
        raise AppCtlError(
            "Es gibt lokale, nicht committete Änderungen. "
            "Bitte zuerst committen oder stashen.\n"
            f"Geändert: {preview}{extra}"
        )


def pull_current_branch(root: Path | None = None) -> str:
    base = root or repo_root()
    _require_clean_tracked(base)
    branch = git_current_branch(base)
    if not branch:
        raise AppCtlError("HEAD ist detached — bitte zuerst einen Branch auswählen.")
    fetch_origin(base)
    result = _run(["git", "pull", "--ff-only"], cwd=base, timeout=120)
    if result.returncode != 0:
        raise AppCtlError(
            "git pull --ff-only ist fehlgeschlagen "
            "(Branch ist nicht fast-forward oder hat keinen Upstream):\n"
            + (result.stderr or result.stdout or "").strip()
        )
    revision = git_revision(base) or "?"
    return f"{branch} @ {revision}"


def _ref_exists(root: Path, ref: str) -> bool:
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", ref],
        cwd=root,
        timeout=8,
    )
    return result.returncode == 0


def checkout_and_pull(branch: str, root: Path | None = None) -> str:
    base = root or repo_root()
    name = normalize_branch_name(branch)
    if not is_valid_branch_name(name):
        raise AppCtlError(f"Ungültiger Branch-Name: {branch!r}")
    _require_clean_tracked(base)
    fetch_result = _run(["git", "fetch", "origin", "--prune"], cwd=base, timeout=120)
    remote_ok = fetch_result.returncode == 0
    local_ref = f"refs/heads/{name}"
    remote_ref = f"refs/remotes/origin/{name}"
    if remote_ok and _ref_exists(base, remote_ref):
        switched = _run(
            ["git", "switch", "-C", name, "--track", f"origin/{name}"],
            cwd=base,
            timeout=30,
        )
        if switched.returncode != 0:
            # ältere git-switch-Semantik / Branch existiert lokal bereits
            switched = _run(["git", "switch", name], cwd=base, timeout=30)
            if switched.returncode != 0:
                switched = _run(
                    ["git", "checkout", "-B", name, f"origin/{name}"],
                    cwd=base,
                    timeout=30,
                )
        if switched.returncode != 0:
            raise AppCtlError(
                f"Branch {name} konnte nicht ausgecheckt werden:\n"
                + (switched.stderr or switched.stdout or "").strip()
            )
    elif _ref_exists(base, local_ref):
        switched = _run(["git", "switch", name], cwd=base, timeout=30)
        if switched.returncode != 0:
            switched = _run(["git", "checkout", name], cwd=base, timeout=30)
        if switched.returncode != 0:
            raise AppCtlError(
                f"Lokaler Branch {name} konnte nicht ausgecheckt werden:\n"
                + (switched.stderr or switched.stdout or "").strip()
            )
    else:
        hint = (fetch_result.stderr or fetch_result.stdout or "").strip()
        extra = f"\nFetch: {hint}" if hint and not remote_ok else ""
        raise AppCtlError(f"Branch {name} existiert weder lokal noch auf origin.{extra}")

    pull = _run(["git", "pull", "--ff-only"], cwd=base, timeout=120)
    if pull.returncode != 0:
        upstream = _run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
            cwd=base,
            timeout=8,
        )
        if upstream.returncode == 0:
            raise AppCtlError(
                "git pull --ff-only nach dem Checkout ist fehlgeschlagen:\n"
                + (pull.stderr or pull.stdout or "").strip()
            )
    revision = git_revision(base) or "?"
    return f"{name} @ {revision}"


def get_status(root: Path | None = None, port: int = DEFAULT_PORT) -> AppStatus:
    base = root or repo_root()
    processes = collect_app_pids(base, port)
    return AppStatus(
        port=port,
        healthy=http_is_healthy(port),
        pids=[item.pid for item in processes],
        processes=processes,
        pid_file=_read_pid_file(base),
        branch=git_current_branch(base),
        revision=git_revision(base),
        log_path=log_file_path(base),
    )


def _send_signal(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
    except OSError:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            return


def _terminate_pids(pids: list[int]) -> list[str]:
    lines: list[str] = []
    unique = []
    seen: set[int] = set()
    mine = {os.getpid(), os.getppid()}
    for pid in pids:
        if pid in seen or pid in mine or pid <= 1:
            continue
        seen.add(pid)
        unique.append(pid)
    for pid in unique:
        _send_signal(pid, signal.SIGTERM)
        lines.append(f"SIGTERM → PID {pid}")
    deadline = time.time() + STOP_WAIT_SECONDS
    pending = set(unique)
    while pending and time.time() < deadline:
        pending = {pid for pid in pending if _pid_is_alive(pid)}
        if pending:
            time.sleep(0.2)
    for pid in sorted(pending):
        _send_signal(pid, signal.SIGKILL)
        lines.append(f"SIGKILL → PID {pid}")
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
    return lines


def stop_app(root: Path | None = None, port: int = DEFAULT_PORT) -> CommandResult:
    base = root or repo_root()
    processes = collect_app_pids(base, port)
    listeners = pids_listening_on_port(port)
    details: list[str] = []
    kill_pids: list[int] = []

    for pid in listeners:
        info = next((item for item in processes if item.pid == pid), None)
        command = info.command if info is not None else _read_command(pid)
        if command and not is_killable_listener_command(command):
            raise AppCtlError(
                f"Port {port} wird von einem fremden Prozess belegt "
                f"(PID {pid}: {command}). Bitte manuell beenden."
            )
        kill_pids.append(pid)
        details.append(f"Port {port}: PID {pid} · {command or '?'}")

    for info in processes:
        if info.pid not in kill_pids:
            kill_pids.append(info.pid)
            details.append(f"Streamlit/Supervisor: PID {info.pid} · {info.command or '?'}")

    if not kill_pids:
        _clear_pid_file(base)
        return CommandResult(ok=True, message="App war bereits gestoppt.", details=details)

    details.extend(_terminate_pids(kill_pids))
    _clear_pid_file(base)
    leftover = pids_listening_on_port(port)
    if leftover:
        return CommandResult(
            ok=False,
            message=f"Port {port} ist nach dem Stoppen noch belegt (PIDs {leftover}).",
            details=details,
        )
    return CommandResult(ok=True, message="App gestoppt.", details=details)


def _wait_until_healthy(port: int, timeout: float = START_WAIT_SECONDS) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_is_healthy(port):
            return True
        time.sleep(0.25)
    return False


def start_app(
    root: Path | None = None,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
    force: bool = False,
) -> CommandResult:
    base = root or repo_root()
    details: list[str] = []
    if not force and http_is_healthy(port):
        if open_browser:
            _open_browser(port)
        return CommandResult(
            ok=True,
            message=f"App läuft bereits unter {app_url(port)}",
            details=details,
        )

    stop = stop_app(base, port)
    details.extend(stop.details)
    if not stop.ok:
        return CommandResult(ok=False, message=stop.message, details=details)

    python = venv_python(base)
    supervisor = supervisor_script(base)
    if not supervisor.is_file():
        raise AppCtlError(f"Supervisor fehlt: {supervisor}")
    log_path = log_file_path(base)
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        popen_kwargs: dict = {
            "cwd": str(base),
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [str(python), str(supervisor), "--port", str(port)],
                **popen_kwargs,
            )
        except OSError as exc:
            raise AppCtlError(f"App konnte nicht gestartet werden: {exc}") from exc
    finally:
        log_handle.close()
    _write_pid_file(base, proc.pid)
    details.append(f"Supervisor gestartet · PID {proc.pid}")
    details.append(f"Log: {log_path}")
    if proc.poll() is not None:
        _clear_pid_file(base)
        tail = _tail_log(log_path)
        return CommandResult(
            ok=False,
            message="Supervisor ist sofort beendet. Siehe Log.",
            details=details + ([tail] if tail else []),
        )
    if not _wait_until_healthy(port):
        tail = _tail_log(log_path)
        return CommandResult(
            ok=False,
            message=(
                f"App antwortet nicht auf {health_url(port)}. "
                "Port war frei, Start hing aber — Log prüfen."
            ),
            details=details + ([tail] if tail else []),
        )
    if open_browser:
        _open_browser(port)
    branch = git_current_branch(base) or "?"
    revision = git_revision(base) or "?"
    return CommandResult(
        ok=True,
        message=f"App läuft: {app_url(port)} · {branch} @ {revision}",
        details=details,
    )


def restart_app(
    root: Path | None = None,
    port: int = DEFAULT_PORT,
    *,
    pull: bool = False,
    branch: str | None = None,
    open_browser: bool = True,
) -> CommandResult:
    base = root or repo_root()
    details: list[str] = []
    stop = stop_app(base, port)
    details.extend(stop.details)
    if not stop.ok:
        return CommandResult(ok=False, message=stop.message, details=details)
    if branch:
        label = checkout_and_pull(branch, base)
        details.append(f"Checkout: {label}")
    elif pull:
        label = pull_current_branch(base)
        details.append(f"Pull: {label}")
    started = start_app(base, port, open_browser=open_browser, force=True)
    details.extend(started.details)
    return CommandResult(ok=started.ok, message=started.message, details=details)


def _open_browser(port: int) -> None:
    import webbrowser

    try:
        webbrowser.open(app_url(port), new=2)
    except Exception:
        return


def _tail_log(path: Path, lines: int = 20) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    chunk = text.splitlines()[-lines:]
    return "\n".join(chunk)


def format_status(status: AppStatus) -> str:
    branch = status.branch or "?"
    revision = status.revision or "?"
    if status.healthy:
        state = f"läuft ({app_url(status.port)})"
    elif status.pids:
        state = f"Port {status.port} belegt, App antwortet nicht — Altprozess"
    else:
        state = "gestoppt"
    pid_txt = ", ".join(str(pid) for pid in status.pids) or "—"
    return f"{state} · {branch} @ {revision} · PIDs {pid_txt}"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OTIO Streamlit-App hart stoppen und neu starten "
            "(inkl. git pull / Branch-Wechsel)."
        )
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Status, Branch und Port anzeigen")
    sub.add_parser("stop", help="Port und Streamlit-Prozesse beenden")
    start = sub.add_parser("start", help="Starten; räumt blockierte Ports zuerst")
    start.add_argument("--no-browser", action="store_true")
    restart = sub.add_parser("restart", help="Stoppen und neu starten")
    restart.add_argument("--pull", action="store_true", help="Vorher git pull --ff-only")
    restart.add_argument("--branch", help="Branch auschecken, pullen, dann starten")
    restart.add_argument("--no-browser", action="store_true")
    sub.add_parser("branches", help="Lokale und origin-Branches listen (fetch)")
    pull = sub.add_parser("pull", help="Nur git pull --ff-only (App nicht starten)")
    pull.add_argument("--branch", help="Zuerst diesen Branch auschecken")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    root = repo_root()
    port = int(args.port)
    try:
        if args.command == "status":
            print(format_status(get_status(root, port)))
            return 0
        if args.command == "stop":
            result = stop_app(root, port)
            _print_result(result)
            return 0 if result.ok else 1
        if args.command == "start":
            result = start_app(root, port, open_browser=not args.no_browser)
            _print_result(result)
            return 0 if result.ok else 1
        if args.command == "restart":
            result = restart_app(
                root,
                port,
                pull=bool(args.pull),
                branch=args.branch,
                open_browser=not args.no_browser,
            )
            _print_result(result)
            return 0 if result.ok else 1
        if args.command == "branches":
            for name in list_git_branches(root, fetch=True):
                print(name)
            return 0
        if args.command == "pull":
            if args.branch:
                message = checkout_and_pull(args.branch, root)
            else:
                message = pull_current_branch(root)
            print(message)
            return 0
    except AppCtlError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.error(f"Unbekanntes Kommando: {args.command}")
    return 2


def _print_result(result: CommandResult) -> None:
    print(result.message)
    for line in result.details:
        if line:
            print(f"  {line}")


if __name__ == "__main__":
    raise SystemExit(main())
