"""Tests für den lokalen App-Launcher (Start/Stop/git)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.app_ctl import (
    AppCtlError,
    CommandResult,
    ProcessInfo,
    build_arg_parser,
    checkout_and_pull,
    format_status,
    get_status,
    git_current_branch,
    git_dirty_tracked_files,
    is_killable_listener_command,
    is_otio_streamlit_command,
    is_otio_streamlit_process,
    is_valid_branch_name,
    list_git_branches,
    normalize_branch_name,
    restart_app,
    start_app,
    stop_app,
)
import otio_app.app_ctl as app_ctl
import otio_app.shutdown as shutdown


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "OTIO Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "otio@example.test")
    env.setdefault("GIT_COMMITTER_NAME", "OTIO Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "otio@example.test")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "otio@example.test")
    _git(path, "config", "user.name", "OTIO Test")
    (path / "README").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-m", "init")
    return path


def test_is_otio_streamlit_command_matches_this_repo() -> None:
    root = Path("/Users/claudiakuhn/Documents/otio")
    assert is_otio_streamlit_command(
        "/Users/claudiakuhn/Documents/otio/.venv/bin/python -m streamlit run app.py",
        root,
    )
    assert is_otio_streamlit_command(
        "/Users/claudiakuhn/Documents/otio/.venv/bin/python "
        "/Users/claudiakuhn/Documents/otio/scripts/run_otio_streamlit.py --port 8501",
        root,
    )
    assert not is_otio_streamlit_command(
        "/Users/claudiakuhn/Documents/otio/.venv/bin/python scripts/otio_launcher.py",
        root,
    )
    assert not is_otio_streamlit_command(
        "pytest tests/test_app_ctl.py",
        root,
    )
    assert not is_otio_streamlit_command(
        "/other/project/.venv/bin/python -m streamlit run app.py",
        root,
    )
    assert is_otio_streamlit_process(
        "python -m streamlit run app.py --server.port 8501",
        root,
        cwd=str(root),
    )
    assert not is_otio_streamlit_process(
        "python -m streamlit run app.py",
        root,
        cwd="/other/project",
    )


def test_branch_name_validation() -> None:
    assert is_valid_branch_name("cursor/coverage-gap-crosslang-analysis-71ca")
    assert is_valid_branch_name("main")
    assert not is_valid_branch_name("-evil")
    assert not is_valid_branch_name("foo; rm -rf /")
    assert not is_valid_branch_name("../x")
    assert normalize_branch_name("origin/feat/x") == "feat/x"
    assert normalize_branch_name("remotes/origin/feat/x") == "feat/x"


def test_killable_listener_detects_python_not_nginx() -> None:
    assert is_killable_listener_command("python -m streamlit run app.py")
    assert not is_killable_listener_command("nginx: master process")


def test_stop_app_when_nothing_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_ctl, "collect_app_pids", lambda *a, **k: [])
    monkeypatch.setattr(app_ctl, "pids_listening_on_port", lambda port: [])
    result = stop_app(tmp_path)
    assert result.ok
    assert "gestoppt" in result.message.lower() or "bereits" in result.message.lower()


def test_stop_app_refuses_foreign_port_holder(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_ctl, "collect_app_pids", lambda *a, **k: [])
    monkeypatch.setattr(app_ctl, "pids_listening_on_port", lambda port: [4242])
    monkeypatch.setattr(app_ctl, "_read_command", lambda pid: "nginx: master process")
    with pytest.raises(AppCtlError, match="fremden Prozess"):
        stop_app(tmp_path)


def test_stop_app_terminates_streamlit_listener(monkeypatch, tmp_path: Path) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        app_ctl,
        "collect_app_pids",
        lambda *a, **k: [
            ProcessInfo(pid=4321, command="python -m streamlit run app.py", cwd=str(tmp_path))
        ],
    )
    leftover_calls = {"n": 0}

    def leftover(_port: int) -> list[int]:
        leftover_calls["n"] += 1
        if leftover_calls["n"] == 1:
            return [4321]
        return []

    monkeypatch.setattr(app_ctl, "pids_listening_on_port", leftover)
    monkeypatch.setattr(app_ctl, "_read_command", lambda pid: "python -m streamlit run app.py")
    monkeypatch.setattr(app_ctl, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(app_ctl, "_send_signal", lambda pid, sig: sent.append((pid, sig)))
    result = stop_app(tmp_path)
    assert result.ok
    assert sent and sent[0][0] == 4321


def test_start_skips_when_healthy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_ctl, "http_is_healthy", lambda port, timeout=1.5: True)
    opened: list[int] = []
    monkeypatch.setattr(app_ctl, "_open_browser", lambda port: opened.append(port))
    result = start_app(tmp_path, open_browser=True)
    assert result.ok
    assert "läuft bereits" in result.message
    assert opened == [8501]


def test_restart_checks_out_branch_before_start(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        app_ctl, "stop_app", lambda *a, **k: CommandResult(ok=True, message="stopped")
    )
    monkeypatch.setattr(
        app_ctl,
        "checkout_and_pull",
        lambda branch, root=None: calls.append(branch) or "feat/x @ abc",
    )
    monkeypatch.setattr(
        app_ctl,
        "pull_current_branch",
        lambda root=None: calls.append("PULL") or "main @ abc",
    )
    monkeypatch.setattr(
        app_ctl,
        "start_app",
        lambda *a, **k: CommandResult(ok=True, message="started"),
    )
    result = restart_app(
        tmp_path, pull=True, branch="feat/x", open_browser=False
    )
    assert result.ok
    assert calls == ["feat/x"]


def test_launcher_command_file_is_executable() -> None:
    path = Path(__file__).resolve().parents[1] / "OTIO starten.command"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    text = path.read_text(encoding="utf-8")
    assert "otio_launcher.py" in text
    supervisor = Path(__file__).resolve().parents[1] / "scripts" / "run_otio_streamlit.py"
    assert supervisor.is_file()


def test_cli_restart_pull_and_branch_flags() -> None:
    args = build_arg_parser().parse_args(["restart", "--pull", "--branch", "feat/x"])
    assert args.command == "restart"
    assert args.pull is True
    assert args.branch == "feat/x"
    args = build_arg_parser().parse_args(["restart", "--pull", "--branch", "feat/x"])
    assert args.command == "restart"
    assert args.pull is True
    assert args.branch == "feat/x"


def test_git_checkout_local_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feat/local")
    (repo / "README").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "feat")
    _git(repo, "checkout", "main")
    assert git_current_branch(repo) == "main"
    label = checkout_and_pull("feat/local", repo)
    assert git_current_branch(repo) == "feat/local"
    assert "feat/local" in label


def test_git_dirty_blocks_checkout(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "other")
    _git(repo, "checkout", "main")
    (repo / "README").write_text("dirty\n", encoding="utf-8")
    assert git_dirty_tracked_files(repo) == ["README"]
    with pytest.raises(AppCtlError, match="nicht committete"):
        checkout_and_pull("other", repo)


def test_list_git_branches_includes_local(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feat/a")
    names = list_git_branches(repo, fetch=False)
    assert "main" in names
    assert "feat/a" in names


def test_format_status_reports_stale_port() -> None:
    status = app_ctl.AppStatus(
        port=8501,
        healthy=False,
        pids=[12],
        branch="main",
        revision="abc123",
    )
    text = format_status(status)
    assert "Altprozess" in text
    assert "abc123" in text


def test_get_status_uses_injected_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_ctl, "collect_app_pids", lambda *a, **k: [])
    monkeypatch.setattr(app_ctl, "http_is_healthy", lambda port, timeout=1.5: False)
    monkeypatch.setattr(app_ctl, "git_current_branch", lambda root=None: "demo")
    monkeypatch.setattr(app_ctl, "git_revision", lambda root=None: "deadbee")
    status = get_status(tmp_path)
    assert status.branch == "demo"
    assert status.healthy is False
    assert status.pids == []


def test_cancel_all_background_jobs_resets_registry(monkeypatch) -> None:
    reset = MagicMock(return_value=2)
    monkeypatch.setattr(
        "otio_app.services.job_registry.force_reset_all_jobs",
        reset,
    )
    managers = []

    def _mgr() -> MagicMock:
        mock = MagicMock()
        managers.append(mock)
        return mock

    monkeypatch.setattr(
        "otio_app.services.clean_media_job.get_clean_media_job_manager",
        _mgr,
    )
    monkeypatch.setattr(
        "otio_app.services.voice_analysis_job.get_voice_analysis_job_manager",
        _mgr,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analysis_job.get_asset_analysis_job_manager",
        _mgr,
    )
    monkeypatch.setattr(
        "otio_app.services.otio_export_job.get_otio_export_job_manager",
        _mgr,
    )
    shutdown.cancel_all_background_jobs()
    reset.assert_called_once()
    assert len(managers) == 4
    for mock in managers:
        mock.cancel_all_running.assert_called_once()
