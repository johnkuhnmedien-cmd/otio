"""Globale Enhanced-Cut-Plan-Settings pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import (
    CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
    DEFAULT_ENHANCED_WORK_SUBDIR,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.language_defaults_catalog import (
    get_language_standard,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_UNIFIED,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    CutPlanOptions,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    apply_language_defaults_to_options,
    default_cut_plan_options_for_project,
    get_cut_plan_options_defaults_path,
    load_language_cut_plan_defaults,
    save_language_cut_plan_defaults,
)


def _project(tmp_path: Path, *, language: str = "pt") -> Project:
    root = tmp_path / "Greece"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Athens").mkdir()
    return Project(
        id="pt-greece-cut",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Griechenland",
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


@pytest.fixture()
def cut_plan_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_save_and_load_language_cut_plan_defaults(
    cut_plan_defaults_dir: Path,
) -> None:
    saved = save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=4.0,
            shot_max_sec=12.0,
            min_asset_reuse_distance_shots=6,
            voiceover_preroll_sec=2.0,
            elevenlabs_music_count=5,
        ),
    )
    assert saved.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    assert saved.unified_cut_style == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    assert saved.shot_min_sec == 4.0
    assert saved.elevenlabs_music_count == 5
    loaded = load_language_cut_plan_defaults("PT")
    assert loaded is not None
    assert loaded.shot_max_sec == 12.0
    assert loaded.min_asset_reuse_distance_shots == 6
    assert loaded.voiceover_preroll_sec == 2.0
    assert get_cut_plan_options_defaults_path().is_relative_to(cut_plan_defaults_dir)
    assert get_cut_plan_options_defaults_path().name == CUT_PLAN_OPTIONS_DEFAULTS_FILENAME


def test_load_project_options_uses_language_standard_when_file_missing(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=5.0,
            elevenlabs_music_count=8,
        ),
    )
    project = _project(tmp_path, language="pt")
    loaded = load_cut_plan_options(project)
    assert loaded.unified_cut_style == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    assert loaded.shot_min_sec == 5.0
    assert loaded.elevenlabs_music_count == 8


def test_existing_project_options_are_not_overwritten_by_language_standard(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(shot_min_sec=5.0, elevenlabs_music_count=8),
    )
    project = _project(tmp_path, language="pt")
    save_cut_plan_options(
        project,
        CutPlanOptions(shot_min_sec=3.5, elevenlabs_music_count=2),
    )
    loaded = load_cut_plan_options(project)
    assert loaded.shot_min_sec == 3.5
    assert loaded.elevenlabs_music_count == 2


def test_apply_language_defaults_clamps_and_keeps_mode(
    cut_plan_defaults_dir: Path,
) -> None:
    defaults = save_language_cut_plan_defaults(
        "de",
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            shot_min_sec=6.0,
            shot_max_sec=4.0,
        ),
    )
    applied = apply_language_defaults_to_options(
        CutPlanOptions(shot_min_sec=3.0),
        defaults,
    )
    assert applied.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    assert applied.shot_min_sec == 6.0
    assert applied.shot_max_sec == 6.0


def test_default_for_project_without_standard_is_hardcoded(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    project = _project(tmp_path, language="fr")
    options = default_cut_plan_options_for_project(project)
    assert options.shot_max_sec == 8.0
    assert options.min_asset_reuse_distance_shots == 4
    assert options.elevenlabs_music_count == 4


def test_catalog_includes_cut_plan_options_standard() -> None:
    item = get_language_standard("cut_plan_options")
    assert item.filename == CUT_PLAN_OPTIONS_DEFAULTS_FILENAME
    assert item.tab == "⑦ Cut Plan"
    assert item.per_language is True
