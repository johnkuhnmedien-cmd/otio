"""Nominatim-Drosselung, Cache und Fortschritt für Karten-Koordinaten."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.maps import geocode_service as geocode_mod
from otio_app.services.without_voiceover_enhanced.maps.geo_scope import (
    coordinates_disagree,
    coordinates_in_scope,
    pin_display_label,
    resolve_geocode_scope,
)
from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    NOMINATIM_USER_AGENT,
    LlmPlaceSuggestion,
    build_geocode_coordinate_prompt,
    build_geocode_rewrite_prompt,
    friendly_geocode_error,
    geocode_query_variants,
    lookup_missing_coordinates,
    nominatim_geocode,
    reset_nominatim_client_for_tests,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_CONFIRMED,
    COORDINATE_STATUS_MANUAL,
    COORDINATE_STATUS_RESOLVED,
    MapCoordinateRecord,
    MapCoordinatesDocument,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    build_map_plan,
    confirm_all_valid_map_coordinates,
    save_map_coordinates,
    update_coordinate_record,
)


@pytest.fixture(autouse=True)
def _reset_nominatim_client() -> None:
    reset_nominatim_client_for_tests()
    yield
    reset_nominatim_client_for_tests()


def _project(tmp_path: Path, folders: list[str]) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        name="Geocode Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="fr",
        video_place="Greece",
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
        fps=25.0,
    )


def _confirm(project: Project, folders: list[str]) -> None:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            language="FR",
            project_title="Map Test",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name=folder,
                    order_index=index,
                    enabled=True,
                )
                for index, folder in enumerate(folders)
            ],
        ),
    )


class _JsonResp:
    def __init__(self, payload: list[dict], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self) -> list[dict]:
        return self._payload


def _ok_resp(lat: str = "39.72", lon: str = "21.63") -> _JsonResp:
    return _JsonResp([{"lat": lat, "lon": lon, "importance": 0.9}])


def test_nominatim_sends_identifying_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_get(*_args, **kwargs):
        captured.update(kwargs["headers"])
        return _ok_resp()

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    hit = nominatim_geocode("Meteora", "Greece")
    assert hit["latitude"] == 39.72
    assert captured["User-Agent"] == NOMINATIM_USER_AGENT
    assert "OTIO-Schnittplaner" in captured["User-Agent"]
    assert "maps-geocode" in captured["User-Agent"]
    assert "python-requests" not in captured["User-Agent"]


def test_nominatim_waits_at_least_one_second_between_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    clock = {"t": 50.0}

    monkeypatch.setattr(geocode_mod, "_monotonic", lambda: clock["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(geocode_mod, "_sleep", fake_sleep)
    monkeypatch.setattr(geocode_mod.requests, "get", lambda *_a, **_k: _ok_resp())

    nominatim_geocode("Meteora", "Greece")
    nominatim_geocode("Delphi", "Greece")
    assert sleeps
    assert sleeps[0] == pytest.approx(1.0)


def test_nominatim_retries_429_with_increasing_pauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    clock = {"t": 80.0}
    calls = {"n": 0}

    def fake_get(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            resp = _JsonResp([], status_code=429)
            resp.headers = {}
            return resp
        return _ok_resp()

    monkeypatch.setattr(geocode_mod, "_monotonic", lambda: clock["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(geocode_mod, "_sleep", fake_sleep)
    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    hit = nominatim_geocode("Meteora", "Greece")
    assert hit["latitude"] == 39.72
    assert calls["n"] == 3
    retries = [pause for pause in sleeps if pause >= 1.0]
    assert retries[0] == pytest.approx(1.0)
    assert retries[1] >= retries[0]


def test_lookup_skips_found_places_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    gets: list[str] = []

    def fake_get(*_args, **kwargs):
        gets.append(str(kwargs.get("params", {}).get("q") or ""))
        return _ok_resp()

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    cache = tmp_path / "nominatim.json"
    plan = build_map_plan(project)
    lookup_missing_coordinates(project, plan=plan, cache_path=cache)
    assert gets == ["Meteora, Greece"]

    save_map_coordinates(
        project,
        MapCoordinatesDocument(project_id=project.id, country="Greece", places={}),
    )
    plan = build_map_plan(project)
    lookup_missing_coordinates(
        project,
        plan=plan,
        coordinates=MapCoordinatesDocument(project_id=project.id, country="Greece"),
        cache_path=cache,
    )
    assert gets == ["Meteora, Greece"]


def test_lookup_continues_after_one_place_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["Broken Isle", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    order: list[str] = []

    def fake_get(*_args, **kwargs):
        query = str(kwargs.get("params", {}).get("q") or "")
        order.append(query)
        if "Broken" in query:
            raise requests.ConnectionError(
                "dns boom https://nominatim.openstreetmap.org/search"
            )
        return _ok_resp()

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    messages: list[str] = []
    plan = build_map_plan(project)
    coords, rebuilt, errors = lookup_missing_coordinates(
        project,
        plan=plan,
        cache_path=tmp_path / "cache.json",
        on_progress=lambda event: messages.append(event.message),
    )
    assert "Broken Isle, Greece" in order
    assert "Meteora, Greece" in order
    assert coords.places["Meteora"].has_coordinates is True
    assert rebuilt.maps[1].end_latitude == 39.72
    assert len(errors) == 1
    assert errors[0].startswith("Broken Isle:")
    blob = "\n".join(errors + messages)
    assert "https://" not in blob
    assert "dns boom" not in blob
    assert "ConnectionError" not in blob
    assert any("Meteora: gefunden" in line for line in messages)
    assert any("Broken Isle" in line and "Suche fehlgeschlagen" in line for line in messages)


def test_lookup_exhausted_429_does_not_abort_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["Hydra", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)

    def fake_get(*_args, **kwargs):
        query = str(kwargs.get("params", {}).get("q") or "")
        if "Hydra" in query:
            resp = _JsonResp([], status_code=429)
            resp.headers = {"Retry-After": "1"}
            return resp
        return _ok_resp("40.15", "24.32")

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    coords, _rebuilt, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
    )
    assert coords.places["Meteora"].has_coordinates is True
    assert "Hydra" in errors[0]
    assert "ausgelastet" in errors[0]
    assert "https://" not in errors[0]


def test_friendly_geocode_error_hides_json_parse_noise() -> None:
    exc = ValueError(
        "JSON-Parse fehlgeschlagen: Expecting property name enclosed in double "
        "quotes: line 15 column 5 (char 747) https://example.test/search"
    )
    text = friendly_geocode_error("Naxos", exc)
    assert text == "Suche fehlgeschlagen"
    assert "Expecting property" not in text
    assert "https://" not in text


def test_lookup_skips_chapter_that_already_has_coordinates(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    update_coordinate_record(
        project,
        chapter_id="Mount Athos",
        original_label="Mount Athos",
        display_label="Mont Athos",
        latitude=40.27,
        longitude=24.21,
        status=COORDINATE_STATUS_MANUAL,
    )
    calls: list[str] = []

    def fake_geocode(place: str, country: str) -> dict:
        calls.append(place)
        return {
            "latitude": 39.72,
            "longitude": 21.63,
            "confidence": 0.9,
            "original_label": place,
            "display_label": place,
        }

    lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        geocode_fn=fake_geocode,
        cache_path=tmp_path / "unused.json",
    )
    assert calls == ["Meteora"]


def test_geocode_query_variants_prefer_english_country() -> None:
    variants = geocode_query_variants("Baradla Cave", "Ungarn")
    assert variants[0] == "Baradla Cave, Hungary"
    assert "Baradla Cave, Ungarn" in variants
    assert "Baradla Cave" in variants
    numbered = geocode_query_variants("3: The Bat Colony", "Ungarn")
    assert "The Bat Colony, Hungary" in numbered
    assert "Bat Colony, Hungary" in numbered


def test_nominatim_retries_next_variant_when_first_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    def fake_get(*_args, **kwargs):
        query = str(kwargs.get("params", {}).get("q") or "")
        queries.append(query)
        if query == "Mystery Place, Greece":
            return _JsonResp([])
        return _ok_resp()

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    hit = nominatim_geocode("Mystery Place", "Greece")
    assert hit["latitude"] == 39.72
    assert queries[0] == "Mystery Place, Greece"
    assert "Mystery Place" in queries


def test_nominatim_sends_countrycodes_for_known_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get(*_args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return _ok_resp()

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    nominatim_geocode("Baradla Cave", "Ungarn")
    assert captured.get("q") == "Baradla Cave, Hungary"
    assert captured.get("countrycodes") == "hu"


def test_lookup_uses_photon_when_nominatim_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["Baradla Cave"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Ungarn"})
    _confirm(project, folders)

    class _PhotonResp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "features": [
                    {
                        "geometry": {"coordinates": [20.61, 48.47]},
                        "properties": {"name": "Baradla", "countrycode": "HU"},
                    }
                ]
            }

    def fake_get(url, *_args, **_kwargs):
        if "photon" in str(url):
            return _PhotonResp()
        if "wikipedia" in str(url):
            return _JsonResp([])
        return _JsonResp([])

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
    )
    assert errors == []
    assert coords.places["Baradla Cave"].latitude == pytest.approx(48.47)
    assert coords.places["Baradla Cave"].source == "photon"


def test_lookup_llm_rewrite_retries_nominatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["The Bat Colony"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Ungarn"})
    _confirm(project, folders)

    def fake_get(url, *_args, **kwargs):
        query = str((kwargs.get("params") or {}).get("q") or "")
        if "nominatim" in str(url) and "Aggtelek" in query:
            return _ok_resp("48.47", "20.63")
        return _JsonResp([])

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        llm_rewrite=True,
        rewrite_queries_fn=lambda _rows, _country: {
            "The Bat Colony": "Aggtelek Karst"
        },
    )
    assert errors == []
    assert coords.places["The Bat Colony"].has_coordinates is True
    assert coords.places["The Bat Colony"].latitude == pytest.approx(48.47)


def test_europa_is_continent_scope_not_nominatim_suffix() -> None:
    scope = resolve_geocode_scope("Europa")
    assert scope.kind == "continent"
    assert scope.query_suffix == ""
    assert "es" in scope.countrycodes.split(",")
    assert geocode_query_variants("Granadilla", "Europa") == ["Granadilla"]
    assert "Granadilla, Europa" not in geocode_query_variants("Granadilla", "Europa")
    extremadura = resolve_geocode_scope("Extremadura")
    assert extremadura.iso2 == "es"
    assert extremadura.query_suffix == "Spain"


def test_pin_display_label_keeps_chapter_when_osm_is_unrelated() -> None:
    assert pin_display_label("Granadilla", "Urb. La Europa") == "Granadilla"
    assert pin_display_label("Granadilla", "Granadilla") == "Granadilla"
    assert pin_display_label("Meteora", "Meteora Monasteries") == "Meteora Monasteries"


def test_nominatim_prefers_spain_granadilla_when_video_is_europe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(*_args, **kwargs):
        params = kwargs.get("params") or {}
        assert "es" in str(params.get("countrycodes") or "")
        assert "Granadilla, Europa" not in str(params.get("q") or "")
        return _JsonResp(
            [
                {
                    "lat": "9.93",
                    "lon": "-84.01",
                    "importance": 0.36,
                    "name": "Urb. La Europa",
                    "address": {"country_code": "cr", "country": "Costa Rica"},
                },
                {
                    "lat": "40.268",
                    "lon": "-6.109",
                    "importance": 0.29,
                    "name": "Granadilla",
                    "address": {"country_code": "es", "country": "España"},
                },
            ]
        )

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    hit = nominatim_geocode("Granadilla", "Europa")
    assert hit["latitude"] == pytest.approx(40.268)
    assert hit["longitude"] == pytest.approx(-6.109)
    assert hit["display_label"] == "Granadilla"
    assert hit.get("country_code") == "es"


def test_lookup_rechecks_out_of_region_coordinates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folders = ["Granadilla"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Granadilla": MapCoordinateRecord(
                    chapter_id="Granadilla",
                    original_label="Granadilla",
                    display_label="Urb. La Europa",
                    latitude=9.93,
                    longitude=-84.01,
                    confidence=0.9,
                    status=COORDINATE_STATUS_RESOLVED,
                    source="nominatim",
                    country_context="Europa",
                )
            },
        ),
    )

    def fake_get(*_args, **_kwargs):
        return _JsonResp(
            [
                {
                    "lat": "40.268",
                    "lon": "-6.109",
                    "importance": 0.8,
                    "name": "Granadilla",
                    "address": {"country_code": "es", "country": "España"},
                }
            ]
        )

    monkeypatch.setattr(geocode_mod.requests, "get", fake_get)
    monkeypatch.setattr(geocode_mod, "_sleep", lambda _seconds: None)
    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
    )
    assert errors == []
    assert coords.places["Granadilla"].latitude == pytest.approx(40.268)
    assert coords.places["Granadilla"].display_label == "Granadilla"


def test_geocode_rewrite_prompt_includes_video_geography(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Granadilla"])
    project = project.model_copy(update={"name": "verlassene Dörfer Europas"})
    prompt = build_geocode_rewrite_prompt(
        project,
        "Europa",
        [{"id": "Granadilla", "title": "Granadilla", "narration": "…"}],
    )
    assert "Europe" in prompt
    assert "verlassene Dörfer Europas" in prompt
    assert "Granadilla" in prompt
    assert "Costa Rica" in prompt
    assert "do not write ', Europe'" in prompt.lower() or ", Europa" in prompt


def test_confirm_all_skips_coordinates_outside_region(tmp_path: Path) -> None:
    folders = ["Granadilla"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Granadilla": MapCoordinateRecord(
                    chapter_id="Granadilla",
                    original_label="Granadilla",
                    display_label="Urb. La Europa",
                    latitude=9.93,
                    longitude=-84.01,
                    confidence=0.9,
                    status=COORDINATE_STATUS_RESOLVED,
                    source="nominatim",
                    country_context="Europa",
                )
            },
        ),
    )
    coords, _plan = confirm_all_valid_map_coordinates(project)
    assert coords.places["Granadilla"].status == COORDINATE_STATUS_RESOLVED
    assert coords.places["Granadilla"].latitude == pytest.approx(9.93)


def test_europe_scope_includes_caucasus_and_russia() -> None:
    scope = resolve_geocode_scope("Europa")
    assert "ru" in scope.countrycodes.split(",")
    assert "ge" in scope.countrycodes.split(",")
    assert coordinates_in_scope(41.82, 47.58, scope) is True
    assert coordinates_in_scope(64.0, 25.5, scope) is True
    russia = resolve_geocode_scope("Russland")
    assert russia.iso2 == "ru"
    assert russia.query_suffix == "Russia"
    assert coordinates_disagree(64.0, 25.5, 41.82, 47.58) is True
    assert coordinates_disagree(41.82, 47.58, 41.90, 47.60) is False


def test_geocode_coordinate_prompt_uses_folder_script(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Amuzgi"])
    project = project.model_copy(update={"name": "verlassene Dörfer Europas"})
    prompt = build_geocode_coordinate_prompt(
        project,
        "Europa",
        [
            {
                "id": "Amuzgi",
                "title": "Amuzgi",
                "narration": "Im Nordkaukasus liegt das verlassene Dorf Amuzgi in Dagestan.",
            }
        ],
    )
    assert "verlassene Dörfer Europas" in prompt
    assert "Nordkaukasus" in prompt
    assert "Amuzgi" in prompt
    assert "Finland" in prompt
    assert "Ropoto" in prompt
    assert "Greece" in prompt
    assert "Germany" in prompt
    assert "next larger city" in prompt
    assert "fallback" in prompt
    assert "do not write ', Europe'" in prompt.lower() or ", Europa" in prompt


def test_lookup_llm_overrides_finland_namesake_for_amuzgi(tmp_path: Path) -> None:
    folders = ["Amuzgi"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Amuzgi": MapCoordinateRecord(
                    chapter_id="Amuzgi",
                    original_label="Amuzgi",
                    display_label="Amuzgi",
                    latitude=64.0,
                    longitude=25.5,
                    confidence=0.9,
                    status=COORDINATE_STATUS_RESOLVED,
                    source="nominatim",
                    country_context="Europa",
                )
            },
        ),
    )

    def fake_geocode(_place: str, _country: str):
        return (64.0, 25.5, 0.9)

    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
        llm_rewrite=True,
        suggest_coordinates_fn=lambda _rows, _country: {
            "Amuzgi": LlmPlaceSuggestion(
                query="Amuzgi, Dagestan, Russia",
                country="Russland",
                latitude=41.82,
                longitude=47.58,
            )
        },
    )
    assert errors == []
    assert coords.places["Amuzgi"].latitude == pytest.approx(41.82)
    assert coords.places["Amuzgi"].longitude == pytest.approx(47.58)
    assert coords.places["Amuzgi"].source == "llm"
    assert coords.places["Amuzgi"].display_label == "Amuzgi"


def test_lookup_keeps_nominatim_when_it_agrees_with_llm(tmp_path: Path) -> None:
    folders = ["Amuzgi"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)

    def fake_geocode(place: str, _country: str):
        if "Dagestan" in place:
            return (41.81, 47.59, 0.91)
        return (64.0, 25.5, 0.9)

    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
        llm_rewrite=True,
        suggest_coordinates_fn=lambda _rows, _country: {
            "Amuzgi": LlmPlaceSuggestion(
                query="Amuzgi, Dagestan, Russia",
                country="Russland",
                latitude=41.82,
                longitude=47.58,
            )
        },
    )
    assert errors == []
    assert coords.places["Amuzgi"].latitude == pytest.approx(41.81)
    assert coords.places["Amuzgi"].source != "llm"


def test_lookup_leaves_manual_amuzgi_untouched(tmp_path: Path) -> None:
    folders = ["Amuzgi"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Amuzgi": MapCoordinateRecord(
                    chapter_id="Amuzgi",
                    original_label="Amuzgi",
                    display_label="Amuzgi",
                    latitude=64.0,
                    longitude=25.5,
                    confidence=1.0,
                    status=COORDINATE_STATUS_MANUAL,
                    source="manual",
                    country_context="Europa",
                )
            },
        ),
    )
    called: list[str] = []

    def fake_suggest(rows: list[tuple[str, str]], _country: str):
        called.extend(chapter_id for chapter_id, _title in rows)
        return {
            "Amuzgi": LlmPlaceSuggestion(
                query="Amuzgi, Dagestan, Russia",
                country="Russland",
                latitude=41.82,
                longitude=47.58,
            )
        }

    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=lambda _place, _country: (41.82, 47.58, 0.9),
        llm_rewrite=True,
        suggest_coordinates_fn=fake_suggest,
    )
    assert errors == []
    assert called == []
    assert coords.places["Amuzgi"].latitude == pytest.approx(64.0)
    assert coords.places["Amuzgi"].status == COORDINATE_STATUS_MANUAL


def test_lookup_after_reset_searches_previously_saved_place(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
        reset_auto_map_coordinates,
    )

    folders = ["Amuzgi"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Amuzgi": MapCoordinateRecord(
                    chapter_id="Amuzgi",
                    original_label="Amuzgi",
                    display_label="Amuzgi",
                    latitude=64.0,
                    longitude=25.5,
                    confidence=1.0,
                    status=COORDINATE_STATUS_RESOLVED,
                    source="nominatim",
                    country_context="Europa",
                )
            },
        ),
    )
    cleared, _plan, count = reset_auto_map_coordinates(project)
    assert count == 1
    assert cleared.places["Amuzgi"].has_coordinates is False
    queried: list[str] = []

    def fake_geocode(place: str, _country: str):
        queried.append(place)
        return (41.82, 47.58, 0.9)

    coords, _rebuilt, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        coordinates=cleared,
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
    )
    assert errors == []
    assert queried == ["Amuzgi"]
    assert coords.places["Amuzgi"].latitude == pytest.approx(41.82)


def test_force_recheck_searches_confirmed_in_scope_place(tmp_path: Path) -> None:
    folders = ["Amuzgi"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    save_map_coordinates(
        project,
        MapCoordinatesDocument(
            project_id=project.id,
            country="Europa",
            places={
                "Amuzgi": MapCoordinateRecord(
                    chapter_id="Amuzgi",
                    original_label="Amuzgi",
                    display_label="Amuzgi",
                    latitude=61.500276,
                    longitude=23.740640,
                    confidence=1.0,
                    status=COORDINATE_STATUS_CONFIRMED,
                    source="manual",
                    country_context="Europa",
                )
            },
        ),
    )
    queried: list[str] = []

    def fake_geocode(place: str, _country: str):
        queried.append(place)
        return (41.82, 47.58, 0.9)

    skipped, _plan, _errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
    )
    assert queried == []
    assert skipped.places["Amuzgi"].latitude == pytest.approx(61.500276)

    coords, _rebuilt, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        coordinates=skipped,
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
        force_recheck=True,
    )
    assert errors == []
    assert queried == ["Amuzgi"]
    assert coords.places["Amuzgi"].latitude == pytest.approx(41.82)


def test_lookup_uses_next_larger_city_when_village_is_wrong_country(
    tmp_path: Path,
) -> None:
    folders = ["Ropoto"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)
    queried: list[str] = []

    def fake_geocode(place: str, _country: str):
        queried.append(place)
        if "Trikala" in place:
            return (39.555, 21.768, 0.9)
        return (51.96, 7.63, 0.4)

    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
        llm_rewrite=True,
        suggest_coordinates_fn=lambda _rows, _country: {
            "Ropoto": LlmPlaceSuggestion(
                query="Ropoto",
                country="Greece",
                city="Trikala, Greece",
                fallback="city",
                latitude=39.56,
                longitude=21.77,
            )
        },
    )
    assert errors == []
    assert "Trikala, Greece" in queried
    assert coords.places["Ropoto"].latitude == pytest.approx(39.555)
    assert coords.places["Ropoto"].display_label == "Ropoto"


def test_lookup_uses_llm_city_coords_when_nominatim_misses(tmp_path: Path) -> None:
    folders = ["Ropoto"]
    project = _project(tmp_path, folders)
    project = project.model_copy(update={"video_place": "Europa"})
    _confirm(project, folders)

    def fake_geocode(_place: str, _country: str):
        return None

    coords, _plan, errors = lookup_missing_coordinates(
        project,
        plan=build_map_plan(project),
        cache_path=tmp_path / "cache.json",
        geocode_fn=fake_geocode,
        llm_rewrite=True,
        suggest_coordinates_fn=lambda _rows, _country: {
            "Ropoto": LlmPlaceSuggestion(
                query="Ropoto, Trikala, Greece",
                country="Greece",
                city="Trikala",
                fallback="city",
                latitude=39.56,
                longitude=21.77,
            )
        },
    )
    assert errors == []
    assert coords.places["Ropoto"].latitude == pytest.approx(39.56)
    assert coords.places["Ropoto"].source == "llm"
    assert "city fallback" in coords.places["Ropoto"].note
