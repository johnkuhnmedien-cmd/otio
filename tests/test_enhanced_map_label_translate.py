"""LLM-Kartennamen in der Videosprache, ohne OSM-Müll."""

from __future__ import annotations

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.maps.label_translate_service import (
    apply_overlay_labels,
    build_map_label_translate_prompt,
    localize_map_plan_with_llm,
    overlay_label_is_plausible,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_MANUAL,
    RENDER_STATUS_DONE,
    MapCoordinateRecord,
    MapCoordinatesDocument,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    build_map_plan,
)
from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    overlay_label_is_plausible as remotion_plausible,
    remotion_payload,
)


def _project(tmp_path, folders: list[str], *, language: str = "it") -> Project:
    root = tmp_path / "Turkey"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        id="map-llm-labels",
        name="Turkey",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Türkei",
        asset_subdir_names=list(folders),
        selected_asset_subdirs=list(folders),
    )


def _confirm(project: Project, folders: list[str], *, language: str = "IT") -> None:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            language=language,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name=name, order_index=index, enabled=True
                )
                for index, name in enumerate(folders, start=1)
            ],
        ),
    )


def _coords(project: Project, folders: list[str]) -> MapCoordinatesDocument:
    places = {}
    for index, folder in enumerate(folders):
        places[folder] = MapCoordinateRecord(
            chapter_id=folder,
            original_label=folder,
            display_label="TURKEY MAN HELP WITH PAYMENT BY CARD. SOCKET",
            latitude=36.2 + index * 0.1,
            longitude=29.1 + index * 0.1,
            status=COORDINATE_STATUS_MANUAL,
            confidence=1.0,
        )
    return MapCoordinatesDocument(
        project_id=project.id, country="Türkei", places=places
    )


def test_overlay_label_rejects_payment_socket_garbage() -> None:
    original = "Kaş & Kekova"
    garbage = "Turkey man help with payment by card. Socket"
    assert overlay_label_is_plausible(original, original) is True
    assert remotion_plausible(original, garbage) is False
    assert overlay_label_is_plausible("Bohinjer See", "Lago di Bohinj") is True
    assert overlay_label_is_plausible("Vintgar-Klamm", "Gola di Vintgar") is True


def test_plan_ignores_osm_display_for_non_landmark_names(tmp_path) -> None:
    folders = ["Cappadocia & Göreme", "Kaş & Kekova"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    assert plan.maps[0].localized_display_label == "Cappadocia & Göreme"
    assert plan.maps[1].localized_display_label == "Kaş & Kekova"
    payload = remotion_payload(plan.maps[1])
    assert payload["from"]["label"] == "Cappadocia & Göreme"
    assert payload["to"]["label"] == "Kaş & Kekova"
    assert "SOCKET" not in payload["to"]["label"]


def test_llm_translates_folder_names_with_neighbor_context(tmp_path) -> None:
    folders = ["Cappadocia & Göreme", "Kaş & Kekova"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    captured: list[str] = []

    def fake_llm(prompt: str) -> str:
        captured.append(prompt)
        return (
            '{"places":['
            '{"id":"Cappadocia & Göreme","label":"Cappadocia e Göreme"},'
            '{"id":"Kaş & Kekova","label":"Kaş e Kekova"}'
            "]}"
        )

    localized = localize_map_plan_with_llm(project, plan, translate_fn=fake_llm)
    assert captured, "LLM sollte mit Kontext aufgerufen werden"
    prompt = captured[0]
    assert "Italian" in prompt
    assert "Turkey" in prompt or "Türkei" in prompt
    assert "Cappadocia & Göreme" in prompt
    assert "Kaş & Kekova" in prompt
    assert "previous" in prompt
    assert localized.maps[1].localized_display_label == "Kaş e Kekova"
    assert localized.maps[1].from_localized_display_label == "Cappadocia e Göreme"
    payload = remotion_payload(localized.maps[1])
    assert payload["from"]["label"] == "Cappadocia e Göreme"
    assert payload["to"]["label"] == "Kaş e Kekova"


def test_llm_garbage_falls_back_to_folder_name(tmp_path) -> None:
    folders = ["Kaş & Kekova"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))

    def fake_llm(_prompt: str) -> str:
        return (
            '{"places":[{"id":"Kaş & Kekova",'
            '"label":"Turkey man help with payment by card. Socket"}]}'
        )

    localized = localize_map_plan_with_llm(project, plan, translate_fn=fake_llm)
    assert localized.maps[0].localized_display_label == "Kaş & Kekova"


def test_cached_translation_skips_second_llm_call(tmp_path) -> None:
    folders = ["Kaş & Kekova"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    calls = {"n": 0}

    def fake_llm(_prompt: str) -> str:
        calls["n"] += 1
        return '{"places":[{"id":"Kaş & Kekova","label":"Kaş e Kekova"}]}'

    first = localize_map_plan_with_llm(project, plan, translate_fn=fake_llm)
    second = localize_map_plan_with_llm(project, first, translate_fn=fake_llm)
    assert calls["n"] == 1
    assert second.maps[0].localized_display_label == "Kaş e Kekova"


def test_new_overlay_label_invalidates_done_render(tmp_path) -> None:
    folders = ["Kaş & Kekova"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    plan.maps[0].render_status = RENDER_STATUS_DONE
    plan.maps[0].output_path = "/tmp/old.mp4"
    apply_overlay_labels(plan, {"Kaş & Kekova": "Kaş e Kekova"})
    assert plan.maps[0].localized_display_label == "Kaş e Kekova"
    assert plan.maps[0].render_status != RENDER_STATUS_DONE


def test_translate_prompt_lists_chapter_order() -> None:
    prompt = build_map_label_translate_prompt(
        language="IT",
        country="Türkei",
        rows=[
            {
                "id": "Kaş & Kekova",
                "name": "Kaş & Kekova",
                "previous": "Cappadocia & Göreme",
                "next": "",
            }
        ],
    )
    assert "1. Kaş & Kekova" in prompt
    assert "previous" in prompt
    assert "Never a sentence" in prompt
