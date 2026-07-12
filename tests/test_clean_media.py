"""Tests für Clean Media."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import (
    CleanMediaEntry,
    CleanMediaManifest,
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanSettings,
    EditPlanShot,
    MediaProbeInfo,
    TimelineItem,
    TimelineItemTransform,
)
from otio_app.models import Project
from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    CLEAN_STATUS_OK,
    find_clean_file_for_media,
    folder_clean_media_ready,
    load_clean_media_manifest,
    media_asset_number,
    needs_transcode,
    process_and_persist_media_file,
    process_media_file,
    resolve_effective_media_path,
    save_clean_media_manifest,
    transcode_to_clean,
    upsert_clean_media_entry,
    validate_media_file,
)
from otio_app.services.clean_media_settings import CleanMediaSettings, save_clean_media_settings
from otio_app.services.edit_plan_rules import (
    RULE_FOLDER_TITLE,
    save_edit_plan_rules,
)
from otio_app.services.media_inventory_cache import resolve_media_for_analysis
from otio_app.services.otio_exporter import MergedEditPlanResult, build_otio_timeline
from otio_app.services.otio_media_transform import ensure_zoomed_media_for_export


def _project(tmp_path: Path, *, folder_name: str = "Florida Keys") -> Project:
    root = tmp_path / "USA"
    folder = root / folder_name
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    return Project(
        id="clean-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=[folder_name],
        selected_asset_subdirs=[folder_name],
    )


def test_needs_transcode_detects_hevc() -> None:
    probe = MediaProbeInfo(video_codec="hevc", container="mp4")
    path = Path("/tmp/sample.mp4")
    assert needs_transcode(path, probe, decode_ok=True) is True


def test_needs_transcode_accepts_h264_mp4() -> None:
    probe = MediaProbeInfo(video_codec="h264", container="mp4", pixel_format="yuv420p")
    path = Path("/tmp/sample.mp4")
    assert needs_transcode(path, probe, decode_ok=True) is False


def test_media_asset_number_parses_common_names() -> None:
    assert media_asset_number(Path("Arches_National_Park_Asset03.mp4")) == 3
    assert media_asset_number(Path("Arches National Park_Asset12.MOV")) == 12
    assert media_asset_number(Path("clip.mp4")) is None


@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_validate_media_file_ok(mock_probe, _mock_decode, tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    mock_probe.return_value = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
    )
    entry = validate_media_file(media)
    assert entry.status == CLEAN_STATUS_OK
    assert entry.needs_transcode is False


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(False, "decode error"))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_file_transcodes_on_decode_failure(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    save_edit_plan_rules(
        project,
        EditPlanRulesDocument(project_id=project.id, rules=[]),
    )
    media = project.project_root_path / "Florida Keys" / "clip.mp4"

    def _fake_transcode(
        original: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clean")

    mock_probe.return_value = MediaProbeInfo(
        video_codec="hevc",
        container="mp4",
        width=1920,
        height=1080,
    )
    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Florida Keys", media)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert entry.clean_path is not None
    assert Path(entry.clean_path).is_file()


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
def test_find_clean_by_asset_number(_mock_validate, tmp_path: Path) -> None:
    project = _project(tmp_path, folder_name="Arches National Park")
    original = project.project_root_path / "Arches National Park" / "Arches National Park_Asset03.MOV"
    original.write_bytes(b"mov")
    clean = project.work_dir_path / "clean" / "Arches_National_Park" / "Arches_National_Park_Asset03.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"mp4")

    found = find_clean_file_for_media(project, "Arches National Park", original)
    assert found == clean

    from_edit_plan = project.work_dir_path / "clean" / "Arches_National_Park" / "Arches_National_Park_Asset03.mp4"
    found2 = find_clean_file_for_media(project, "Arches National Park", from_edit_plan)
    assert found2 == clean


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
def test_resolve_effective_media_path_uses_manifest(_mock_validate, tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path=str(clean.resolve()),
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    manifest_path = project.work_dir_path / "clean_media" / "Florida_Keys.json"
    save_clean_media_manifest(manifest_path, manifest)

    resolved = resolve_effective_media_path(project, "Florida Keys", original)
    assert resolved == clean


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
def test_resolve_effective_media_path_falls_back_to_expected_clean_path(
    _mock_validate, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    expected_clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    expected_clean.parent.mkdir(parents=True, exist_ok=True)
    expected_clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path="/nonexistent/stale_clean.mp4",
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )

    resolved = resolve_effective_media_path(project, "Florida Keys", original)
    assert resolved == expected_clean


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
def test_resolve_media_for_analysis_prefers_clean(_mock_validate, tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path=str(clean.resolve()),
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )

    resolved = resolve_media_for_analysis(project, "Florida Keys", original)
    assert resolved == clean


def test_folder_clean_media_ready_with_ok_manifest(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                status=CLEAN_STATUS_OK,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )
    assert folder_clean_media_ready(project, "Florida Keys") is True


@patch("otio_app.services.otio_exporter.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
def test_otio_export_uses_clean_path(_mock_clean, _mock_export, tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        CleanMediaManifest(
            project_id=project.id,
            folder="Florida Keys",
            entries=[
                CleanMediaEntry(
                    original_path=str(original.resolve()),
                    clean_path=str(clean.resolve()),
                    status=CLEAN_STATUS_CLEAN,
                )
            ],
        ),
    )

    shot = EditPlanShot(
        voice_file="/voice/test.wav",
        folder="Florida Keys",
        voice_start_sec=0.0,
        voice_end_sec=3.0,
        duration_sec=3.0,
        asset_path=str(original),
        motif="test",
        passage_text="text",
    )
    item = TimelineItem(
        timeline_item_id="item_001",
        type="video_shot",
        section_id="section_florida_keys",
        folder_name="Florida Keys",
        voice_file="/voice/test.wav",
        resolved_media_path=str(original),
        original_asset_path=str(original),
        timeline_in_sec=0.0,
        timeline_out_sec=3.0,
        duration_sec=3.0,
        final_duration_sec=3.0,
        source_in_sec=0.0,
        source_out_sec=3.0,
        voice_start_sec=0.0,
        voice_end_sec=3.0,
        transform=TimelineItemTransform(),
        motif="test",
        passage_text="text",
    )
    merged = MergedEditPlanResult(
        timeline_items=[item],
        shots=[shot],
        settings=EditPlanSettings(section_outro_sec=0.0),
        included_folders=["Florida Keys"],
        skipped_folders=[],
        warnings=[],
        validation_status="OK",
    )

    with patch("otio_app.services.otio_exporter.probe_duration_seconds", return_value=10.0):
        timeline = build_otio_timeline(project, merged)

    video_clip = timeline.tracks[0][0]
    assert clean.name in video_clip.media_reference.target_url
    assert video_clip.name == clean.name


def _enable_zoom_rule(project: Project) -> None:
    save_clean_media_settings(project, CleanMediaSettings(auto_zoom_fill=True))


def _enable_folder_title_rule(project: Project) -> None:
    save_edit_plan_rules(
        project,
        EditPlanRulesDocument(
            project_id=project.id,
            rules=[
                EditPlanRule(
                    id="title",
                    rule_type=RULE_FOLDER_TITLE,
                    enabled=True,
                    params={"font_name": "Helvetica Neue", "duration_sec": 5.0},
                )
            ],
        ),
    )


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_retranscodes_clean_with_wrong_aspect(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Arches National Park")
    _enable_zoom_rule(project)
    original = (
        project.project_root_path
        / "Arches National Park"
        / "Arches_National_Park_Asset03.mp4"
    )
    original.write_bytes(b"original")
    old_clean = (
        project.work_dir_path
        / "clean"
        / "Arches_National_Park"
        / "Arches_National_Park_Asset03.mp4"
    )
    filled_clean = (
        project.work_dir_path
        / "clean"
        / "Arches_National_Park"
        / "Arches_National_Park_Asset03_3840x2160.mp4"
    )
    old_clean.parent.mkdir(parents=True, exist_ok=True)
    old_clean.write_bytes(b"old-clean-still-wide")

    wide = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=4096,
        height=2160,
    )
    filled = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )
    mock_probe.side_effect = lambda path: filled if Path(path).name.endswith("_3840x2160.mp4") else wide

    captured: dict[str, str | None] = {}

    def _fake_transcode(
        original_path: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        captured["video_filter"] = video_filter
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new-clean-zoomed")

    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Arches National Park", original)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert mock_transcode.called
    assert captured["video_filter"] is not None
    assert "scale=3840:2160" in captured["video_filter"]
    assert "crop=3840:2160" in captured["video_filter"]
    assert entry.clean_path is not None
    assert entry.clean_path.endswith("_3840x2160.mp4")


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_transcodes_ok_original_for_zoom(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Arches National Park")
    _enable_zoom_rule(project)
    original = (
        project.project_root_path
        / "Arches National Park"
        / "Arches_National_Park_Asset03.mp4"
    )
    original.write_bytes(b"original")

    wide = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=4096,
        height=2160,
    )
    filled = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )
    mock_probe.side_effect = lambda path: filled if Path(path).name.endswith("_3840x2160.mp4") else wide

    captured: dict[str, str | None] = {}

    def _fake_transcode(
        original_path: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        captured["video_filter"] = video_filter
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clean-zoomed")

    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Arches National Park", original)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert mock_transcode.called
    assert captured["video_filter"] is not None
    assert "crop=3840:2160" in captured["video_filter"]
    assert entry.clean_path is not None
    assert entry.clean_path.endswith("_3840x2160.mp4")


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_ensure_zoomed_media_for_export_returns_rezoomed_clean(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Arches National Park")
    _enable_zoom_rule(project)
    original = (
        project.project_root_path
        / "Arches National Park"
        / "Arches_National_Park_Asset03.mp4"
    )
    original.write_bytes(b"original")
    old_clean = (
        project.work_dir_path
        / "clean"
        / "Arches_National_Park"
        / "Arches_National_Park_Asset03.mp4"
    )
    filled_clean = (
        project.work_dir_path
        / "clean"
        / "Arches_National_Park"
        / "Arches_National_Park_Asset03_3840x2160.mp4"
    )
    old_clean.parent.mkdir(parents=True, exist_ok=True)
    old_clean.write_bytes(b"old-clean-still-wide")

    wide = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=4096,
        height=2160,
    )
    filled = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )

    def _probe_side_effect(path: str | Path) -> MediaProbeInfo:
        probe_path = Path(path)
        try:
            if probe_path.is_file() and probe_path.stat().st_size == len(b"new-clean-zoomed"):
                return filled
        except OSError:
            pass
        return wide

    mock_probe.side_effect = _probe_side_effect

    def _fake_transcode(
        original_path: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new-clean-zoomed")

    mock_transcode.side_effect = _fake_transcode

    notes: list[str] = []
    resolved = ensure_zoomed_media_for_export(
        project,
        "Arches National Park",
        original,
        notes=notes,
    )
    assert resolved == filled_clean
    assert mock_transcode.called
    assert any("3840" in note for note in notes)


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.path_is_readable_file", return_value=True)
@patch("otio_app.services.clean_media._run_command")
@patch("otio_app.services.clean_media.probe_media")
def test_transcode_to_clean_strips_all_metadata(
    mock_probe,
    mock_run_command,
    _mock_readable,
    _mock_validate,
    tmp_path: Path,
) -> None:
    """Regression: Der Clean-Media-Transcode mappt bewusst nur Video-/
    Audio-Stream (kein tmcd-Datenstream) und setzt -reset_timestamps 1 —
    die Frames der Ausgabedatei starten also faktisch bei Null. Ohne
    -map_metadata -1 kopiert ffmpeg per Default aber trotzdem GLOBALE
    Container-Metadaten (inkl. eines eventuellen format-level
    'timecode'-Tags) vom Original in die Ausgabedatei. Dadurch behauptete
    die 'clean' Datei weiterhin den ALTEN (Kamera-)Timecode, obwohl ihr
    tatsächlicher Frame-Inhalt bei Null zurückgesetzt wurde — ein
    Metadaten-/Inhalt-Mismatch, der beim OTIO-Export zu falschen
    available_range-Werten und in DaVinci Resolve zu 'Media Offline'/
    Timecode-Mismatch-Meldungen führen konnte, obwohl die richtige Datei
    referenziert wurde."""
    original = tmp_path / "Bisti_De_Na_Zin_Wilderness_Asset16.mp4"
    original.write_bytes(b"x")
    output_path = tmp_path / "clean" / "Bisti_De_Na_Zin_Wilderness_Asset16.mp4"

    mock_probe.return_value = MediaProbeInfo(video_codec="h264", audio_codec="aac", container="mp4")

    def _fake_run_command(command, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x" * 2000)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    mock_run_command.side_effect = _fake_run_command

    transcode_to_clean(original, output_path)

    assert mock_run_command.called
    command = mock_run_command.call_args[0][0]
    assert "-map_metadata" in command, f"'-map_metadata -1' fehlt im ffmpeg-Kommando: {command}"
    idx = command.index("-map_metadata")
    assert command[idx + 1] == "-1"


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.path_is_readable_file", return_value=True)
@patch("otio_app.services.clean_media._run_command")
@patch("otio_app.services.clean_media.probe_media")
def test_transcode_to_clean_hides_banner_so_real_errors_surface(
    mock_probe,
    mock_run_command,
    _mock_readable,
    _mock_validate,
    tmp_path: Path,
) -> None:
    """Regression: Ohne '-hide_banner'/'-loglevel' beginnt ffmpegs stderr bei
    JEDEM Aufruf mit dem mehrzeiligen Versions-/Build-Banner. Da die UI
    Fehlermeldungen anzeigt (teils gekürzt), verdeckte dieses Banner die
    eigentliche, für die Diagnose relevante Fehlerursache vollständig —
    egal ob der Fehler wirklich existierte oder nicht. '-hide_banner' plus
    ein reduziertes Loglevel stellen sicher, dass ein etwaiger echter
    ffmpeg-Fehler an erster Stelle in stderr steht."""
    original = tmp_path / "Bisti_De_Na_Zin_Wilderness_Asset01.mp4"
    original.write_bytes(b"x")
    output_path = tmp_path / "clean" / "Bisti_De_Na_Zin_Wilderness_Asset01.mp4"

    mock_probe.return_value = MediaProbeInfo(video_codec="h264", audio_codec="aac", container="mp4")

    def _fake_run_command(command, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x" * 2000)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    mock_run_command.side_effect = _fake_run_command

    transcode_to_clean(original, output_path)

    command = mock_run_command.call_args[0][0]
    assert "-hide_banner" in command
    assert "-loglevel" in command


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_auto_zoom_scales_same_aspect_resolution(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Bisti")
    _enable_zoom_rule(project)
    original = (
        project.project_root_path
        / "Bisti"
        / "Bisti_De_Na_Zin_Wilderness_Asset01.mp4"
    )
    original.write_bytes(b"original")

    hd = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=1920,
        height=1080,
    )
    uhd = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )
    mock_probe.side_effect = lambda path: uhd if "3840x2160" in str(path) else hd

    captured: dict[str, str | None] = {}

    def _fake_transcode(
        original_path: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        captured["video_filter"] = video_filter
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clean" * 300)

    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Bisti", original)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert mock_transcode.called
    assert captured["video_filter"] == "scale=3840:2160"


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_ignores_folder_title_rule(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Bisti")
    _enable_folder_title_rule(project)
    original = (
        project.project_root_path
        / "Bisti"
        / "Bisti_De_Na_Zin_Wilderness_Asset01.mp4"
    )
    original.write_bytes(b"original")

    mock_probe.return_value = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )

    entry = process_media_file(project, "Bisti", original)
    assert entry.status == CLEAN_STATUS_OK
    assert not mock_transcode.called


@patch("otio_app.services.clean_media.validate_clean_output", return_value=(True, None))
@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_transcodes_prores_even_with_folder_title_rule(
    mock_probe,
    _mock_decode,
    mock_transcode,
    _mock_validate,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, folder_name="Bisti")
    _enable_folder_title_rule(project)
    original = (
        project.project_root_path
        / "Bisti"
        / "Bisti_De_Na_Zin_Wilderness_Asset06.mp4"
    )
    original.write_bytes(b"original")

    prores = MediaProbeInfo(
        video_codec="prores",
        container="mp4",
        pixel_format="yuv422p10le",
        width=3840,
        height=2160,
    )
    clean = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
        width=3840,
        height=2160,
    )
    mock_probe.side_effect = lambda path: clean if "clean" in str(path) else prores

    captured: dict[str, str | None] = {}

    def _fake_transcode(
        original_path: Path,
        output_path: Path,
        *,
        video_filter: str | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        captured["video_filter"] = video_filter
        captured["output_name"] = output_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clean" * 300)

    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Bisti", original)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert mock_transcode.called
    assert captured.get("video_filter") is None
    assert "_title" not in (captured.get("output_name") or "")


def test_upsert_clean_media_entry_replaces_same_original(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"x")
    first = CleanMediaEntry(original_path=str(original.resolve()), status=CLEAN_STATUS_OK)
    second = CleanMediaEntry(
        original_path=str(original.resolve()),
        clean_path=str((tmp_path / "clean.mov").resolve()),
        status=CLEAN_STATUS_CLEAN,
    )
    upsert_clean_media_entry(project, "Florida Keys", first)
    upsert_clean_media_entry(project, "Florida Keys", second)
    from otio_app.services.clean_media import folder_manifest_path

    manifest = load_clean_media_manifest(folder_manifest_path(project, "Florida Keys"))
    assert manifest is not None
    assert len(manifest.entries) == 1
    assert manifest.entries[0].status == CLEAN_STATUS_CLEAN


@patch("otio_app.services.clean_media.process_media_file")
def test_process_and_persist_media_file_writes_manifest(mock_process, tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"x")
    clean = tmp_path / "out.mov"
    clean.write_bytes(b"c")
    mock_process.return_value = CleanMediaEntry(
        original_path=str(original.resolve()),
        clean_path=str(clean.resolve()),
        status=CLEAN_STATUS_CLEAN,
    )
    entry = process_and_persist_media_file(
        project, "Florida Keys", original, force_transcode=True
    )
    assert entry.status == CLEAN_STATUS_CLEAN
    mock_process.assert_called_once()
    assert mock_process.call_args.kwargs.get("force_transcode") is True
    from otio_app.services.clean_media import folder_manifest_path

    manifest = load_clean_media_manifest(folder_manifest_path(project, "Florida Keys"))
    assert manifest is not None
    assert len(manifest.entries) == 1
    assert manifest.entries[0].clean_path == str(clean.resolve())
