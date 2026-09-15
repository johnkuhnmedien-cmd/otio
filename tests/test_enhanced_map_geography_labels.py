"""Nachbarländer- und Meer-Beschriftung auf Vintage-Karten."""

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
)
from otio_app.services.without_voiceover_enhanced.maps.geography_catalog import (
    country_fallback_label,
    geography_label_is_plausible,
    visible_geography,
)
from otio_app.services.without_voiceover_enhanced.maps.label_translate_service import (
    apply_geography_labels,
    build_map_geography_translate_prompt,
    localize_map_plan_with_llm,
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
    remotion_payload,
    view_bounds,
)


def _project(tmp_path: Path, folders: list[str], *, language: str = "de") -> Project:
    root = tmp_path / "Montenegro"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        id="map-geo-labels",
        name="Montenegro",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Montenegro",
        asset_subdir_names=list(folders),
        selected_asset_subdirs=list(folders),
    )


def _confirm(project: Project, folders: list[str], *, language: str = "DE") -> None:
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
    for folder in folders:
        places[folder] = MapCoordinateRecord(
            chapter_id=folder,
            original_label=folder,
            display_label=folder,
            latitude=42.39,
            longitude=18.85,
            status=COORDINATE_STATUS_MANUAL,
            confidence=1.0,
        )
    return MapCoordinatesDocument(
        project_id=project.id, country="Montenegro", places=places
    )


def test_montenegro_view_includes_croatia_albania_and_adriatic() -> None:
    bounds = view_bounds("499", 18.85, 42.39, 18.85, 42.39)
    entries = visible_geography(
        bounds,
        exclude_numeric="499",
        pin_longitude=18.85,
        pin_latitude=42.39,
    )
    ids = {entry.id for entry in entries}
    kinds = {entry.id: entry.kind for entry in entries}
    assert "country:croatia" in ids
    assert "country:albania" in ids
    assert "country:kosovo" in ids
    assert "sea:adriatic" in ids
    assert "country:montenegro" not in ids
    assert kinds["sea:adriatic"] == "sea"
    assert kinds["country:croatia"] == "country"


def test_kosovo_is_named_but_never_used_as_fill() -> None:
    bounds = view_bounds("499", 18.85, 42.39, 18.85, 42.39)
    kosovo = next(
        entry
        for entry in visible_geography(bounds, exclude_numeric="499")
        if entry.id == "country:kosovo"
    )
    assert kosovo.numeric_id == ""
    assert kosovo.atlas_name == "Kosovo"
    assert country_fallback_label("Kosovo", "de") == "Kosovo"
    assert country_fallback_label("Kosovo", "pl") == "Kosowo"
    assert country_fallback_label("North Macedonia", "de") == "Nordmazedonien"
    assert country_fallback_label("Serbia", "de") == "Serbien"


def test_geography_label_rejects_sentences() -> None:
    assert geography_label_is_plausible("Kroatien") is True
    assert geography_label_is_plausible("Adriatisches Meer") is True
    assert geography_label_is_plausible("Please help with payment.") is False


def test_payload_labels_neighbors_without_destination(tmp_path: Path) -> None:
    folders = ["Plava Špilja"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    payload = remotion_payload(plan.maps[0])
    ids = {item["id"] for item in payload["geographyLabels"]}
    labels = {item["id"]: item["label"] for item in payload["geographyLabels"]}
    assert payload["countryNumericId"] == "499"
    assert "country:croatia" in ids
    assert "country:albania" in ids
    assert "country:kosovo" in ids
    assert "sea:adriatic" in ids
    assert "country:montenegro" not in ids
    assert labels["country:croatia"] == "Kroatien"
    assert labels["country:albania"] == "Albanien"
    assert labels["country:kosovo"] == "Kosovo"
    assert labels["sea:adriatic"] == "Adriatisches Meer"
    kosovo = next(item for item in payload["geographyLabels"] if item["id"] == "country:kosovo")
    assert kosovo["atlasName"] == "Kosovo"
    assert kosovo["numericId"] == ""
    serbia = next(item for item in payload["geographyLabels"] if item["id"] == "country:serbia")
    assert serbia["label"] == "Serbien"


def test_llm_geography_prompt_asks_for_short_map_names() -> None:
    prompt = build_map_geography_translate_prompt(
        language="DE",
        country="Montenegro",
        rows=[
            {"id": "country:croatia", "name": "Croatia", "kind": "country"},
            {"id": "sea:adriatic", "name": "Adriatic Sea", "kind": "sea"},
        ],
    )
    assert "Croatia" in prompt
    assert "Adriatic Sea" in prompt
    assert "Do NOT output a label for that destination country" in prompt
    assert "Never OSM" in prompt


def test_llm_fills_neighbor_labels_and_invalidates_render(tmp_path: Path) -> None:
    folders = ["Plava Špilja"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    plan.maps[0].render_status = RENDER_STATUS_DONE
    plan.maps[0].output_path = "/tmp/old-map.mp4"

    def fake_llm(prompt: str) -> str:
        if "Adriatic Sea" in prompt or "Croatia" in prompt:
            return (
                '{"places":['
                '{"id":"country:croatia","label":"Kroatien"},'
                '{"id":"country:albania","label":"Albanien"},'
                '{"id":"sea:adriatic","label":"Adriatisches Meer"}'
                "]}"
            )
        return '{"places":[{"id":"Plava Špilja","label":"Plava Špilja"}]}'

    localized = localize_map_plan_with_llm(project, plan, translate_fn=fake_llm)
    ids = {item.id: item.label for item in localized.maps[0].geography_labels}
    assert ids.get("country:croatia") == "Kroatien"
    assert ids.get("sea:adriatic") == "Adriatisches Meer"
    assert localized.maps[0].render_status != RENDER_STATUS_DONE
    payload = remotion_payload(localized.maps[0])
    assert payload["styleVersion"] == "otio-vintage-map-v16"
    assert any(item["label"] == "Kroatien" for item in payload["geographyLabels"])


def test_apply_geography_uses_table_when_llm_returns_garbage(tmp_path: Path) -> None:
    folders = ["Plava Špilja"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))

    def fake_llm(_prompt: str) -> str:
        return '{"places":[{"id":"country:croatia","label":"Help with payment."}]}'

    localized = apply_geography_labels(project, plan, translate_fn=fake_llm)
    labels = {item.id: item.label for item in localized.maps[0].geography_labels}
    assert labels.get("country:croatia") == "Kroatien"


def test_renderer_draws_geography_labels() -> None:
    src = Path(
        "otio_app/services/without_voiceover_enhanced/maps/remotion_renderer/src/VintageMapTransition.tsx"
    ).read_text(encoding="utf-8")
    assert "geographyOnScreen" in src
    assert "geographyLabels" in src
    assert "interiorLonLat" in src
    assert "atlasFeatureForLabel" in src
    assert "geoBounds" in src
    assert "inlandPoints" in src
    assert "paddingLeft: 16" in src
    assert "whiteSpace: \"nowrap\"" in src
    assert "letterSpacing: 0" in src
    assert "borderRadius: 3" in src
    assert "translate(-50%, -50%)" in src
    assert "visibleCountryFit" not in src
    assert "fontSize: item.kind === \"sea\" ? 14.5 : 13" in src
