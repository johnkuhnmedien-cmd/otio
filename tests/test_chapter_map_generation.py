"""Tests für Kapitel-Karten (Bulk + Einzel, Prompt, Ablage)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from otio_app.defaults import (
    CHAPTER_MAP_STATUS_MISSING,
    CHAPTER_MAP_STATUS_PASS,
    CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME,
    CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME,
    DRAMATURGY_STATUS_CONFIRMED,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_chapter_map_path,
    get_chapter_maps_manifest_path,
)
from otio_app.services.voiceover_generation.chapter_map_service import (
    build_chapter_map_prompt,
    display_chapter_number,
    generate_all_chapter_maps,
    generate_single_chapter_map,
    import_style_examples_from_folder,
    load_chapter_map_manifest,
)
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.project_brief_service import save_project_brief
from otio_app.services.voiceover_generation.models import ProjectBrief


def _make_project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    (root / "Antelope Canyon").mkdir(parents=True)
    (root / "Niagara Falls").mkdir(parents=True)
    work = root / "_otio"
    work.mkdir()
    return Project(
        id="chapter-map-project",
        name="Chapter Map Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        language="en",
        asset_subdir_names=["Antelope Canyon", "Niagara Falls"],
        selected_asset_subdirs=["Antelope Canyon", "Niagara Falls"],
    )


def _confirm_plan(project: Project) -> None:
    save_project_brief(
        project,
        ProjectBrief(project_id=project.id, language="EN", video_title="USA"),
    )
    plan = DramaturgyPlan(
        project_id=project.id,
        language="EN",
        status=DRAMATURGY_STATUS_CONFIRMED,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name="Antelope Canyon", order_index=1, enabled=True, dramaturgy_role="opener"
            ),
            DramaturgyFolderEntry(
                folder_name="Niagara Falls", order_index=2, enabled=True, dramaturgy_role="setup"
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)


def _write_style_examples(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), color=(240, 180, 40)).save(
        folder / CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME
    )
    Image.new("RGB", (1920, 1080), color=(230, 120, 30)).save(
        folder / CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME
    )


def _fake_generate_image(*, prompt, reference_image_paths, output_path, model=None):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), color=(40, 90, 160)).save(output_path)
    return 1920, 1080


def test_display_chapter_number_counts_down() -> None:
    assert display_chapter_number(order_index=1, total_chapters=37) == 37
    assert display_chapter_number(order_index=2, total_chapters=37) == 36
    assert display_chapter_number(order_index=37, total_chapters=37) == 1


def test_build_chapter_map_prompt_first_vs_followup() -> None:
    first = build_chapter_map_prompt(
        display_number=37,
        location_name="Antelope Canyon",
        previous_location_name=None,
        language="EN",
        is_first=True,
        total_chapters=37,
    )
    follow = build_chapter_map_prompt(
        display_number=36,
        location_name="Niagara Falls",
        previous_location_name="Antelope Canyon",
        language="EN",
        is_first=False,
        total_chapters=37,
    )
    assert '"37"' in first
    assert "Antelope Canyon" in first
    assert "16:9" in first
    assert "Northern Arizona" in first
    assert '"36"' in follow
    assert "Niagara Falls" in follow
    assert "Antelope Canyon" in follow
    assert "REMOVE" in follow
    assert "New York" in follow or "northeastern" in follow.lower()


def test_delete_chapter_map_removes_file(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.chapter_map_service import delete_chapter_map

    project = _make_project(tmp_path)
    _confirm_plan(project)
    examples = tmp_path / "Map_example"
    _write_style_examples(examples)
    import_style_examples_from_folder(project, examples)

    with patch(
        "otio_app.services.voiceover_generation.chapter_map_service.generate_chapter_map_image",
        side_effect=_fake_generate_image,
    ):
        generate_all_chapter_maps(project)

    path_1 = get_folder_chapter_map_path(
        project.project_root_path, folder_name="Antelope Canyon", order_index=1
    )
    assert path_1.is_file()
    delete_chapter_map(project, order_index=1, invalidate_following=True)
    assert not path_1.is_file()
    manifest = load_chapter_map_manifest(project)
    by_index = {entry.order_index: entry for entry in manifest.entries}
    assert by_index[1].status == CHAPTER_MAP_STATUS_MISSING
    assert by_index[2].status == CHAPTER_MAP_STATUS_MISSING


def test_import_style_examples_and_generate_bulk(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _confirm_plan(project)
    examples = tmp_path / "Map_example"
    _write_style_examples(examples)
    import_style_examples_from_folder(project, examples)

    with patch(
        "otio_app.services.voiceover_generation.chapter_map_service.generate_chapter_map_image",
        side_effect=_fake_generate_image,
    ):
        result = generate_all_chapter_maps(project)

    assert result.status == CHAPTER_MAP_STATUS_PASS
    assert result.generated == 2
    path_1 = get_folder_chapter_map_path(
        project.project_root_path, folder_name="Antelope Canyon", order_index=1
    )
    path_2 = get_folder_chapter_map_path(
        project.project_root_path, folder_name="Niagara Falls", order_index=2
    )
    assert path_1.is_file()
    assert path_2.is_file()
    assert get_chapter_maps_manifest_path(project.language_work_dir_path).is_file()
    manifest = load_chapter_map_manifest(project)
    assert [entry.order_index for entry in manifest.entries] == [1, 2]
    assert all(entry.status == CHAPTER_MAP_STATUS_PASS for entry in manifest.entries)


def test_single_chapter_requires_previous_map(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _confirm_plan(project)
    examples = tmp_path / "Map_example"
    _write_style_examples(examples)
    import_style_examples_from_folder(project, examples)

    with patch(
        "otio_app.services.voiceover_generation.chapter_map_service.generate_chapter_map_image",
        side_effect=_fake_generate_image,
    ):
        fail = generate_single_chapter_map(project, order_index=2)
        assert fail.status != CHAPTER_MAP_STATUS_PASS
        assert "Vorgänger" in (fail.error or "")

        ok1 = generate_single_chapter_map(project, order_index=1)
        assert ok1.status == CHAPTER_MAP_STATUS_PASS
        ok2 = generate_single_chapter_map(project, order_index=2)
        assert ok2.status == CHAPTER_MAP_STATUS_PASS


def test_regenerate_invalidates_following(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _confirm_plan(project)
    examples = tmp_path / "Map_example"
    _write_style_examples(examples)
    import_style_examples_from_folder(project, examples)

    with patch(
        "otio_app.services.voiceover_generation.chapter_map_service.generate_chapter_map_image",
        side_effect=_fake_generate_image,
    ):
        generate_all_chapter_maps(project)
        regenerate = generate_single_chapter_map(
            project, order_index=1, invalidate_following=True
        )

    assert regenerate.status == CHAPTER_MAP_STATUS_PASS
    manifest = load_chapter_map_manifest(project)
    by_index = {entry.order_index: entry for entry in manifest.entries}
    assert by_index[1].status == CHAPTER_MAP_STATUS_PASS
    assert by_index[2].status == CHAPTER_MAP_STATUS_MISSING
