"""Structure finalization hotfix: complete structure leaves structure_pending."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import assert_schema_20, install_no_media_io_guards
from fixtures.script_lock_current_state_l1 import (
    _decide_all_claims,
    _resolve_all_gaps_locally,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.narration_job_launcher import (
    reset_narration_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import (
    FakeTextAdapter,
    reset_fake_text_test_hook,
    set_fake_text_test_hook,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_user_script_edit,
    start_coverage_run,
    start_script_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    build_current_script_lock_preview,
    preview_script_lock,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING,
    EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE,
    EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING,
    ScriptDraftStatus,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.ui import editorial_page
from test_discovery_v2_editorial_script import (
    _accepted_editorial_project,
    _brief_to_narrative,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _pending_structured_project(tmp_path: Path, temp_db_path: Path):
    """Script v2 in structure_pending after user edit (USA_v2-like)."""

    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(
        project, full_text=view.script.full_text + " Zusatzsatz fuer Struktur."
    )
    assert edited.ok and edited.script is not None
    assert edited.script.status == ScriptDraftStatus.STRUCTURE_PENDING
    assert edited.script.script_version == 2
    return project


def _count_structure_rows(project) -> dict[str, int]:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        script = editorial_repo.get_active_script(conn, project_id=project.id)
        assert script is not None
        sid = script.script_id
        sentences = conn.execute(
            "SELECT COUNT(*) AS n FROM script_sentences WHERE script_id = ?", (sid,)
        ).fetchone()["n"]
        beats = conn.execute(
            "SELECT COUNT(*) AS n FROM visual_beats WHERE script_id = ?", (sid,)
        ).fetchone()["n"]
        intents = conn.execute(
            """
            SELECT COUNT(*) AS n FROM visual_intents
            WHERE visual_beat_id IN (
                SELECT visual_beat_id FROM visual_beats WHERE script_id = ?
            )
            """,
            (sid,),
        ).fetchone()["n"]
        return {
            "script_id": sid,
            "script_version": script.script_version,
            "sentences": int(sentences),
            "beats": int(beats),
            "intents": int(intents),
            "status": script.status.value,
        }
    finally:
        conn.close()


def _incomplete_structure_hook(*, drop: str):
    adapter = FakeTextAdapter()

    def hook(request):
        if request.request_kind != "structure":
            return None
        payload = adapter._script_or_structure(request)
        if drop == "sentences":
            payload["sentences"] = []
            payload["script"]["sentence_order"] = []
            payload["claims"] = []
            payload["visual_beats"] = []
            payload["visual_intents"] = []
        elif drop == "beats":
            for sentence in payload["sentences"]:
                sentence["visual_beat_ids"] = []
            payload["visual_beats"] = []
            payload["visual_intents"] = []
        elif drop == "intents":
            payload["visual_intents"] = []
        else:
            raise AssertionError(drop)
        return payload

    return hook


def test_structure_update_finalizes_pending_script_when_structure_is_complete(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    result = start_structure_run(project, sync=True)
    assert result.started is True
    assert result.message == "Struktur aktualisiert."
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.REVIEW_REQUESTED
    bundle = view.script_bundle or {}
    assert bundle.get("sentences")
    assert bundle.get("visual_beats")
    assert bundle.get("visual_intents")


def test_structure_update_keeps_pending_status_when_sentences_are_incomplete(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    set_fake_text_test_hook(_incomplete_structure_hook(drop="sentences"))
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.STRUCTURE_PENDING


def test_structure_update_keeps_pending_status_when_beats_are_missing(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    set_fake_text_test_hook(_incomplete_structure_hook(drop="beats"))
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.STRUCTURE_PENDING


def test_structure_update_keeps_pending_status_when_visual_intents_are_missing(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    set_fake_text_test_hook(_incomplete_structure_hook(drop="intents"))
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.STRUCTURE_PENDING


def test_structure_update_is_idempotent_and_creates_no_duplicate_structure_rows(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    before = _count_structure_rows(project)
    assert start_structure_run(project, sync=True).started
    after = _count_structure_rows(project)
    assert after["script_id"] == before["script_id"]
    assert after["script_version"] == before["script_version"]
    assert after["sentences"] == before["sentences"]
    assert after["beats"] == before["beats"]
    assert after["intents"] == before["intents"]
    assert after["status"] == ScriptDraftStatus.REVIEW_REQUESTED.value


def test_structure_update_does_not_create_new_script_version(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    before = get_editorial_view(project).script
    assert before is not None
    assert start_structure_run(project, sync=True).started
    after = get_editorial_view(project).script
    assert after is not None
    assert after.script_id == before.script_id
    assert after.script_version == before.script_version == 2


def test_structure_update_surfaces_failure_code_in_editorial_ui(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    set_fake_text_test_hook(_incomplete_structure_hook(drop="beats"))

    class _FakeSt:
        def __init__(self) -> None:
            self.messages: list[str] = []
            self.session_state: dict = {}
            self.buttons: list[str] = []

        def button(self, label, **kwargs):
            self.buttons.append(label)
            return label == "Struktur aktualisieren"

        def columns(self, n):
            return [self, self][:n]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def warning(self, text):
            self.messages.append(str(text))

        def info(self, text):
            self.messages.append(str(text))

        def success(self, text):
            self.messages.append(str(text))

        def title(self, text):
            self.messages.append(str(text))

        def write(self, text):
            self.messages.append(str(text))

        def markdown(self, text):
            self.messages.append(str(text))

        def caption(self, text):
            self.messages.append(str(text))

        def text_area(self, *args, **kwargs):
            return kwargs.get("value", "")

        def text_input(self, *args, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, *args, **kwargs):
            return kwargs.get("value", 0)

        def checkbox(self, *args, **kwargs):
            return False

        def dataframe(self, *args, **kwargs):
            return None

        def form(self, *args, **kwargs):
            return self

        def form_submit_button(self, *args, **kwargs):
            return False

        def container(self):
            return self

        def expander(self, *args, **kwargs):
            return self

        def rerun(self):
            return None

        def code(self, *args, **kwargs):
            if args:
                self.messages.append(str(args[0]))

        def subheader(self, text):
            self.messages.append(str(text))

    fake = _FakeSt()
    flashes: list[tuple[str, str]] = []

    def _flash(message, level="success"):
        flashes.append((str(message), str(level)))

    monkeypatch.setattr(editorial_page, "st", fake)
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(editorial_page, "_flash_and_rerun", _flash)
    # Avoid unrelated mutation paths during full page render.
    monkeypatch.setattr(
        editorial_page,
        "start_narrative_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    monkeypatch.setattr(
        editorial_page,
        "start_script_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    monkeypatch.setattr(
        editorial_page,
        "start_coverage_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    editorial_page.render_discovery_editorial_page()
    assert any(code == EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING for code, _ in flashes)
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.STRUCTURE_PENDING


def test_successful_structure_update_removes_script_structure_pending_preview_blocker(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    before = build_current_script_lock_preview(project)
    assert "script_structure_pending" in before.blockers
    assert start_structure_run(project, sync=True).started
    after = build_current_script_lock_preview(project)
    assert "script_structure_pending" not in after.blockers
    assert "Struktur aktuell" in after.fulfilled_requirements


def test_successful_structure_update_allows_lock_preview_fingerprint_when_other_requirements_are_complete(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert preview.ok is True
    assert preview.lock_fingerprint
    assert "script_structure_pending" not in preview.blockers


def test_structure_update_render_and_preview_are_read_only_without_button_click(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    before = _count_structure_rows(project)

    class _NoClickSt:
        def __init__(self) -> None:
            self.session_state: dict = {}

        def button(self, *args, **kwargs):
            return False

        def columns(self, n):
            return [self, self][:n]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def warning(self, *a, **k):
            return None

        def info(self, *a, **k):
            return None

        def success(self, *a, **k):
            return None

        def title(self, *a, **k):
            return None

        def write(self, *a, **k):
            return None

        def markdown(self, *a, **k):
            return None

        def caption(self, *a, **k):
            return None

        def text_area(self, *args, **kwargs):
            return kwargs.get("value", "")

        def text_input(self, *args, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, *args, **kwargs):
            return kwargs.get("value", 0)

        def checkbox(self, *args, **kwargs):
            return False

        def dataframe(self, *args, **kwargs):
            return None

        def form(self, *args, **kwargs):
            return self

        def form_submit_button(self, *args, **kwargs):
            return False

        def container(self):
            return self

        def expander(self, *args, **kwargs):
            return self

        def code(self, *a, **k):
            return None

        def subheader(self, *a, **k):
            return None

        def rerun(self):
            return None

    monkeypatch.setattr(editorial_page, "st", _NoClickSt())
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    called = {"structure": 0}

    def _guard(*args, **kwargs):
        called["structure"] += 1
        raise AssertionError("structure must not run without button click")

    monkeypatch.setattr(editorial_page, "start_structure_run", _guard)
    editorial_page.render_discovery_editorial_page()
    assert called["structure"] == 0
    assert _count_structure_rows(project) == before
    build_current_script_lock_preview(project)
    assert _count_structure_rows(project) == before


def test_schema20_fake_only_no_gateway_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    assert REGISTRY_SCHEMA_VERSION == "20"
    assert start_structure_run(project, sync=True).started
    conn = get_registry_connection(project.project_root_path)
    try:
        assert read_schema_version(conn) == "20"
    finally:
        conn.close()


def test_classic_without_vo_isolation(tmp_path: Path, temp_db_path: Path) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    for rel in (
        "otio_app/discovery_v2/jobs/editorial_worker.py",
        "otio_app/discovery_v2/application/editorial_service.py",
        "otio_app/discovery_v2/ui/editorial_page.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        assert "without_vo" not in source
        assert "classic_migration" not in source
    classic = Path(project.project_root_path) / "_otio"
    assert not classic.exists() or not any(classic.rglob("*"))
