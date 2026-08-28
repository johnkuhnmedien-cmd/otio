"""Tests für Projektordner-Layout."""

from __future__ import annotations

from pathlib import Path

from otio_app.project_layout import (
    classify_subdirectories,
    classify_subdirectories_no_voiceover,
    default_otio_export_basename,
    detect_voice_over_folder,
    diagnose_project_root,
    discover_asset_subdir_names,
    get_confirmed_voiceover_project_plan_path,
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_plan_draft_path,
    get_dramaturgy_settings_path,
    get_elevenlabs_settings_path,
    get_folder_inventory_path,
    get_folder_tts_runs_dir,
    get_folder_voiceover_settings_path,
    get_folder_voiceovers_confirmed_path,
    get_folder_voiceovers_draft_path,
    get_inventory_dir,
    get_inventory_path,
    get_intro_audio_dir,
    get_intro_hook_candidates_path,
    get_intro_hook_confirmed_path,
    get_llm_run_dir,
    get_llm_runs_dir,
    get_project_brief_path,
    get_folder_voiceover_audio_dir,
    get_tts_run_dir,
    get_voice_analysis_path,
    get_project_youtube_metadata_path,
    get_project_youtube_metadata_text_path,
    get_voice_over_dir,
    get_voiceover_audio_manifest_path,
    get_voiceover_generation_audio_dir,
    get_voiceover_generation_dir,
    get_voiceover_style_profile_path,
    get_voiceover_style_references_path,
    language_folder_name,
    resolve_otio_export_path,
    resolve_voice_over_folder_name,
    scan_project_structure,
    scan_project_structure_no_voiceover,
    safe_folder_slug,
)


def test_language_folder_name() -> None:
    assert language_folder_name("de") == "DE"
    assert language_folder_name("en") == "EN"
    assert language_folder_name("ja") == "JP"
    assert language_folder_name("JP") == "JP"
    assert language_folder_name("ko") == "KR"
    assert language_folder_name("kr") == "KR"


def test_get_voice_over_dir(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    voice_dir = get_voice_over_dir(project_root, "Voice over", "de")
    assert voice_dir == project_root / "Voice over" / "DE"


def test_project_youtube_metadata_path_uses_language_folder(
    temp_project_layout: dict[str, Path],
) -> None:
    project_root = temp_project_layout["project_root"]
    assert get_project_youtube_metadata_path(
        project_root, "Voice over", "pt"
    ) == project_root / "Voice over" / "PT" / "youtube_metadata.json"
    assert get_project_youtube_metadata_text_path(
        project_root, "Voice over", "ja"
    ) == project_root / "Voice over" / "JP" / "youtube_metadata.txt"


def test_output_paths(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = temp_project_layout["work_dir"]
    assert get_inventory_path(project_root).name == "inventory.json"
    assert get_inventory_dir(work_dir).name == "inventory"
    assert safe_folder_slug("Florida Keys") == "Florida_Keys"
    assert get_folder_inventory_path(work_dir, "Florida Keys").name == "Florida_Keys.json"
    assert get_voice_analysis_path(project_root).name == "voice_over_analysis.json"


def test_default_otio_export_basename_single_folder_uses_folder_name() -> None:
    assert (
        default_otio_export_basename(
            project_name="USA",
            folder_names=("Arches National Park",),
        )
        == "Arches_National_Park"
    )


def test_default_otio_export_basename_multiple_folders_uses_project_name() -> None:
    assert (
        default_otio_export_basename(
            project_name="USA Trip",
            folder_names=("Arches National Park", "Grand Canyon"),
        )
        == "USA_Trip"
    )


def test_default_otio_export_basename_appends_language() -> None:
    assert (
        default_otio_export_basename(
            project_name="USA",
            folder_names=("Arches National Park",),
            language="de",
        )
        == "Arches_National_Park_DE"
    )
    assert (
        default_otio_export_basename(
            project_name="USA Trip",
            folder_names=("Arches National Park", "Grand Canyon"),
            language="en",
        )
        == "USA_Trip_EN"
    )


def test_default_otio_export_basename_does_not_double_append_language() -> None:
    assert (
        default_otio_export_basename(
            project_name="USA_DE",
            folder_names=("A", "B"),
            language="de",
        )
        == "USA_DE"
    )


def test_resolve_otio_export_path_strips_otio_suffix(temp_project_layout: dict[str, Path]) -> None:
    work_dir = temp_project_layout["work_dir"]
    path = resolve_otio_export_path(work_dir, basename="Arches National Park.otio")
    assert path.name == "Arches_National_Park.otio"
    assert path.parent.name == "exports"


def test_discover_asset_subdir_names(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    names = discover_asset_subdir_names(project_root, work_dir, "Voice over")
    assert names == ["Grand Canyon", "Yellowstone"]


def test_resolve_voice_over_case_insensitive(temp_project_layout: dict[str, Path]) -> None:
    all_names = ["Grand Canyon", "voice over", "Yellowstone"]
    resolved = resolve_voice_over_folder_name(all_names, "Voice Over")
    assert resolved == "voice over"


def test_detect_voice_over_folder() -> None:
    names = ["Grand Canyon", "Voice Over", "Yellowstone"]
    assert detect_voice_over_folder(names) == "Voice Over"


def test_diagnose_project_root(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    diagnostic = diagnose_project_root(project_root)
    assert diagnostic.exists is True
    assert diagnostic.is_directory is True
    assert "Grand Canyon" in diagnostic.subdirectory_names
    assert "Voice over" in diagnostic.subdirectory_names


def test_scan_project_structure(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    scan = scan_project_structure(project_root, work_dir, "Voice over", "de")
    assert scan.ok
    assert scan.voice_over_folder_name == "Voice over"
    assert scan.voice_over_language_exists is True
    assert scan.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert scan.diagnostic is not None


def test_classify_excludes_selected_voice_over(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    all_names = ["Grand Canyon", "Voice over", "Yellowstone", "USA"]
    scan = classify_subdirectories(
        all_names,
        "Voice over",
        work_dir,
        project_root,
        "de",
    )
    assert "Voice over" not in scan.asset_subdir_names
    assert "USA" in scan.asset_subdir_names


# --- "Projekt ohne Voice-Over": Scan ohne Voice-over-Klassifikation ---


def test_classify_no_voiceover_treats_all_folders_as_assets(
    temp_project_layout: dict[str, Path],
) -> None:
    """Ohne Voice-Over gibt es keinen auszuschließenden Voice-over-Ordner —
    selbst ein Ordner, der wie "Voice over" heißt, bleibt ein Asset-Ordner."""
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    all_names = ["Grand Canyon", "Voice over", "Yellowstone"]
    scan = classify_subdirectories_no_voiceover(all_names, work_dir, project_root)
    assert scan.asset_subdir_names == ["Grand Canyon", "Voice over", "Yellowstone"]
    assert scan.voice_over_folder_name is None
    assert scan.voice_over_dir is None


def test_classify_no_voiceover_excludes_only_work_dir(
    temp_project_layout: dict[str, Path],
) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    all_names = ["Grand Canyon", "Yellowstone", "_otio"]
    scan = classify_subdirectories_no_voiceover(all_names, work_dir, project_root)
    assert scan.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert scan.system_folder_names == ["_otio"]


def test_scan_project_structure_no_voiceover(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    scan = scan_project_structure_no_voiceover(project_root, work_dir)
    assert scan.ok
    # "Voice over" existiert im Fixture-Projektordner, gilt hier aber als Asset-Ordner.
    assert set(scan.asset_subdir_names) == {"Grand Canyon", "Voice over", "Yellowstone"}
    assert scan.voice_over_folder_name is None


# --- Pfad-Helfer für "Projekt ohne Voice-Over" (Dramaturgie-/VO-Generierung) ---


def test_voiceover_generation_dir(temp_project_layout: dict[str, Path]) -> None:
    work_dir = temp_project_layout["work_dir"]
    assert get_voiceover_generation_dir(work_dir) == work_dir / "voiceover_generation"


def test_voiceover_generation_artifact_paths(temp_project_layout: dict[str, Path]) -> None:
    work_dir = temp_project_layout["work_dir"]
    base = work_dir / "voiceover_generation"
    assert get_project_brief_path(work_dir) == base / "project_brief.json"
    assert get_voiceover_style_references_path(work_dir) == base / "voiceover_style_references.json"
    assert get_voiceover_style_profile_path(work_dir) == base / "voiceover_style_profile.json"
    assert get_dramaturgy_plan_draft_path(work_dir) == base / "dramaturgy_plan.draft.json"
    assert get_dramaturgy_plan_confirmed_path(work_dir) == base / "dramaturgy_plan.confirmed.json"
    assert get_dramaturgy_settings_path(work_dir) == base / "dramaturgy_settings.json"
    assert get_folder_voiceover_settings_path(work_dir) == base / "folder_voiceover_settings.json"
    assert get_folder_voiceovers_draft_path(work_dir) == base / "folder_voiceovers.draft.json"
    assert get_folder_voiceovers_confirmed_path(work_dir) == base / "folder_voiceovers.confirmed.json"
    assert get_intro_hook_candidates_path(work_dir) == base / "intro_hook_candidates.json"
    assert get_intro_hook_confirmed_path(work_dir) == base / "intro_hook.confirmed.json"
    assert get_elevenlabs_settings_path(work_dir) == base / "elevenlabs_settings.json"
    assert get_voiceover_audio_manifest_path(work_dir) == base / "voiceover_audio_manifest.json"
    assert get_confirmed_voiceover_project_plan_path(work_dir) == (
        base / "confirmed_voiceover_project_plan.json"
    )


def test_voiceover_generation_audio_paths(temp_project_layout: dict[str, Path]) -> None:
    work_dir = temp_project_layout["work_dir"]
    audio_base = work_dir / "voiceover_generation" / "audio"
    assert get_voiceover_generation_audio_dir(work_dir) == audio_base
    assert get_intro_audio_dir(work_dir) == audio_base / "000_intro"
    assert get_folder_voiceover_audio_dir(work_dir, 1, "Grand Canyon") == (
        audio_base / "1_Grand_Canyon"
    )
    assert get_folder_tts_runs_dir(work_dir, 1, "Grand Canyon") == (
        audio_base / "1_Grand_Canyon" / "tts_runs"
    )
    assert get_tts_run_dir(work_dir, 1, "Grand Canyon", "run-abc") == (
        audio_base / "1_Grand_Canyon" / "tts_runs" / "run-abc"
    )


def test_llm_run_paths(temp_project_layout: dict[str, Path]) -> None:
    work_dir = temp_project_layout["work_dir"]
    llm_base = work_dir / "voiceover_generation" / "llm_runs"
    assert get_llm_runs_dir(work_dir) == llm_base
    assert get_llm_run_dir(work_dir, "run-xyz") == llm_base / "run-xyz"


def test_voiceover_generation_paths_are_isolated_from_edit_plan(
    temp_project_layout: dict[str, Path],
) -> None:
    """Der neue Artefaktbaum darf niemals unter _otio/edit_plan/ oder
    _otio/exports/ liegen — vollständige Trennung von der Produktionspipeline."""
    from otio_app.project_layout import get_edit_plan_dir, get_exports_dir

    work_dir = temp_project_layout["work_dir"]
    voiceover_gen_dir = get_voiceover_generation_dir(work_dir)
    edit_plan_dir = get_edit_plan_dir(work_dir)
    exports_dir = get_exports_dir(work_dir)

    assert voiceover_gen_dir != edit_plan_dir
    assert voiceover_gen_dir != exports_dir
    assert edit_plan_dir not in voiceover_gen_dir.parents
    assert voiceover_gen_dir not in edit_plan_dir.parents
