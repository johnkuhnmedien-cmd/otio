"""Zweiter Enhanced-Skriptmodus: Slim Inventory als Motiv-Palette."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.script_asset_palette import (
    cluster_slim_assets,
    folder_has_visual_palette,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    generate_enhanced_script_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_options import (
    SCRIPT_MODE_ASSET_GROUNDED,
    SCRIPT_MODE_RESEARCH,
    ScriptOptions,
    is_asset_grounded_script_mode,
    load_script_options,
    save_script_options,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    ASSET_GROUNDED_SCRIPT_RULES,
    build_enhanced_folder_script_prompt,
)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder_names = folders or ["Bleder See"]
    for folder in folder_names:
        (root / folder).mkdir(exist_ok=True)
    return Project(
        name="Asset Grounded Script",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=folder_names,
        selected_asset_subdirs=folder_names,
        fps=25.0,
    )


def _confirm_dramaturgy(project: Project, folders: list[str]) -> DramaturgyPlan:
    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Slowenien",
        core_promise="Geschichte und Eigenart",
        narrative_arc="Hook → Entwicklung",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder,
                order_index=index,
                enabled=True,
                dramaturgy_role="hook" if index == 0 else "development",
                reason=f"Kapitel {folder}",
                recommended_word_count=170,
                recommended_min_words=140,
                recommended_max_words=200,
            )
            for index, folder in enumerate(folders)
        ],
    )
    return save_confirmed_dramaturgy(project, plan)


def _fake_folder_llm_response(folder_name: str) -> str:
    slug = folder_name.lower().replace(" ", "_")
    return (
        "{"
        f'"narration_full": "Narration für {folder_name}.",'
        f'"segments": [{{'
        f'"segment_id": "{slug}_segment_001",'
        f'"text": "Narration für {folder_name}.",'
        f'"sequence_index": 1,'
        f'"semantic_function": "history",'
        f'"fact_check_required": false,'
        f'"folder_name": "{folder_name}"'
        "}],"
        '"rhetoric_usage": [],'
        '"visual_intents": [],'
        '"visual_beats": [],'
        '"coverage_needs": [],'
        '"fact_check_hints": []'
        "}"
    )


def _write_slim(project: Project, folder_name: str, assets: list[dict]) -> Path:
    canonical = get_folder_inventory_path(project.work_dir_path, folder_name)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    slim_path = slim_inventory_path_for(canonical)
    payload = {
        "schema_version": "asset-slim-v2",
        "chapter": folder_name,
        "assets": assets,
    }
    slim_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return slim_path


def _asset(
    index: int,
    caption: str,
    *,
    tags: list[str],
    media: str = "video",
    people_action: str = "",
) -> dict:
    row: dict = {
        "id": f"asset_bleder_see_asset{index:05d}",
        "file": f"Bleder See_Asset{index:05d}.mov",
        "type": media,
        "caption": caption,
        "tags": tags,
    }
    if people_action:
        row["people"] = True
        row["people_action"] = people_action
    return row


BLED_ASSETS = [
    _asset(
        1,
        "Luftaufnahme des Bleder Sees mit der Burg Bled im Vordergrund und der Inselkirche im Hintergrund.",
        tags=["Bleder See", "Burg Bled", "Marienkirche", "See", "Burg", "Insel"],
    ),
    _asset(
        3,
        "Weite Luftaufnahme des türkisfarbenen Bleder Sees mit der zentralen Kircheninsel vor bewaldeter Bergkulisse.",
        tags=["Bleder See", "Bleder Insel", "Marienkirche", "See", "Insel", "Luftaufnahme"],
    ),
    _asset(
        4,
        "Luftaufnahme des türkisfarbenen Bleder Sees mit der zentralen Kircheninsel vor bewaldeten Berghängen.",
        tags=["Bleder See", "Insel", "Marienkirche", "Luftaufnahme", "Slowenien", "Alpen"],
    ),
    _asset(
        6,
        "Luftaufnahme der Marienkirche auf der Insel im Bleder See vor bewaldeten Bergen und blauem Himmel.",
        tags=["Bleder See", "Marienkirche", "Slowenien", "Insel", "Luftaufnahme", "See"],
    ),
    _asset(
        10,
        "Luftaufnahme der Burg von Bled auf einer Klippe am Bleder See mit schneebedeckten Bergen im Hintergrund.",
        tags=["Bleder See", "Burg von Bled", "Slowenien", "Luftaufnahme", "Berge", "Schnee"],
    ),
    _asset(
        11,
        "Luftaufnahme der Burg Bled auf einem Felsen mit Blick auf den Ort und die schneebedeckten Alpen im Hintergrund.",
        tags=["Burg Bled", "Bled", "Burg", "Luftaufnahme", "Alpen", "Slowenien"],
    ),
    _asset(
        12,
        "Luftaufnahme der geschichtsträchtigen Burg Bled auf einer bewaldeten Klippe über dem Bleder See.",
        tags=["Burg Bled", "Bleder See", "Burg", "Klippe", "Luftaufnahme", "Wald"],
    ),
    _asset(
        14,
        "Traditionelle Pletna-Boote am Ufer des Bleder Sees vor bewaldeten Bergen.",
        tags=["Bleder See", "Pletna", "Boot", "See", "Slowenien", "Holzsteg"],
        media="photo",
    ),
    _asset(
        15,
        "Traditionelle Pletna-Boote an einem Holzsteg am Bleder See vor einer bergigen Kulisse.",
        tags=["Bleder See", "Pletna", "Boot", "Holzsteg", "See", "Slowenien"],
        media="photo",
    ),
    _asset(
        18,
        "Ein traditionelles Pletna-Boot liegt an einem Holzsteg am Ufer des Bleder Sees vor der Kulisse der St.-Martins-Kirche.",
        tags=["Bleder See", "Pletna", "Boot", "Holzsteg", "St.-Martins-Kirche", "See"],
        media="photo",
    ),
]


def _prompt_kwargs(**overrides) -> dict:
    payload = dict(
        project_brief_text="Brief",
        film_context_text="core_promise: promise",
        chapter_dramaturgy_text="folder_name: Bleder See\ndramaturgy_role: hook",
        style_profile_text="Style",
        verified_facts_text="Facts",
        folder_name="Bleder See",
        folder_slug="Bleder_See",
        dramaturgy_role="hook",
        target_words=170,
        min_words=140,
        max_words=200,
        previous_folder_name=None,
        next_folder_name="Vintgar-Klamm",
        language="de",
    )
    payload.update(overrides)
    return payload


def test_research_prompt_has_no_visual_palette() -> None:
    prompt = build_enhanced_folder_script_prompt(**_prompt_kwargs())
    assert "CHAPTER VISUAL PALETTE" not in prompt
    assert "ASSET-GROUNDED SCRIPT MODE" not in prompt
    assert "LOCAL ASSETS" not in prompt


def test_asset_grounded_prompt_has_no_visual_quota() -> None:
    palette = (
        "CHAPTER VISUAL PALETTE (this folder only — palette, not a shot list)\n\n"
        "- (3 files, 3 video) Burg Bled on a cliff above the lake"
    )
    prompt = build_enhanced_folder_script_prompt(
        **_prompt_kwargs(chapter_visual_palette_text=palette)
    )
    assert "ASSET-GROUNDED SCRIPT MODE" in prompt
    assert ASSET_GROUNDED_SCRIPT_RULES in prompt
    assert "CHAPTER VISUAL PALETTE" in prompt
    assert "Burg Bled on a cliff" in prompt
    assert "no minimum and no maximum" in prompt
    assert "0–3" not in prompt
    assert "0-3" not in prompt
    assert "even when no matching asset exists" in prompt
    assert "Do not drop an important fact" in prompt
    assert "THIS CHAPTER DRAMATURGY:" in prompt
    assert "SILENT EDITORIAL METADATA" in prompt or "dramaturgy_role" in prompt


def test_cluster_slim_assets_merges_duplicates_not_subjects() -> None:
    clusters = cluster_slim_assets(BLED_ASSETS, chapter_name="Bleder See")
    joined = " | ".join(item.representative_caption.lower() for item in clusters)
    assert len(clusters) >= 3
    assert len(clusters) < len(BLED_ASSETS)
    pletna = [item for item in clusters if "pletna" in item.representative_caption.lower()]
    castle = [
        item
        for item in clusters
        if "burg" in item.representative_caption.lower()
        and "pletna" not in item.representative_caption.lower()
        and "inselkirche" not in item.representative_caption.lower()
        and "kircheninsel" not in item.representative_caption.lower()
    ]
    church = [
        item
        for item in clusters
        if (
            "marienkirche" in item.representative_caption.lower()
            or "kircheninsel" in item.representative_caption.lower()
        )
        and "burg" not in item.representative_caption.lower()
        and "pletna" not in item.representative_caption.lower()
    ]
    assert pletna, joined
    assert castle, joined
    assert church, joined
    assert pletna[0].count >= 2
    assert castle[0].count >= 2
    assert church[0].count >= 2
    assert all("luftaufnahme" not in item.representative_caption.lower()[:20] for item in clusters)


def test_script_options_persist(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert load_script_options(project).script_mode == SCRIPT_MODE_RESEARCH
    saved = save_script_options(
        project, ScriptOptions(script_mode=SCRIPT_MODE_ASSET_GROUNDED)
    )
    assert saved.script_mode == SCRIPT_MODE_ASSET_GROUNDED
    assert load_script_options(project).script_mode == SCRIPT_MODE_ASSET_GROUNDED
    assert is_asset_grounded_script_mode(load_script_options(project).script_mode)


def test_generate_asset_grounded_fails_without_slim(tmp_path: Path) -> None:
    project = _project(tmp_path, folders=["Bleder See"])
    _confirm_dramaturgy(project, ["Bleder See"])
    called = False

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del prompt, model, max_output_tokens
        nonlocal called
        called = True
        return _fake_folder_llm_response("Bleder See")

    result = generate_enhanced_script_for_folder(
        project,
        "Bleder See",
        llm_callable=fake_llm,
        script_mode=SCRIPT_MODE_ASSET_GROUNDED,
    )
    assert result.status == "FAIL"
    assert "Slim-Inventar" in (result.error or "")
    assert called is False
    assert folder_has_visual_palette(project, "Bleder See") is False


def test_generate_asset_grounded_sends_palette_not_file_dump(tmp_path: Path) -> None:
    project = _project(tmp_path, folders=["Bleder See"])
    _confirm_dramaturgy(project, ["Bleder See"])
    _write_slim(project, "Bleder See", BLED_ASSETS)
    captured: list[str] = []

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        captured.append(prompt)
        return _fake_folder_llm_response("Bleder See")

    result = generate_enhanced_script_for_folder(
        project,
        "Bleder See",
        llm_callable=fake_llm,
        script_mode=SCRIPT_MODE_ASSET_GROUNDED,
    )
    assert result.status == "PASS", result.error
    assert len(captured) == 1
    prompt = captured[0]
    assert "ASSET-GROUNDED SCRIPT MODE" in prompt
    assert "CHAPTER VISUAL PALETTE" in prompt
    assert "Pletna" in prompt
    assert "asset_bleder_see_asset00001" not in prompt
    assert "Bleder See_Asset00001.mov" not in prompt
    assert "0–3" not in prompt
    assert "even when no matching asset exists" in prompt
    assert folder_has_visual_palette(project, "Bleder See") is True


def test_generate_research_mode_ignores_slim_file(tmp_path: Path) -> None:
    project = _project(tmp_path, folders=["Bleder See"])
    _confirm_dramaturgy(project, ["Bleder See"])
    _write_slim(project, "Bleder See", BLED_ASSETS)
    captured: list[str] = []

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        captured.append(prompt)
        return _fake_folder_llm_response("Bleder See")

    result = generate_enhanced_script_for_folder(
        project,
        "Bleder See",
        llm_callable=fake_llm,
        script_mode=SCRIPT_MODE_RESEARCH,
    )
    assert result.status == "PASS", result.error
    prompt = captured[0]
    assert "CHAPTER VISUAL PALETTE" not in prompt
    assert "ASSET-GROUNDED SCRIPT MODE" not in prompt


def test_generate_reads_saved_script_mode(tmp_path: Path) -> None:
    project = _project(tmp_path, folders=["Bleder See"])
    _confirm_dramaturgy(project, ["Bleder See"])
    _write_slim(project, "Bleder See", BLED_ASSETS)
    save_script_options(
        project, ScriptOptions(script_mode=SCRIPT_MODE_ASSET_GROUNDED)
    )
    captured: list[str] = []

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        captured.append(prompt)
        return _fake_folder_llm_response("Bleder See")

    result = generate_enhanced_script_for_folder(
        project, "Bleder See", llm_callable=fake_llm
    )
    assert result.status == "PASS", result.error
    assert "CHAPTER VISUAL PALETTE" in captured[0]
    assert "ASSET-GROUNDED SCRIPT MODE" in captured[0]
