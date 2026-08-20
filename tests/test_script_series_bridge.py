"""Serie-Brücke am letzten Enhanced-Kapitel-Skript."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ProjectBrief,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    save_project_brief,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    _brief_text,
    generate_enhanced_script_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
)
from otio_app.services.without_voiceover_enhanced.script_series_bridge import (
    SeriesBridgeConfig,
    build_series_bridge_prompt_block,
    detect_series_bridge_cta_violations,
    series_bridge_from_brief,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folders = ["Budapest", "Holloko"]
    for folder in folders:
        (root / folder).mkdir()
    return Project(
        id="series-bridge",
        name="Ungarn",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        video_place="Ungarn",
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )


def _confirm(project: Project) -> None:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            project_title="Ungarn",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Budapest",
                    order_index=0,
                    enabled=True,
                    dramaturgy_role="hook",
                    reason="Hauptstadt",
                ),
                DramaturgyFolderEntry(
                    folder_name="Holloko",
                    order_index=1,
                    enabled=True,
                    dramaturgy_role="closing",
                    reason="UNESCO-Dorf",
                ),
            ],
        ),
    )


def _payload(folder_name: str, narration: str) -> str:
    slug = folder_name.lower().replace(" ", "_")
    return (
        "{"
        f'"narration_full": "{narration}",'
        f'"segments": [{{'
        f'"segment_id": "{slug}_segment_001",'
        f'"text": "{narration}",'
        f'"sequence_index": 1,'
        f'"semantic_function": "atmosphere",'
        f'"folder_name": "{folder_name}"'
        "}],"
        '"rhetoric_usage": [],'
        '"fact_check_hints": []'
        "}"
    )


def _bridge_brief(project: Project) -> ProjectBrief:
    return ProjectBrief(
        project_id=project.id,
        video_title="Ungarn",
        language="DE",
        series_bridge_enabled=True,
        series_bridge_destination="Griechenland",
        series_bridge_angle="Adria als Schwelle",
        series_bridge_hook_facts=(
            "Die Adria verbindet beide Küsten. In Griechenland stehen antike "
            "Theater noch im Alltag."
        ),
    )


def test_series_bridge_from_brief_requires_destination() -> None:
    assert series_bridge_from_brief(None) is None
    assert (
        series_bridge_from_brief(
            ProjectBrief(project_id="x", series_bridge_enabled=True)
        )
        is None
    )
    config = series_bridge_from_brief(
        ProjectBrief(
            project_id="x",
            series_bridge_enabled=True,
            series_bridge_destination="  Griechenland ",
        )
    )
    assert config is not None
    assert config.destination == "Griechenland"


def test_series_bridge_prompt_only_on_last_chapter() -> None:
    config = SeriesBridgeConfig(
        destination="Griechenland",
        hook_facts="Die Adria verbindet beide Küsten.",
        editorial_angle="gleiches Meer, anderes Licht",
    )
    last = build_series_bridge_prompt_block(
        config, this_place="Ungarn", is_last_chapter=True
    )
    earlier = build_series_bridge_prompt_block(
        config, this_place="Ungarn", is_last_chapter=False
    )
    assert earlier == ""
    assert "SERIES BRIDGE" in last
    assert "Griechenland" in last
    assert "Ungarn" in last
    assert "Adria" in last
    assert "schau dir das Video an" in last
    assert "chapter_link_usage.to_next stays false" in last


def test_folder_prompt_includes_series_bridge_block() -> None:
    block = build_series_bridge_prompt_block(
        SeriesBridgeConfig(destination="Griechenland", hook_facts="Adria."),
        this_place="Ungarn",
        is_last_chapter=True,
    )
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text="ctx",
        chapter_dramaturgy_text="meta",
        style_profile_text="Style",
        verified_facts_text="Facts",
        folder_name="Holloko",
        folder_slug="holloko",
        dramaturgy_role="closing",
        target_words=150,
        min_words=120,
        max_words=180,
        previous_folder_name="Budapest",
        next_folder_name=None,
        series_bridge_text=block,
        language="de",
    )
    assert "SERIES BRIDGE" in prompt
    assert "Griechenland" in prompt


def test_cta_detector_flags_watch_now_but_allows_documentary_hinge() -> None:
    bad = (
        "Hollókő bleibt im Holz. Schau dir jetzt mein letztes Video über "
        "Griechenland an."
    )
    good = (
        "Hollókő bleibt im Holz. Jenseits der Adria wartet Griechenland, "
        "wo antike Theater noch im Alltag stehen."
    )
    canal = (
        "Der Kanal von Korinth trennt die Peloponnes vom Festland und "
        "zwingt Schiffe durch einen Schnitt im Stein."
    )
    assert detect_series_bridge_cta_violations(bad)
    assert not detect_series_bridge_cta_violations(good)
    assert not detect_series_bridge_cta_violations(canal)


def test_brief_text_omits_series_bridge_fields(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_project_brief(project, _bridge_brief(project))
    text = _brief_text(project)
    assert "series_bridge" not in text
    assert "Griechenland" not in text
    assert "Adria" not in text


def test_last_chapter_prompt_gets_bridge_first_does_not(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _confirm(project)
    save_project_brief(project, _bridge_brief(project))
    captured: dict[str, str] = {}

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        for folder in ("Budapest", "Holloko"):
            if f"folder_name (EXACT): {folder}" in prompt:
                captured[folder] = prompt
                return _payload(folder, f"Narration für {folder}.")
        raise AssertionError("unexpected prompt")

    first = generate_enhanced_script_for_folder(
        project, "Budapest", llm_callable=fake_llm
    )
    last = generate_enhanced_script_for_folder(
        project, "Holloko", llm_callable=fake_llm
    )
    assert first.status == "PASS"
    assert last.status == "PASS"
    assert "SERIES BRIDGE" not in captured["Budapest"]
    assert "Griechenland" not in captured["Budapest"]
    assert "SERIES BRIDGE" in captured["Holloko"]
    assert "Griechenland" in captured["Holloko"]
    assert "Adria" in captured["Holloko"]
    assert "Ungarn" in captured["Holloko"]


def test_last_chapter_repairs_youtube_cta(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _confirm(project)
    save_project_brief(project, _bridge_brief(project))
    calls: list[str] = []

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        calls.append(prompt)
        if len(calls) == 1:
            return _payload(
                "Holloko",
                "Hollókő bleibt im Holz. Schau dir jetzt mein letztes Video "
                "über Griechenland an.",
            )
        return _payload(
            "Holloko",
            "Hollókő bleibt im Holz. Jenseits der Adria wartet Griechenland, "
            "wo antike Theater noch im Alltag stehen.",
        )

    result = generate_enhanced_script_for_folder(
        project, "Holloko", llm_callable=fake_llm
    )
    assert result.status == "PASS"
    assert len(calls) == 2
    assert "SERIES BRIDGE REPAIR REQUIRED" in calls[1]
    assert "YouTube" in calls[1]
    assert result.document is not None
    assert "letztes Video" not in result.document.narration_full
    assert "Griechenland" in result.document.narration_full
