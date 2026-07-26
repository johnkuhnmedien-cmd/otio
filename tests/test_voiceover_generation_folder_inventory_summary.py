"""Phase 3: folder_inventory_summary.py — reine Python-Aggregation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import VOICEOVER_GEN_MAX_FOLDER_WORDS, VOICEOVER_GEN_MIN_FOLDER_WORDS
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_summaries_path
from otio_app.services.voiceover_generation.folder_inventory_summary import (
    RISK_IMAGES_ONLY,
    RISK_MISSING_DESCRIPTIONS,
    RISK_NO_ASSETS,
    RISK_VERY_FEW_ASSETS,
    build_and_save_folder_inventory_summaries,
    build_folder_inventory_summary,
    load_folder_inventory_summaries,
)

_TRACE_MODULE = "otio_app.services.voiceover_generation.folder_inventory_summary"


def _make_project(tmp_path: Path, folders: list[str]) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in folders:
        (project_root / folder).mkdir()
    return Project(
        id="dram-project",
        name="Dramaturgy Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )


def _asset(path: str, description: str = "") -> AssetMediaAnalysis:
    return AssetMediaAnalysis(path=path, description=description)


def test_summary_counts_video_and_image_assets(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    inventory = AssetFolderAnalysis(
        folder="Grand Canyon",
        assets=[
            _asset("Grand Canyon/clip1.mp4", "Weite Schlucht bei Sonnenuntergang."),
            _asset("Grand Canyon/clip2.mp4", "Wanderer am Rand der Schlucht."),
            _asset("Grand Canyon/photo1.jpg", "Nahaufnahme von rotem Gestein."),
        ],
    )
    with patch(f"{_TRACE_MODULE}.probe_duration_seconds", return_value=10.0):
        summary = build_folder_inventory_summary(project, "Grand Canyon", inventory=inventory)

    assert summary.asset_count == 3
    assert summary.video_count == 2
    assert summary.image_count == 1
    assert summary.total_video_duration_sec == 20.0
    assert summary.average_video_duration_sec == 10.0


def test_summary_flags_no_assets_as_risk(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Empty Folder"])
    inventory = AssetFolderAnalysis(folder="Empty Folder", assets=[])
    summary = build_folder_inventory_summary(project, "Empty Folder", inventory=inventory)
    assert summary.asset_count == 0
    assert RISK_NO_ASSETS in summary.risks


def test_summary_flags_very_few_assets_as_risk(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Small Folder"])
    inventory = AssetFolderAnalysis(
        folder="Small Folder",
        assets=[_asset("Small Folder/a.mp4", "Ein einzelner Baum in der Wüste.")],
    )
    with patch(f"{_TRACE_MODULE}.probe_duration_seconds", return_value=5.0):
        summary = build_folder_inventory_summary(project, "Small Folder", inventory=inventory)
    assert RISK_VERY_FEW_ASSETS in summary.risks


def test_summary_flags_missing_descriptions_as_risk(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["No Desc"])
    inventory = AssetFolderAnalysis(
        folder="No Desc",
        assets=[_asset("No Desc/a.mp4", ""), _asset("No Desc/b.mp4", "")],
    )
    with patch(f"{_TRACE_MODULE}.probe_duration_seconds", return_value=5.0):
        summary = build_folder_inventory_summary(project, "No Desc", inventory=inventory)
    assert RISK_MISSING_DESCRIPTIONS in summary.risks


def test_summary_flags_images_only_as_risk(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Photos Only"])
    inventory = AssetFolderAnalysis(
        folder="Photos Only",
        assets=[
            _asset("Photos Only/a.jpg", "Foto 1"),
            _asset("Photos Only/b.jpg", "Foto 2"),
            _asset("Photos Only/c.jpg", "Foto 3"),
            _asset("Photos Only/d.jpg", "Foto 4"),
        ],
    )
    summary = build_folder_inventory_summary(project, "Photos Only", inventory=inventory)
    assert RISK_IMAGES_ONLY in summary.risks


def test_estimated_word_count_within_configured_bounds(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Any Folder"])
    descriptions = [
        f"Einzigartige Beschreibung Nummer {i} mit vielen visuellen Details." for i in range(12)
    ]
    inventory = AssetFolderAnalysis(
        folder="Any Folder",
        assets=[_asset(f"Any Folder/clip{i}.mp4", desc) for i, desc in enumerate(descriptions)],
    )
    with patch(f"{_TRACE_MODULE}.probe_duration_seconds", return_value=8.0):
        summary = build_folder_inventory_summary(project, "Any Folder", inventory=inventory)

    assert VOICEOVER_GEN_MIN_FOLDER_WORDS <= summary.estimated_voiceover_word_count
    assert summary.estimated_voiceover_word_count <= VOICEOVER_GEN_MAX_FOLDER_WORDS
    assert summary.estimated_min_words <= summary.estimated_voiceover_word_count
    assert summary.estimated_voiceover_word_count <= summary.estimated_max_words


def test_estimated_word_count_lower_for_few_assets_low_diversity(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Sparse Folder"])
    inventory = AssetFolderAnalysis(
        folder="Sparse Folder",
        assets=[_asset("Sparse Folder/a.mp4", "Blick auf den Fluss.")],
    )
    with patch(f"{_TRACE_MODULE}.probe_duration_seconds", return_value=4.0):
        summary = build_folder_inventory_summary(project, "Sparse Folder", inventory=inventory)
    assert summary.estimated_voiceover_word_count <= 80


def test_build_and_save_folder_inventory_summaries_writes_json(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Folder A", "Folder B"])
    inventory_a = AssetFolderAnalysis(
        folder="Folder A", assets=[_asset("Folder A/a.mp4", "Beschreibung A")]
    )
    inventory_b = AssetFolderAnalysis(
        folder="Folder B", assets=[_asset("Folder B/b.mp4", "Beschreibung B")]
    )

    def _fake_load(_project: Project, folder_name: str) -> AssetFolderAnalysis:
        return inventory_a if folder_name == "Folder A" else inventory_b

    with patch(f"{_TRACE_MODULE}.load_folder_inventory", side_effect=_fake_load), patch(
        f"{_TRACE_MODULE}.probe_duration_seconds", return_value=5.0
    ):
        summaries = build_and_save_folder_inventory_summaries(project)

    assert len(summaries) == 2
    path = get_folder_inventory_summaries_path(project.work_dir_path)
    assert path.is_file()

    loaded = load_folder_inventory_summaries(project)
    assert loaded is not None
    assert loaded.project_id == project.id
    assert len(loaded.folder_summaries) == 2
    assert {s.folder_name for s in loaded.folder_summaries} == {"Folder A", "Folder B"}


def test_load_folder_inventory_summaries_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Folder A"])
    assert load_folder_inventory_summaries(project) is None
