"""R4: Resolve-safe Relink für portables OTIO-Paket."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
    export_portable_otio_package,
)
from otio_app.services.without_voiceover_enhanced.relink_for_resolve import (
    RelinkError,
    _positive_ffprobe_duration,
    _validate_video,
    package_root_from_script,
    path_to_file_uri,
    relink_package,
)
from tests.test_enhanced_otio_portable_export import _build_full_project
from tests.test_keyword_flow_e2e_production import (
    _build_chapter_a_project,
    _plan_with_pause_and_closing,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    resolve_unified_timeline,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _collect_urls(timeline) -> list[str]:
    urls: list[str] = []
    for track in timeline.tracks:
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            media = item.media_reference
            if media is None:
                continue
            target = getattr(media, "target_url", None)
            if target:
                urls.append(str(target))
    return urls


def _structure_signature(timeline) -> dict:
    tracks = []
    for track in timeline.tracks:
        items = []
        for item in track:
            if isinstance(item, otio.schema.Gap):
                items.append(
                    {
                        "kind": "gap",
                        "duration": round(item.duration().to_seconds(), 6),
                    }
                )
            elif isinstance(item, otio.schema.Clip):
                src = item.source_range
                items.append(
                    {
                        "kind": "clip",
                        "name": item.name,
                        "duration": round(item.duration().to_seconds(), 6),
                        "src_start": round(src.start_time.to_seconds(), 6)
                        if src
                        else None,
                        "src_duration": round(src.duration.to_seconds(), 6)
                        if src
                        else None,
                    }
                )
        tracks.append({"track_kind": str(track.kind), "items": items})
    return {"tracks": tracks}


def test_portable_package_contains_relink_script_and_readme(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_ship")
    assert (package / "relink_for_resolve.py").is_file()
    readme = (package / "README.md").read_text(encoding="utf-8")
    assert "relink_for_resolve.py" in readme
    assert "timeline_resolve.otio" in readme
    assert "nicht" in readme.lower() and "timeline.otio" in readme
    assert (package / "timeline.otio").is_file()
    assert (package / "media_manifest.json").is_file()
    assert (package / "media").is_dir()


def test_relink_script_uses_file_dir_as_package_root(tmp_path: Path) -> None:
    script = tmp_path / "pkg" / "relink_for_resolve.py"
    script.parent.mkdir(parents=True)
    script.write_text("# placeholder\n", encoding="utf-8")
    assert package_root_from_script(script) == script.parent.resolve()


def test_relink_creates_resolve_otio_keeps_transport(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_relink")
    transport = package / "timeline.otio"
    before = transport.read_bytes()
    before_sha = _sha256(transport)
    out = relink_package(package)
    assert out == package / "timeline_resolve.otio"
    assert out.is_file()
    assert transport.read_bytes() == before
    assert _sha256(transport) == before_sha

    original = otio.adapters.read_from_file(str(transport))
    resolve_tl = otio.adapters.read_from_file(str(out))
    assert _structure_signature(original) == _structure_signature(resolve_tl)

    urls = _collect_urls(resolve_tl)
    assert urls
    for url in urls:
        assert url.startswith("file://")
        assert Path(url).is_absolute() is False  # file URI, not Path
        assert "media/" not in url or url.startswith("file://")
        assert not url.lower().startswith(("http://", "https://"))
        assert "/opt/cursor" not in url or url.startswith("file://")
        # After as_uri, path must exist
        from urllib.parse import unquote, urlparse

        path = Path(unquote(urlparse(url).path))
        assert path.is_file()
        assert path.is_absolute()
        assert str(path).startswith(str((package / "media").resolve()))
    # Transport remains relative
    for url in _collect_urls(original):
        assert url.startswith("media/")
        assert not url.startswith("file://")


def test_relink_blocks_cloud_and_relative_in_resolve_otio(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_abs")
    relink_package(package)
    urls = _collect_urls(
        otio.adapters.read_from_file(str(package / "timeline_resolve.otio"))
    )
    for url in urls:
        assert url.startswith("file://")
        assert "/tmp/" not in url or "file://" in url
        # relative media paths forbidden in resolve OTIO
        assert not url.startswith("media/")
        assert "http://" not in url.lower()
        assert "https://" not in url.lower()


def test_relink_spaces_and_umlauts_in_package_path(tmp_path: Path) -> None:
    from urllib.parse import unquote, urlparse

    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_unicode")
    weird = tmp_path / "Paket äöü & test (1)"
    shutil.copytree(package, weird)
    out = relink_package(weird)
    assert out.is_file()
    urls = _collect_urls(otio.adapters.read_from_file(str(out)))
    assert urls
    for url in urls:
        assert url.startswith("file://")
        path = Path(unquote(urlparse(url).path))
        assert path.is_file()
        assert path.is_absolute()
        # Package path with spaces/umlauts must appear (encoded) in file URI.
        assert "%20" in url or "%C3%" in url.upper() or "ä" in unquote(url)
        assert path_to_file_uri(path) == url


def test_relink_missing_media_blocks(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_missing")
    media_files = list((package / "media").glob("*"))
    assert media_files
    media_files[0].unlink()
    with pytest.raises(RelinkError, match="fehlt|Mediendatei"):
        relink_package(package)
    assert not (package / "timeline_resolve.otio").exists()


def test_relink_missing_manifest_entry_blocks(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_manifest")
    manifest_path = package / "media_manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Drop first packaged filename from manifest while file remains.
    dropped = rows[0]["packaged_filename"]
    rows = [r for r in rows if r["packaged_filename"] != dropped]
    manifest_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with pytest.raises(RelinkError, match="Manifesteintrag"):
        relink_package(package)
    assert not (package / "timeline_resolve.otio").exists()


def test_relink_checksum_mismatch_blocks(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_sha")
    manifest_path = package / "media_manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows[0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with pytest.raises(RelinkError, match="Prüfsumme"):
        relink_package(package)
    assert not (package / "timeline_resolve.otio").exists()


def test_relink_path_traversal_blocks(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_trav")
    tl_path = package / "timeline.otio"
    payload = json.loads(tl_path.read_text(encoding="utf-8"))

    def _poison(node):
        if isinstance(node, dict):
            if str(node.get("OTIO_SCHEMA") or "").startswith("ExternalReference"):
                node["target_url"] = "media/../README.md"
            for value in node.values():
                _poison(value)
        elif isinstance(node, list):
            for item in node:
                _poison(item)

    _poison(payload)
    tl_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RelinkError, match="Traversal|außerhalb|Manifest"):
        relink_package(package)
    assert not (package / "timeline_resolve.otio").exists()


def test_relink_duplicate_manifest_filename_blocks(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_dup")
    manifest_path = package / "media_manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) >= 1
    dup = dict(rows[0])
    dup["asset_id"] = dup["asset_id"] + "__dup"
    rows.append(dup)
    manifest_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with pytest.raises(RelinkError, match="Doppelte|mehrdeutige"):
        relink_package(package)


def test_relink_revalidates_video(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_video")
    # Corrupt a video media file but keep size/sha by rewriting after sha check...
    # Better: change bytes and also update sha to match so validation reaches ffprobe.
    media = next(p for p in (package / "media").glob("*.mov"))
    media.write_bytes(b"not-a-video")
    rows = json.loads((package / "media_manifest.json").read_text(encoding="utf-8"))
    for row in rows:
        if row["packaged_filename"] == media.name:
            row["sha256"] = _sha256(media)
    (package / "media_manifest.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    with pytest.raises(RelinkError, match="Video|ffprobe|ungültig"):
        relink_package(package)


def _ffprobe_payload(
    *,
    stream_duration: object | None = "2.5",
    format_duration: object | None = "2.5",
    width: int = 320,
    height: int = 240,
) -> dict:
    stream: dict = {
        "codec_type": "video",
        "width": width,
        "height": height,
    }
    if stream_duration is not None:
        stream["duration"] = stream_duration
    fmt: dict = {}
    if format_duration is not None:
        fmt["duration"] = format_duration
    return {"streams": [stream], "format": fmt}


def _patch_ffprobe(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    class _Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.relink_for_resolve.subprocess.run",
        lambda *args, **kwargs: _Result(),
    )


def test_validate_video_positive_stream_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "ok.mp4"
    media.write_bytes(b"x" * 64)
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="1.25", format_duration=None),
    )
    _validate_video(media)


def test_validate_video_format_duration_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "fmt.mp4"
    media.write_bytes(b"x" * 64)
    # Fehlende Stream-Dauer → Format-Dauer.
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration=None, format_duration="3.0"),
    )
    _validate_video(media)
    # Stream-Dauer 0 ist nicht positiv → Format-Dauer.
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="0", format_duration="1.5"),
    )
    _validate_video(media)


def test_validate_video_missing_durations_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "nodur.mp4"
    media.write_bytes(b"x" * 64)
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration=None, format_duration=None),
    )
    with pytest.raises(RelinkError, match="positive Dauer"):
        _validate_video(media)


def test_validate_video_zero_durations_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "zero.mp4"
    media.write_bytes(b"x" * 64)
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="0", format_duration="0.0"),
    )
    with pytest.raises(RelinkError, match="positive Dauer"):
        _validate_video(media)


def test_validate_video_negative_duration_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "neg.mp4"
    media.write_bytes(b"x" * 64)
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="-1", format_duration="-0.5"),
    )
    with pytest.raises(RelinkError, match="positive Dauer"):
        _validate_video(media)


def test_validate_video_non_numeric_duration_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "nan.mp4"
    media.write_bytes(b"x" * 64)
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="n/a", format_duration="???"),
    )
    with pytest.raises(RelinkError, match="positive Dauer"):
        _validate_video(media)
    assert _positive_ffprobe_duration("n/a") is None
    assert _positive_ffprobe_duration("2.0") == pytest.approx(2.0)


def test_validate_video_nonzero_filesize_without_duration_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dateigröße darf fehlende positive Dauer nicht ersetzen."""
    media = tmp_path / "sized.mp4"
    media.write_bytes(b"not-empty-but-no-duration" * 32)
    assert media.stat().st_size > 0
    _patch_ffprobe(
        monkeypatch,
        _ffprobe_payload(stream_duration="0", format_duration=None),
    )
    with pytest.raises(RelinkError, match="positive Dauer"):
        _validate_video(media)


def test_local_production_export_unchanged_by_relink(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    local = export_otio_from_resolved_timeline(
        project, basename="r4_local_ok", resolved=resolved
    )
    urls = _collect_urls(otio.adapters.read_from_file(str(local)))
    assert urls
    for url in urls:
        # Local export remains absolute host paths (not file:// rewrite pipeline).
        assert not url.startswith("media/")
        assert Path(url).is_file()


def test_standalone_script_main_succeeds(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    package = export_portable_otio_package(project, basename="r4_cli")
    script = package / "relink_for_resolve.py"
    result = subprocess.run(
        ["python3", str(script)],
        cwd=str(package),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (package / "timeline_resolve.otio").is_file()
    assert "OK:" in result.stdout
