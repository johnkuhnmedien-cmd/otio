"""Phase 8.1: Cut-Plan-Grundgerüst — Datenmodelle, Pfade, Settings, UI-Platzhalter, Guards.

Noch KEINE Timeline-Mathematik, Asset-Auswahl, Split/Merge-Logik,
Supplement-Requests, Validierung oder Confirm/Lock — siehe cut_plan_tab.py.
"""

from __future__ import annotations

import inspect
import json
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC,
    CUT_PLAN_DEFAULT_MAX_ASSET_USAGE,
    CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS,
    CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC,
    CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC,
    CUT_PLAN_DEFAULT_SHOT_MAX_SEC,
    CUT_PLAN_DEFAULT_SHOT_MIN_SEC,
    CUT_PLAN_DEFAULT_TIMELINE_FPS,
    CUT_PLAN_DEFAULT_TIMELINE_HEIGHT,
    CUT_PLAN_DEFAULT_TIMELINE_WIDTH,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_VALIDATION_STATUS_PASS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_dir,
    get_cut_plan_draft_path,
    get_cut_plan_settings_path,
    get_cut_plan_supplement_requests_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
    get_edit_plan_dir,
    get_exports_dir,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    CutPlanTraceDocument,
    CutPlanTraceEntry,
    CutPlanValidationError,
    CutPlanValidationReport,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    default_cut_plan_settings,
    load_cut_plan_settings,
    save_cut_plan_settings,
)
from otio_app.ui import navigation as nav
from otio_app.ui import routing
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page


@dataclass
class _FakePage:
    """Ersetzt st.Page in Routing-Tests — echte StreamlitPage-Objekte lösen
    title/url_path erst innerhalb eines laufenden Scripts auf (siehe
    tests/test_routing_project_mode.py)."""

    render_fn: Callable
    title: str
    url_path: str = ""
    default: bool = False


@pytest.fixture
def _fake_streamlit_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_page(render_fn, *, title, url_path="", default=False):
        return _FakePage(render_fn=render_fn, title=title, url_path=url_path, default=default)

    monkeypatch.setattr(routing.st, "Page", _fake_page)


def _noop() -> None:
    return None


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="cut-plan-project",
        name="Cut Plan Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=mode,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


# --- 1. CutPlanSettings Defaults ---


def test_cut_plan_settings_defaults_are_correct(tmp_path: Path) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    settings = default_cut_plan_settings(project)

    assert settings.project_id == project.id
    assert settings.initial_audio_offset_sec == CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC == 1.0
    assert settings.pause_between_sections_sec == CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC == 0.25
    assert settings.section_visual_preroll_sec == CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC == 0.0
    assert settings.video_head_trim_sec == CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC == 1.0
    assert settings.shot_min_sec == CUT_PLAN_DEFAULT_SHOT_MIN_SEC == 3.0
    assert settings.shot_max_sec == CUT_PLAN_DEFAULT_SHOT_MAX_SEC == 8.0
    assert settings.max_asset_usage == CUT_PLAN_DEFAULT_MAX_ASSET_USAGE == 2
    assert settings.min_asset_reuse_distance_shots == CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS == 0
    assert settings.timeline_fps == CUT_PLAN_DEFAULT_TIMELINE_FPS == 25
    assert settings.timeline_width == CUT_PLAN_DEFAULT_TIMELINE_WIDTH == 3840
    assert settings.timeline_height == CUT_PLAN_DEFAULT_TIMELINE_HEIGHT == 2160


# --- 2. CutPlanSettings speichern/laden ---


def test_cut_plan_settings_save_and_load_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    custom = default_cut_plan_settings(project).model_copy(
        update={"pause_between_sections_sec": 0.5, "max_asset_usage": 3}
    )
    save_cut_plan_settings(project, custom)

    loaded = load_cut_plan_settings(project)
    assert loaded.pause_between_sections_sec == 0.5
    assert loaded.max_asset_usage == 3
    assert loaded.project_id == project.id


def test_cut_plan_settings_load_returns_defaults_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    loaded = load_cut_plan_settings(project)
    assert loaded.initial_audio_offset_sec == 1.0
    assert not get_cut_plan_settings_path(project.language_work_dir_path).exists()


# --- 3. Cut-Plan-Pfadhelfer ---


def test_cut_plan_path_helpers_point_under_cut_plan_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    work_dir = project.work_dir_path
    cut_plan_dir = get_cut_plan_dir(work_dir)

    assert cut_plan_dir == work_dir / "voiceover_generation" / "cut_plan"
    assert get_cut_plan_settings_path(work_dir) == cut_plan_dir / "cut_plan_settings.json"
    assert get_cut_plan_draft_path(work_dir) == cut_plan_dir / "cut_plan.draft.json"
    assert get_cut_plan_validation_report_path(work_dir) == cut_plan_dir / "cut_plan.validation_report.json"
    assert get_cut_plan_confirmed_path(work_dir) == cut_plan_dir / "cut_plan.confirmed.json"
    assert get_cut_plan_trace_path(work_dir) == cut_plan_dir / "cut_plan.trace.json"
    assert (
        get_cut_plan_supplement_requests_path(work_dir)
        == cut_plan_dir / "supplement_requests.from_cut_plan.json"
    )


def test_cut_plan_supplement_requests_path_differs_from_production_supplement_path(tmp_path: Path) -> None:
    from otio_app.project_layout import get_supplement_dir

    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    cut_plan_path = get_cut_plan_supplement_requests_path(project.language_work_dir_path)
    production_dir = get_supplement_dir(project.language_work_dir_path)
    assert production_dir not in cut_plan_path.parents
    assert cut_plan_path != production_dir / "supplement_requests.json"


# --- 4-6. Serialisierung ---


def test_cut_plan_document_serializes_and_deserializes() -> None:
    doc = CutPlanDocument(
        project_id="p1",
        project_title="Wunder der Wüste",
        items=[
            CutPlanItem(
                cut_item_id="cut_001",
                source_refs=[CutPlanSourceRef(source_sentence_id="sentence_001", text="Ein Satz.")],
                planned_visual_segments=[VisualSegment(segment_id="seg_001")],
            )
        ],
        warnings=[CutPlanValidationError(type="SHOT_TOO_SHORT")],
    )
    payload = doc.model_dump_json()
    restored = CutPlanDocument.model_validate_json(payload)

    assert restored.project_id == "p1"
    assert restored.status == CUT_PLAN_STATUS_DRAFT
    assert len(restored.items) == 1
    assert restored.items[0].source_refs[0].source_sentence_id == "sentence_001"
    assert restored.items[0].asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED
    assert restored.items[0].planned_visual_segments[0].segment_id == "seg_001"
    assert restored.warnings[0].type == "SHOT_TOO_SHORT"


def test_cut_plan_item_supports_multiple_source_refs_for_merge() -> None:
    """§5 Nutzerentscheidung: ein CutPlanItem kann mehrere source_refs enthalten
    (Merge kurzer Sätze zu einem visuellen Schnitt)."""
    item = CutPlanItem(
        cut_item_id="cut_merged",
        source_refs=[
            CutPlanSourceRef(source_sentence_id="sentence_001", text="Kurzer Satz eins."),
            CutPlanSourceRef(source_sentence_id="sentence_002", text="Kurzer Satz zwei."),
        ],
        duration_strategy="MERGED",
    )
    assert len(item.source_refs) == 2
    assert item.duration_strategy == "MERGED"


def test_cut_plan_trace_document_serializes_and_deserializes() -> None:
    doc = CutPlanTraceDocument(
        project_id="p1",
        source_plan_hash="abc",
        cut_plan_hash="def",
        entries=[
            CutPlanTraceEntry(
                trace_id="trace_001",
                cut_item_id="cut_001",
                chosen_asset_id="asset_clip1",
                choice_reason="primary_asset verwendet",
            )
        ],
    )
    payload = doc.model_dump_json()
    restored = CutPlanTraceDocument.model_validate_json(payload)

    assert restored.source_plan_hash == "abc"
    assert restored.entries[0].chosen_asset_id == "asset_clip1"


def test_cut_plan_validation_report_serializes_and_deserializes() -> None:
    report = CutPlanValidationReport(
        project_id="p1",
        status=CUT_PLAN_VALIDATION_STATUS_PASS,
        warnings=[CutPlanValidationError(type="FRAME_ROUNDING_ERROR", severity="WARNING")],
    )
    payload = report.model_dump_json()
    restored = CutPlanValidationReport.model_validate_json(payload)

    assert restored.status == "PASS"
    assert restored.warnings[0].type == "FRAME_ROUNDING_ERROR"


# --- 7. Navigation nur im without_voiceover Workflow ---


def test_page_cut_plan_only_in_without_voiceover_workflow() -> None:
    assert nav.PAGE_CUT_PLAN in nav.VOICEOVER_GEN_WORKFLOW_PAGES
    assert nav.PAGE_CUT_PLAN not in nav.WORKFLOW_PAGES
    assert nav.PAGE_CUT_PLAN not in nav.NAVIGATION_OPTIONS
    assert nav.PAGE_CUT_PLAN in nav.VOICEOVER_GEN_NAVIGATION_OPTIONS


# --- 8. with_voiceover Workflow bleibt unverändert ---


def test_with_voiceover_pages_unchanged_by_cut_plan_addition(_fake_streamlit_page: None) -> None:
    pages = routing._build_with_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert nav.PAGE_CUT_PLAN not in titles
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "② Zuordnung",
        "②½ Supplement Assets",
        "③ Schnittplan",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]


def test_without_voiceover_pages_include_cut_plan_after_final_output(_fake_streamlit_page: None) -> None:
    pages = routing._build_without_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    final_output_index = titles.index("⑦ Final Output")
    cut_plan_index = titles.index("⑧ Cut Plan")
    assert cut_plan_index == final_output_index + 1


# --- 9-13. UI-Guards ---


def test_cut_plan_page_renders_without_exception_when_no_confirmed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen


def test_cut_plan_page_writes_nothing_when_no_confirmed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()

    assert not get_cut_plan_dir(project.language_work_dir_path).exists()
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_cut_plan_page_guards_with_voiceover_project_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen und darf nichts schreiben

    assert not get_cut_plan_dir(project.language_work_dir_path).exists()
    assert not (project.language_work_dir_path / "voiceover_generation").exists()
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_cut_plan_page_shows_hint_when_confirmed_plan_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda message, *a, **k: warnings.append(message))

    render_cut_plan_page()

    assert any("Final Output" in message for message in warnings)


def test_cut_plan_page_shows_project_plan_status_when_confirmed_plan_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.services.voiceover_generation.final_plan_service import (
        save_confirmed_voiceover_project_plan,
    )
    from otio_app.services.voiceover_generation.models import ConfirmedVoiceoverProjectPlan

    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, project_title="Test Titel", status="AUDIO_READY")
    save_confirmed_voiceover_project_plan(project, plan)
    _patch_project_selector(project, monkeypatch)

    metrics: list[tuple] = []
    monkeypatch.setattr("streamlit.metric", lambda label, value, *a, **k: metrics.append((label, value)))

    render_cut_plan_page()

    metric_values = [value for _, value in metrics]
    assert "Test Titel" in metric_values
    assert "AUDIO_READY" in metric_values


def test_cut_plan_page_never_writes_edit_plan_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()

    assert not get_edit_plan_dir(project.language_work_dir_path).exists()
    assert not list(project.work_dir_path.rglob("*.edit_plan.json"))


def test_cut_plan_page_never_triggers_otio_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Struktureller Nachweis: cut_plan_tab.py importiert otio_exporter nicht,
    zusätzlich funktional geprüft, dass kein exports/-Ordner entsteht."""
    import otio_app.ui.voiceover_generation.cut_plan_tab as cut_plan_tab_module

    source = inspect.getsource(cut_plan_tab_module)
    assert "otio_exporter" not in source
    assert "export_otio_timeline" not in source

    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)
    render_cut_plan_page()

    assert not get_exports_dir(project.language_work_dir_path).exists()


# --- 14. Struktureller Guard gegen Produktions-Symbole ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def _cut_plan_module_names() -> list[str]:
    import otio_app.services.voiceover_generation as service_pkg
    import otio_app.ui.voiceover_generation as ui_pkg

    names = []
    for package, prefix in (
        (service_pkg, "otio_app.services.voiceover_generation"),
        (ui_pkg, "otio_app.ui.voiceover_generation"),
    ):
        package_path = Path(package.__file__).parent
        for module_info in pkgutil.iter_modules([str(package_path)]):
            if module_info.name.startswith("cut_plan"):
                names.append(f"{prefix}.{module_info.name}")
    return names


def test_cut_plan_modules_exist_and_are_discovered() -> None:
    names = _cut_plan_module_names()
    assert "otio_app.services.voiceover_generation.cut_plan_models" in names
    assert "otio_app.services.voiceover_generation.cut_plan_settings_service" in names
    assert "otio_app.ui.voiceover_generation.cut_plan_tab" in names


def test_cut_plan_modules_never_reference_forbidden_production_symbols() -> None:
    import importlib
    import re

    for module_name in _cut_plan_module_names():
        module = importlib.import_module(module_name)
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            # Wort-Grenzen-Suche statt reiner Substring-Suche: verhindert
            # False Positives durch legitime, längere Bridge-Funktionsnamen
            # wie build_edit_plan_draft_from_confirmed_cut_plan (Phase 9.1),
            # die 'build_edit_plan' nur als Präfix enthalten, nicht als
            # eigenständigen Aufruf/Bezeichner.
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module_name} referenziert verbotenes Produktions-Symbol '{forbidden}'."
            )


def test_cut_plan_modules_write_nothing_under_edit_plan_or_exports(tmp_path: Path) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    settings = default_cut_plan_settings(project)
    save_cut_plan_settings(project, settings)

    assert not get_edit_plan_dir(project.language_work_dir_path).exists()
    assert not get_exports_dir(project.language_work_dir_path).exists()
    assert get_cut_plan_settings_path(project.language_work_dir_path).is_file()


# --- 15. Regression: bestehender Workflow bleibt funktionsfähig ---


def test_production_modules_unaffected_by_cut_plan_addition() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


def test_cut_plan_settings_json_round_trip_matches_default_dict(tmp_path: Path) -> None:
    """Smoke-Test: Die in der Aufgabenstellung genannten Default-Werte landen
    unverändert in der gespeicherten JSON-Datei."""
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    settings = default_cut_plan_settings(project)
    save_cut_plan_settings(project, settings)

    payload = json.loads(get_cut_plan_settings_path(project.language_work_dir_path).read_text(encoding="utf-8"))
    assert payload["initial_audio_offset_sec"] == 1.0
    assert payload["pause_between_sections_sec"] == 0.25
    assert payload["section_visual_preroll_sec"] == 0.0
    assert payload["video_head_trim_sec"] == 1.0
    assert payload["shot_min_sec"] == 3.0
    assert payload["shot_max_sec"] == 8.0
    assert payload["max_asset_usage"] == 2
    assert payload["min_asset_reuse_distance_shots"] == 0
    assert payload["timeline_fps"] == 25
    assert payload["timeline_width"] == 3840
    assert payload["timeline_height"] == 2160
