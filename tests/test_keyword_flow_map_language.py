"""Map-Opener: Sprach-Prefix EN_/FR_ (und Maps/{LANG}/)."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
    _list_map_media_for_chapter,
    _parse_map_stem,
    decide_map_opener,
)


def _project(tmp_path: Path, *, language: str) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    maps = root / "Maps"
    maps.mkdir(parents=True)
    return Project(
        name="MapLang",
        project_root=str(root),
        work_dir=str(work),
        language=language,
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Skellig Michael", "Maps"],
        asset_subdir_names=["Skellig Michael", "Maps"],
    )


def test_parse_map_stem_language_and_index() -> None:
    assert _parse_map_stem("FR_Skellig Michael_Map") == ("FR", "Skellig_Michael")
    assert _parse_map_stem("EN_37_Dublin_Map")[0] == "EN"
    assert _parse_map_stem("EN_37_Dublin_Map")[1] == "Dublin"
    assert _parse_map_stem("ChapterA") == (None, "ChapterA")


def test_fr_project_prefers_fr_prefix_over_en(tmp_path: Path) -> None:
    project = _project(tmp_path, language="fr")
    maps = Path(project.project_root) / "Maps"
    en = maps / "EN_Skellig Michael_Map.mp4"
    fr = maps / "FR_Skellig Michael_Map.mp4"
    en.write_bytes(b"en")
    fr.write_bytes(b"fr")

    matches = _list_map_media_for_chapter(project, "Skellig Michael")
    assert len(matches) == 1
    assert matches[0]["path"].endswith("FR_Skellig Michael_Map.mp4")


def test_en_project_ignores_fr_prefix(tmp_path: Path) -> None:
    project = _project(tmp_path, language="en")
    maps = Path(project.project_root) / "Maps"
    (maps / "FR_Skellig Michael_Map.mp4").write_bytes(b"fr")
    (maps / "EN_Skellig Michael_Map.mp4").write_bytes(b"en")

    matches = _list_map_media_for_chapter(project, "Skellig Michael")
    assert len(matches) == 1
    assert "EN_Skellig Michael_Map.mp4" in matches[0]["path"]


def test_legacy_unprefixed_still_matches_when_no_tagged(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, language="fr")
    maps = Path(project.project_root) / "Maps"
    (maps / "Skellig Michael_Map.mp4").write_bytes(b"legacy")

    matches = _list_map_media_for_chapter(project, "Skellig Michael")
    assert len(matches) == 1
    assert matches[0]["path"].endswith("Skellig Michael_Map.mp4")


def test_lang_subfolder_maps_fr(tmp_path: Path) -> None:
    project = _project(tmp_path, language="fr")
    maps = Path(project.project_root) / "Maps"
    (maps / "EN").mkdir()
    (maps / "FR").mkdir()
    (maps / "EN" / "Skellig Michael_Map.mp4").write_bytes(b"en")
    (maps / "FR" / "Skellig Michael_Map.mp4").write_bytes(b"fr")

    matches = _list_map_media_for_chapter(project, "Skellig Michael")
    assert len(matches) == 1
    assert "/FR/" in matches[0]["path"].replace("\\", "/")


def test_decide_map_missing_wrong_language_only(tmp_path: Path) -> None:
    project = _project(tmp_path, language="fr")
    maps = Path(project.project_root) / "Maps"
    (maps / "EN_Skellig Michael_Map.mp4").write_bytes(b"en-only")

    decision = decide_map_opener(project, "Skellig Michael")
    assert decision.status == "missing"
