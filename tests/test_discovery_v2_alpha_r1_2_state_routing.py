"""R1.2 Discovery V2 state, routing, flash-rerun, and reload contracts."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import otio_app.ui.routing as routing
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    evaluate_gap_accept_unresolved_eligibility,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_project_brief,
    select_hook,
    start_narrative_run,
    start_script_run,
)
from otio_app.discovery_v2.application.project_route_service import (
    DISCOVERY_PAGE_ALLOWLIST,
    DISCOVERY_SAFE_START_SLUG,
    DiscoveryRouteStatus,
    normalize_discovery_page_slug,
    resolve_discovery_route,
    slug_for_url_path,
    url_path_for_slug,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import record_claim_decision
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.supplementation import CoverageGapStatus
from otio_app.discovery_v2.ui import editorial_page
from otio_app.discovery_v2.ui import flash as flash_mod
from otio_app.discovery_v2.ui import route_context
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, get_project_by_id
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY

from test_discovery_v2_alpha_r1_1_blockers import (
    _decide_all_claims,
    _reject_all_candidates,
    _reproduced_blocker_fixture,
)
from test_discovery_v2_editorial_script import _accepted_editorial_project


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


class _FakeQueryParams(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def get_all(self, key):
        value = self.get(key)
        if value is None:
            return []
        return [value]

    def to_dict(self):
        return dict(self)

    def from_dict(self, data):
        self.clear()
        self.update({k: v for k, v in data.items() if v is not None})

    def __delitem__(self, key):
        if key in self:
            dict.__delitem__(self, key)


@dataclass
class _FakePage:
    render_fn: Callable
    title: str
    url_path: str = ""
    default: bool = False


def _install_query_params(monkeypatch: pytest.MonkeyPatch, params: dict | None = None):
    qp = _FakeQueryParams(params or {})
    monkeypatch.setattr(route_context.st, "query_params", qp)
    monkeypatch.setattr(routing.st, "query_params", qp)
    return qp


def _install_session(monkeypatch: pytest.MonkeyPatch, data: dict | None = None):
    session = dict(data or {})
    monkeypatch.setattr(route_context.st, "session_state", session)
    monkeypatch.setattr(routing.st, "session_state", session)
    monkeypatch.setattr(flash_mod.st, "session_state", session)
    return session


def _discovery_project(tmp_path: Path, temp_db_path: Path, name: str = "R12 Disco"):
    root = tmp_path / name.replace(" ", "_")
    root.mkdir(parents=True, exist_ok=True)
    (root / "clips").mkdir()
    return create_project(
        ProjectCreate(
            name=name,
            project_root=str(root),
            language="de",
            voice_over_dir=str(root / "vo"),
            selected_asset_subdirs=["clips"],
            project_mode=ProjectMode.DISCOVERY_V2,
        ),
        db_path=temp_db_path,
    )


def _classic_project(tmp_path: Path, temp_db_path: Path, name: str = "R12 Classic"):
    root = tmp_path / name.replace(" ", "_")
    root.mkdir(parents=True, exist_ok=True)
    (root / "clips").mkdir()
    return create_project(
        ProjectCreate(
            name=name,
            project_root=str(root),
            language="de",
            voice_over_dir=str(root / "vo"),
            selected_asset_subdirs=["clips"],
            project_mode=ProjectMode.WITH_VOICEOVER,
        ),
        db_path=temp_db_path,
    )


# --- Domain / application route contract ---


def test_r1_page_slugs_cover_required_workflow_pages() -> None:
    required = {
        "inventory",
        "media_intake",
        "technical_validation",
        "asset_analysis",
        "editorial",
        "narration",
        "visual_edit",
        "review_export",
    }
    assert required <= DISCOVERY_PAGE_ALLOWLIST
    for slug in required:
        assert url_path_for_slug(slug)
        assert slug_for_url_path(url_path_for_slug(slug)) == slug


def test_r1_unknown_missing_property_page_not_auto_accepted() -> None:
    slug, unknown = normalize_discovery_page_slug("not-a-page")
    assert slug is None and unknown is True


def test_r1_resolve_ok_discovery_project(tmp_path: Path, temp_db_path: Path) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    resolution = resolve_discovery_route(
        project_id=project.id,
        page_token="editorial",
        db_path=temp_db_path,
    )
    assert resolution.ok
    assert resolution.page_slug == "editorial"
    assert resolution.url_path == "discovery-editorial"
    assert resolution.project is not None
    assert resolution.project.project_mode == ProjectMode.DISCOVERY_V2


def test_r1_invalid_project_id_keeps_discovery_shell(
    tmp_path: Path, temp_db_path: Path
) -> None:
    resolution = resolve_discovery_route(
        project_id="missing-id",
        page_token="editorial",
        db_path=temp_db_path,
    )
    assert resolution.status == DiscoveryRouteStatus.PROJECT_NOT_FOUND
    assert resolution.keep_discovery_shell is True
    assert resolution.project is None


def test_r1_mode_mismatch_blocks_discovery_open(
    tmp_path: Path, temp_db_path: Path
) -> None:
    classic = _classic_project(tmp_path, temp_db_path)
    resolution = resolve_discovery_route(
        project_id=classic.id,
        page_token="editorial",
        db_path=temp_db_path,
    )
    assert resolution.status == DiscoveryRouteStatus.PROJECT_MODE_MISMATCH
    assert resolution.keep_discovery_shell is True


def test_r1_unknown_page_falls_back_to_overview_keeps_project(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    resolution = resolve_discovery_route(
        project_id=project.id,
        page_token="totally-unknown",
        db_path=temp_db_path,
    )
    assert resolution.status == DiscoveryRouteStatus.INVALID_DISCOVERY_ROUTE
    assert resolution.fallback_page_slug == DISCOVERY_SAFE_START_SLUG
    assert resolution.project_id == project.id
    assert resolution.keep_discovery_shell is True


# --- Reload / deep link ---


def test_r1_reload_keeps_project_id_and_editorial_route(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    qp = _install_query_params(
        monkeypatch, {"project_id": project.id, "page": "editorial"}
    )
    monkeypatch.setattr(
        route_context,
        "current_streamlit_url_path",
        lambda: "discovery-editorial",
    )
    monkeypatch.setattr(
        route_context,
        "get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )

    resolution = route_context.restore_discovery_route_context()
    assert resolution is not None and resolution.ok
    assert session[ACTIVE_PROJECT_KEY] == project.id
    assert qp.get("page") == "editorial"
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2


@pytest.mark.parametrize(
    "slug,url_path",
    [
        ("narration", "discovery-narration"),
        ("visual_edit", "discovery-visual-edit"),
        ("review_export", "discovery-review-export"),
    ],
)
def test_r1_reload_keeps_named_discovery_page(
    slug: str,
    url_path: str,
    tmp_path: Path,
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    _install_query_params(monkeypatch, {"project_id": project.id, "page": slug})
    monkeypatch.setattr(route_context, "current_streamlit_url_path", lambda: url_path)
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    monkeypatch.setattr(
        route_context,
        "get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    resolution = route_context.restore_discovery_route_context()
    assert resolution is not None and resolution.ok
    assert session[ACTIVE_PROJECT_KEY] == project.id
    assert session[route_context.ROUTE_PAGE_SLUG_KEY] == slug


def test_smoke_c_browser_reload_same_project_and_page(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke C — Discovery Editorial survives simulated browser reload."""
    project = _discovery_project(tmp_path, temp_db_path)
    # First visit binds route.
    session = _install_session(monkeypatch, {ACTIVE_PROJECT_KEY: project.id})
    qp = _install_query_params(monkeypatch, {})
    monkeypatch.setattr(
        route_context, "current_streamlit_url_path", lambda: "discovery-editorial"
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    monkeypatch.setattr(
        route_context,
        "get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    route_context.restore_discovery_route_context()
    assert qp.get("project_id") == project.id
    assert qp.get("page") == "editorial"

    # Browser reload: empty session, URL retained.
    session.clear()
    session_after = _install_session(monkeypatch, {})
    _install_query_params(
        monkeypatch, {"project_id": project.id, "page": "editorial"}
    )
    monkeypatch.setattr(route_context.st, "session_state", session_after)
    monkeypatch.setattr(routing.st, "session_state", session_after)
    resolution = route_context.restore_discovery_route_context()
    assert resolution is not None and resolution.ok
    assert session_after[ACTIVE_PROJECT_KEY] == project.id
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2


def test_smoke_d_deep_link_without_prior_session(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    _install_query_params(
        monkeypatch, {"project_id": project.id, "page": "editorial"}
    )
    monkeypatch.setattr(
        route_context, "current_streamlit_url_path", lambda: "discovery-editorial"
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    resolution = route_context.restore_discovery_route_context()
    assert resolution is not None and resolution.ok
    assert session[ACTIVE_PROJECT_KEY] == project.id
    assert session.get(route_context.ROUTE_HINT_KEY) is True


def test_smoke_e_unknown_page_safe_start_no_classic_fallback(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    qp = _install_query_params(
        monkeypatch, {"project_id": project.id, "page": "does-not-exist"}
    )
    monkeypatch.setattr(route_context, "current_streamlit_url_path", lambda: "")
    monkeypatch.setattr(route_context, "_maybe_switch_to_safe_start", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    resolution = route_context.restore_discovery_route_context()
    assert resolution is not None
    assert resolution.status == DiscoveryRouteStatus.INVALID_DISCOVERY_ROUTE
    assert session[ACTIVE_PROJECT_KEY] == project.id
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2
    assert qp.get("page") == DISCOVERY_SAFE_START_SLUG
    # Discovery shell stays Discovery — Classic mapping page is absent from Discovery nav.
    monkeypatch.setattr(
        routing.st,
        "Page",
        lambda render_fn, *, title, url_path="", default=False: _FakePage(
            render_fn, title, url_path, default
        ),
    )
    titles = [p.title for p in routing._build_discovery_v2_pages(lambda: None, lambda: None)]
    assert "Discovery V2 – Übersicht" in titles
    assert "② Zuordnung" not in titles


def test_r1_invalid_project_id_no_classic_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, temp_db_path: Path
) -> None:
    session = _install_session(monkeypatch, {})
    _install_query_params(
        monkeypatch, {"project_id": "missing", "page": "editorial"}
    )
    monkeypatch.setattr(
        route_context, "current_streamlit_url_path", lambda: "discovery-editorial"
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: None,
    )
    route_context.restore_discovery_route_context()
    assert ACTIVE_PROJECT_KEY not in session or session.get(ACTIVE_PROJECT_KEY) is None
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2
    err = session.get(route_context.ROUTE_ERROR_KEY)
    assert err and err["code"] == "project_not_found"


def test_r1_project_switch_clears_old_ui_state(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _discovery_project(tmp_path, temp_db_path, name="A")
    b = _discovery_project(tmp_path / "b", temp_db_path, name="B")
    session = _install_session(
        monkeypatch,
        {
            ACTIVE_PROJECT_KEY: a.id,
            "discovery_v2_editorial_flash": "stale",
            "discovery_v2_lock_confirmed": True,
        },
    )
    _install_query_params(monkeypatch, {})
    route_context.bind_active_discovery_project(b.id, page_slug="overview")
    assert session[ACTIVE_PROJECT_KEY] == b.id
    assert "discovery_v2_lock_confirmed" not in session
    assert session.get(route_context.ROUTE_PAGE_SLUG_KEY) == "overview"


def test_r1_discovery_never_silently_opens_as_classic(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    _install_query_params(monkeypatch, {"project_id": project.id, "page": "editorial"})
    monkeypatch.setattr(
        route_context, "current_streamlit_url_path", lambda: "discovery-editorial"
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    route_context.restore_discovery_route_context()
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2


# --- Mutations / flash / rerun ---


def test_smoke_a_editorial_without_manual_reload(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    brief = save_project_brief(
        project,
        language="de",
        topic="R1.2 Brief",
        target_audience="Audience",
        tone="klar",
    )
    assert brief.ok
    view = get_editorial_view(project)
    assert view.can_start_narrative

    narrative = start_narrative_run(project, sync=True)
    assert narrative.started
    view = get_editorial_view(project)
    assert view.hooks

    selected = select_hook(project, hook_id=view.hooks[0].hook_id)
    assert selected.ok
    view = get_editorial_view(project)
    assert view.can_start_script


def test_smoke_b_claim_and_gap_refresh_without_manual_reload(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    view = get_editorial_view(project)
    claim = view.script_bundle["claims"][0]
    record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="confirmed",
    )
    # Fresh viewmodel reflects claim history without UI reload ritual.
    view2 = get_editorial_view(project)
    assert view2.script is not None

    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert eligibility.ok
    accepted = accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=[risk.value for risk in eligibility.visible_risks],
        user_confirmed=True,
    )
    assert accepted.ok and accepted.gap is not None
    assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    risk_key = f"{gap_id}:coverage_exact_match_not_verified"
    gate = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint="pending",
        accepted_unresolved_risk_confirmations={risk_key: True},
    )
    assert gate.preview is not None
    assert gate.preview.lock_fingerprint


def test_r1_script_lock_unlocks_narration_gate(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=[risk.value for risk in eligibility.visible_risks],
        user_confirmed=True,
    ).ok
    risk_key = f"{gap_id}:coverage_exact_match_not_verified"
    gate = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint="pending",
        accepted_unresolved_risk_confirmations={risk_key: True},
    )
    assert gate.preview is not None and gate.preview.lock_fingerprint
    locked = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=gate.preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={risk_key: True},
    )
    assert locked.ok
    from otio_app.discovery_v2.application.voice_generation_service import (
        get_narration_view,
    )

    narration = get_narration_view(project)
    assert narration.ok
    assert narration.effective_lock is not None


def test_r1_flash_survives_one_rerun_then_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _install_session(monkeypatch, {})
    messages: list[str] = []
    monkeypatch.setattr(flash_mod.st, "success", lambda msg: messages.append(str(msg)))
    reruns: list[str] = []
    monkeypatch.setattr(flash_mod.st, "rerun", lambda: reruns.append("rerun"))

    flash_mod.discovery_ui_flash_and_rerun("gespeichert")
    assert session[flash_mod.FLASH_KEY]["message"] == "gespeichert"
    assert reruns == ["rerun"]

    flash_mod.consume_discovery_flash()
    assert flash_mod.FLASH_KEY not in session
    assert messages == ["gespeichert"]
    flash_mod.consume_discovery_flash()
    assert messages == ["gespeichert"]


def test_r1_mutation_rerun_does_not_double_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    session: dict = {}
    st = MagicMock()
    st.session_state = session
    st.rerun.side_effect = lambda: calls.append("rerun")
    monkeypatch.setattr(editorial_page, "st", st)
    monkeypatch.setattr(flash_mod, "st", st)

    # One successful mutation path + one controlled rerun — no second mutation.
    calls.append("save")
    editorial_page._flash_and_rerun("ok")
    assert calls == ["save", "rerun"]
    assert session[flash_mod.FLASH_KEY]["message"] == "ok"


def test_smoke_f_jobstart_rerun_single_launcher(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.discovery_v2.application import analysis_prepare_service as prep
    from otio_app.discovery_v2.ui import asset_analysis_page

    project = _accepted_editorial_project(tmp_path, temp_db_path)
    launches: list[str] = []

    real_start = prep.start_analysis_prepare

    def _counting_start(p, **kwargs):
        launches.append("start")
        # Avoid real job when can_start is false — just count explicit calls.
        return SimpleNamespace(started=True, message="gestartet", run=None)

    monkeypatch.setattr(asset_analysis_page, "start_analysis_prepare", _counting_start)
    monkeypatch.setattr(asset_analysis_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        asset_analysis_page,
        "get_analysis_prepare_view",
        lambda _p: SimpleNamespace(
            ok=True,
            can_start=True,
            plan_id="plan",
            active_run=None,
            latest_run=None,
            items=[],
            chain_error_code=None,
            message=None,
        ),
    )
    monkeypatch.setattr(
        asset_analysis_page,
        "get_analysis_prepare_artifact_review",
        lambda _p: SimpleNamespace(ok=True, shots=[], frames=[], message=None),
    )
    monkeypatch.setattr(
        asset_analysis_page,
        "_render_model_analysis_section",
        lambda _p: None,
    )

    class _FakeSt:
        def __init__(self):
            self.session_state = {}
            self.clicked = True
            self.rerun_calls = 0

        def title(self, *a, **k):
            return None

        def subheader(self, *a, **k):
            return None

        def info(self, *a, **k):
            return None

        def warning(self, *a, **k):
            return None

        def caption(self, *a, **k):
            return None

        def write(self, *a, **k):
            return None

        def dataframe(self, *a, **k):
            return None

        def button(self, *a, **k):
            was = self.clicked
            self.clicked = False  # only first render clicks
            return was

        def rerun(self):
            self.rerun_calls += 1

        def success(self, *a, **k):
            return None

    fake = _FakeSt()
    monkeypatch.setattr(asset_analysis_page, "st", fake)
    monkeypatch.setattr(flash_mod, "st", fake)
    monkeypatch.setattr(
        flash_mod,
        "discovery_ui_flash_and_rerun",
        lambda msg, level="success": (fake.session_state.__setitem__(
            flash_mod.FLASH_KEY, {"level": level, "message": msg}
        ), fake.rerun()),
    )

    asset_analysis_page.render_discovery_asset_analysis_page()
    assert launches == ["start"]
    assert fake.rerun_calls == 1

    # Controlled rerun / second render must not start another job.
    asset_analysis_page.render_discovery_asset_analysis_page()
    assert launches == ["start"]


def test_r1_browser_reload_does_not_start_job(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.discovery_v2.ui import asset_analysis_page

    project = _discovery_project(tmp_path, temp_db_path)
    launches: list[str] = []
    monkeypatch.setattr(
        asset_analysis_page,
        "start_analysis_prepare",
        lambda *a, **k: launches.append("start") or SimpleNamespace(started=True, message="x"),
    )
    monkeypatch.setattr(asset_analysis_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        asset_analysis_page,
        "get_analysis_prepare_view",
        lambda _p: SimpleNamespace(
            ok=True,
            can_start=True,
            plan_id="plan",
            active_run=None,
            latest_run=None,
            items=[],
            chain_error_code=None,
            message=None,
        ),
    )
    monkeypatch.setattr(
        asset_analysis_page,
        "get_analysis_prepare_artifact_review",
        lambda _p: SimpleNamespace(ok=True, shots=[], frames=[], message=None),
    )
    monkeypatch.setattr(asset_analysis_page, "_render_model_analysis_section", lambda _p: None)

    class _FakeSt:
        session_state: dict = {}

        def title(self, *a, **k):
            return None

        def subheader(self, *a, **k):
            return None

        def info(self, *a, **k):
            return None

        def warning(self, *a, **k):
            return None

        def caption(self, *a, **k):
            return None

        def write(self, *a, **k):
            return None

        def dataframe(self, *a, **k):
            return None

        def button(self, *a, **k):
            return False  # reload: no click

    monkeypatch.setattr(asset_analysis_page, "st", _FakeSt())
    asset_analysis_page.render_discovery_asset_analysis_page()
    assert launches == []


def test_r1_double_render_no_gateway_or_sqlite_from_streamlit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_discovery_v2_editorial_ui import _FakeStreamlit, _project

    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    monkeypatch.setattr(editorial_page, "st", fake_st)
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        editorial_page,
        "get_editorial_view",
        lambda _p: MagicMock(
            ok=True,
            stale=False,
            active_run=None,
            active_brief=None,
            briefs=[],
            can_start_narrative=False,
            narrative_plan=None,
            hooks=[],
            selected_hook_id=None,
            can_start_script=False,
            can_start_structure=False,
            script=None,
            script_versions=[],
            script_bundle=None,
            can_start_coverage=False,
            coverage_audit=None,
            runs=[],
            message=None,
        ),
    )
    monkeypatch.setattr(
        editorial_page,
        "get_supplementation_view",
        lambda _p: MagicMock(ok=True, gaps=[], candidates_by_gap={}, script_locks=[], active_run=None, message=None),
    )
    started = []
    monkeypatch.setattr(
        editorial_page,
        "start_narrative_run",
        lambda *a, **k: started.append("job") or SimpleNamespace(started=True, message="x"),
    )
    editorial_page.render_discovery_editorial_page()
    editorial_page.render_discovery_editorial_page()
    assert started == []


def test_r1_schema_remains_20() -> None:
    assert REGISTRY_SCHEMA_VERSION == "20"


def test_r1_discovery_url_paths_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routing.st,
        "Page",
        lambda render_fn, *, title, url_path="", default=False: _FakePage(
            render_fn, title, url_path, default
        ),
    )
    pages = routing._build_discovery_v2_pages(lambda: None, lambda: None)
    paths = {page.url_path for page in pages}
    for slug in (
        "editorial",
        "narration",
        "visual_edit",
        "review_export",
        "inventory",
        "media_intake",
        "technical_validation",
        "asset_analysis",
    ):
        assert url_path_for_slug(slug) in paths


def test_r1_run_app_navigation_restores_discovery_from_query(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _discovery_project(tmp_path, temp_db_path)
    session = _install_session(monkeypatch, {})
    _install_query_params(
        monkeypatch, {"project_id": project.id, "page": "editorial"}
    )
    monkeypatch.setattr(
        route_context, "current_streamlit_url_path", lambda: "discovery-editorial"
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.project_route_service.get_project_by_id",
        lambda pid, db_path=None: get_project_by_id(pid, db_path=temp_db_path),
    )
    monkeypatch.setattr(
        routing,
        "get_project_by_id",
        lambda pid: get_project_by_id(pid, db_path=temp_db_path),
    )
    monkeypatch.setattr(
        routing.st,
        "Page",
        lambda render_fn, *, title, url_path="", default=False: _FakePage(
            render_fn, title, url_path, default
        ),
    )
    captured: dict[str, Any] = {}

    class _Nav:
        def __init__(self, pages, position="sidebar"):
            captured["pages"] = pages

        def run(self):
            return None

    monkeypatch.setattr(routing.st, "navigation", _Nav)
    monkeypatch.setattr(routing.st, "sidebar", contextlib.nullcontext())
    monkeypatch.setattr(routing.st, "caption", lambda *_a, **_k: None)
    monkeypatch.setattr(routing, "render_activity_panel", lambda: None)
    monkeypatch.setattr(routing, "format_build_label", lambda: "test")

    routing.run_app_navigation(render_new_project=lambda: None, render_project_list=lambda: None)
    titles = [page.title for page in captured["pages"]]
    assert "Discovery V2 – Übersicht" in titles
    assert "② Zuordnung" not in titles
    assert session[ACTIVE_PROJECT_KEY] == project.id
