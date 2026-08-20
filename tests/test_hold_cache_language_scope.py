"""Still-Hold-Cache pro Sprache — paralleles FR darf EN-Resolve-Dateien nicht anfassen."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.media_hold import (
    _hold_filenames,
    ensure_gap_placeholder_slate,
    ensure_still_hold_video,
    still_hold_video_filter,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    hold_cache_dir,
    legacy_hold_cache_dir,
    placeholders_dir,
)


def _enhanced_project(tmp_path: Path, *, language: str) -> Project:
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True, exist_ok=True)
    return Project(
        id=f"p-{language}",
        name=f"{language.upper()}_Ungarn",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
        width=1920,
        height=1080,
        fps=25.0,
    )


def _still_content_parts(source: Path, *, duration: float = 4.0) -> tuple[str, ...]:
    resolved = source.expanduser().resolve()
    vf = still_hold_video_filter(width=1920, height=1080)
    return (
        str(resolved),
        f"{duration:.3f}",
        "25.000",
        "1920x1080",
        vf,
        "still_v2",
    )


def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, payload: bytes = b"hold-mp4"):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        del kwargs
        if "-filters" in cmd:
            return subprocess.CompletedProcess(cmd, 0, " drawtext \n", "")
        calls.append(list(cmd))
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(payload)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.media_hold.subprocess.run",
        run,
    )
    return calls


def test_en_and_fr_write_still_holds_to_separate_language_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_ffmpeg(monkeypatch)
    still = tmp_path / "cave.jpg"
    still.write_bytes(b"jpeg")
    en = _enhanced_project(tmp_path, language="en")
    fr = _enhanced_project(tmp_path, language="fr")

    en_out = ensure_still_hold_video(
        en, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )
    fr_out = ensure_still_hold_video(
        fr, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )

    assert en_out != fr_out
    assert en_out.parent == hold_cache_dir(en)
    assert fr_out.parent == hold_cache_dir(fr)
    assert "EN" in en_out.parts
    assert "FR" in fr_out.parts
    shared = en.work_dir_path / "exports" / "hold_cache"
    assert not shared.exists()
    assert en_out.read_bytes() == b"hold-mp4"
    assert fr_out.read_bytes() == b"hold-mp4"
    assert len(calls) == 2


def test_fr_encode_does_not_change_en_still_hold_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads = iter((b"EN-HOLD", b"FR-HOLD"))

    def run(cmd, **kwargs):
        del kwargs
        if "-filters" in cmd:
            return subprocess.CompletedProcess(cmd, 0, " drawtext \n", "")
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(next(payloads))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.media_hold.subprocess.run",
        run,
    )
    still = tmp_path / "cave.jpg"
    still.write_bytes(b"jpeg")
    en = _enhanced_project(tmp_path, language="en")
    fr = _enhanced_project(tmp_path, language="fr")

    en_out = ensure_still_hold_video(
        en, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )
    before = en_out.read_bytes()
    mtime = en_out.stat().st_mtime_ns
    fr_out = ensure_still_hold_video(
        fr, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )

    assert before == b"EN-HOLD"
    assert en_out.read_bytes() == b"EN-HOLD"
    assert en_out.stat().st_mtime_ns == mtime
    assert fr_out.read_bytes() == b"FR-HOLD"


def test_legacy_shared_hold_is_reused_and_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_ffmpeg(monkeypatch, payload=b"SHOULD-NOT-WRITE")
    still = tmp_path / "cave.jpg"
    still.write_bytes(b"jpeg")
    en = _enhanced_project(tmp_path, language="en")
    parts = _still_content_parts(still)
    _scoped, legacy_name = _hold_filenames("still_hold", parts, "EN")
    legacy_path = legacy_hold_cache_dir(en) / legacy_name
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"KEEP-ME")
    mtime = legacy_path.stat().st_mtime_ns

    out = ensure_still_hold_video(
        en, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )

    assert out == legacy_path
    assert out.read_bytes() == b"KEEP-ME"
    assert out.stat().st_mtime_ns == mtime
    assert calls == []


def test_fr_does_not_overwrite_legacy_en_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    still = tmp_path / "cave.jpg"
    still.write_bytes(b"jpeg")
    en = _enhanced_project(tmp_path, language="en")
    fr = _enhanced_project(tmp_path, language="fr")
    parts = _still_content_parts(still)
    _scoped, legacy_name = _hold_filenames("still_hold", parts, "EN")
    legacy_path = legacy_hold_cache_dir(en) / legacy_name
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"EN-RESOLVE")
    mtime = legacy_path.stat().st_mtime_ns

    calls = _fake_ffmpeg(monkeypatch, payload=b"FR-NEW")
    fr_out = ensure_still_hold_video(
        fr, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )

    assert legacy_path.read_bytes() == b"EN-RESOLVE"
    assert legacy_path.stat().st_mtime_ns == mtime
    # Gleicher Still+Dauer: FR darf die Legacy-Datei wiederverwenden, nicht neu encoden.
    assert fr_out == legacy_path
    assert calls == []


def test_atomic_ffmpeg_leaves_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(monkeypatch)
    still = tmp_path / "cave.jpg"
    still.write_bytes(b"jpeg")
    en = _enhanced_project(tmp_path, language="en")
    out = ensure_still_hold_video(
        en, still, duration_seconds=4.0, fps=25.0, width=1920, height=1080
    )
    assert out.is_file()
    leftovers = [p for p in out.parent.glob("*partial*") if p != out]
    assert leftovers == []


def test_placeholders_are_language_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(monkeypatch)
    en = _enhanced_project(tmp_path, language="en")
    fr = _enhanced_project(tmp_path, language="fr")
    kwargs = dict(
        shot_id="slot_001",
        gap_id="gap_001",
        needed_visual="cave",
        start_seconds=0.0,
        end_seconds=2.0,
        fps=25.0,
    )
    en_out = ensure_gap_placeholder_slate(en, **kwargs)
    fr_out = ensure_gap_placeholder_slate(fr, **kwargs)
    assert en_out.parent == placeholders_dir(en)
    assert fr_out.parent == placeholders_dir(fr)
    assert en_out != fr_out
    assert "EN" in en_out.parts
    assert "FR" in fr_out.parts
    assert not (en.work_dir_path / "placeholders").exists()
