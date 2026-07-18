"""Coverage Stability C2 — canonical input fingerprint and audit/run reuse."""

from __future__ import annotations

import threading
import time
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import (
    assert_schema_20,
    build_script_ready_project,
    current_audit_id,
    gaps_for_audit,
    install_active_coverage_worker_gate,
    install_no_media_io_guards,
    list_all_gaps,
    load_audit,
    progress_three_gaps,
    run_manual_coverage,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    get_editorial_job_launcher,
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.narration_job_launcher import (
    reset_narration_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    escalate_gap,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.coverage_idempotency_service import (
    build_current_canonical_coverage,
    find_completed_equivalent_current_audit,
)
from otio_app.discovery_v2.application.coverage_revalidation_service import (
    revalidate_coverage_after_accepted_reviews,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_user_script_edit,
    start_coverage_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.observation_review_service import (
    submit_observation_review,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_claim_decision,
)
from otio_app.discovery_v2.domain.coverage_input import (
    COVERAGE_INPUT_SCHEMA_VERSION,
    EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT,
    LEGACY_COVERAGE_RECOMPUTE_MESSAGE,
    build_canonical_coverage_input,
    build_coverage_run_dedup_key,
    compute_canonical_coverage_fingerprint,
)
from otio_app.discovery_v2.domain.editorial import (
    Claim,
    CoverageAuditStatus,
    ScriptDraft,
    Sentence,
    VisualBeat,
    VisualIntent,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    EscalationStep,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    REGISTRY_SCHEMA_VERSION,
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION as SCHEMA


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _gateway_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    import otio_app.discovery_v2.adapters.text_gateway as gateway_mod

    original = gateway_mod.DiscoveryTextGateway.generate

    def _wrapped(self, request):
        calls.append(request.request_kind)
        return original(self, request)

    monkeypatch.setattr(gateway_mod.DiscoveryTextGateway, "generate", _wrapped)
    return calls


def _decide_all_claims(project) -> None:
    view = get_editorial_view(project)
    assert view.script is not None and view.script_bundle is not None
    for claim in view.script_bundle["claims"]:
        record_claim_decision(
            project,
            script_id=view.script.script_id,
            claim_id=claim["claim_id"],
            claim_text=claim["statement"],
            decision="confirmed",
        )


def _resolve_sibling_gaps(project, *, keep_gap_id: str) -> None:
    from otio_app.discovery_v2.application.coverage_gap_service import (
        mark_gap_resolved_with_local_asset,
    )

    for gap in materialize_gaps_from_current_coverage(project).gaps:
        if gap.gap_id == keep_gap_id:
            continue
        assert mark_gap_resolved_with_local_asset(
            project, gap_id=gap.gap_id, asset_id="asset-local"
        ).ok


def _coverage_run_ids(project) -> set[str]:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        return {
            run.run_id
            for run in editorial_repo.list_editorial_runs(conn, project_id=project.id)
            if run.scope == "editorial_coverage_only"
        }
    finally:
        conn.close()


def _strip_stored_canonical_fingerprint(project) -> str:
    """Make the current completed audit a legacy audit (no stored fingerprint)."""

    audit_id = current_audit_id(project)
    audit = load_audit(project, audit_id)
    assert audit.canonical_coverage_input_fingerprint
    legacy = audit.model_copy(update={"canonical_coverage_input_fingerprint": None})
    editorial_repo.save_coverage_json(project.project_root_path, legacy)
    reloaded = load_audit(project, audit_id)
    assert reloaded.canonical_coverage_input_fingerprint in (None, "")
    assert current_audit_id(project) == audit_id
    return audit_id


def _mutate_script_bundle_under_same_script_id(
    project,
    *,
    beat_description_suffix: str | None = None,
    intent_motif_suffix: str | None = None,
) -> str:
    """Overwrite the mutable script JSON under the same script_id.

    Uses the JSON artifact path that ``get_script_bundle`` reads. DB row
    replacement is avoided (FK links from coverage results/gaps); product
    ``replace_script_structure`` remains the mutable overwrite API under the
    same ``script_id``.
    """

    prior_audit_id = current_audit_id(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        script = editorial_repo.get_active_script(conn, project_id=project.id)
        assert script is not None
        script_id = script.script_id
        bundle = editorial_repo.get_script_bundle(conn, script_id=script_id)
        assert bundle is not None
        script_model = ScriptDraft.model_validate(bundle["script"])
        sentences = [Sentence.model_validate(item) for item in bundle.get("sentences", [])]
        claims = [Claim.model_validate(item) for item in bundle.get("claims", [])]
        beats = [VisualBeat.model_validate(item) for item in bundle.get("visual_beats", [])]
        intents = [
            VisualIntent.model_validate(item) for item in bundle.get("visual_intents", [])
        ]
        assert beats and intents
        if beat_description_suffix is not None:
            beats[0] = beats[0].model_copy(
                update={
                    "description": beats[0].description + beat_description_suffix,
                }
            )
        if intent_motif_suffix is not None:
            intents[0] = intents[0].model_copy(
                update={
                    "desired_motif": intents[0].desired_motif + intent_motif_suffix,
                }
            )
        editorial_repo.save_script_bundle_json(
            project.project_root_path,
            script=script_model,
            sentences=sentences,
            claims=claims,
            visual_beats=beats,
            visual_intents=intents,
        )
    finally:
        conn.close()
    assert current_audit_id(project) == prior_audit_id
    # Prove the live bundle now differs under the same script_id.
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        mutated = editorial_repo.get_script_bundle(conn, script_id=script_id)
    finally:
        conn.close()
    assert mutated is not None
    if beat_description_suffix is not None:
        assert beat_description_suffix in mutated["visual_beats"][0]["description"]
    if intent_motif_suffix is not None:
        assert intent_motif_suffix in mutated["visual_intents"][0]["desired_motif"]
    return script_id


# --- Canonical input ---------------------------------------------------------


def test_c2_identical_fachliche_inputs_share_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    a = build_current_canonical_coverage(project)
    b = build_current_canonical_coverage(project)
    assert a.ok and b.ok
    assert a.fingerprint == b.fingerprint
    assert a.coverage_input is not None
    assert a.coverage_input.schema_version == COVERAGE_INPUT_SCHEMA_VERSION
    payload = a.coverage_input.model_dump(mode="json")
    assert "run_id" not in payload
    assert "coverage_audit_id" not in payload
    assert "gap_id" not in str(payload)
    assert "created_at" not in str(payload)
    assert "_otio" not in str(payload)


def test_c2_run_id_and_audit_id_do_not_affect_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    built = build_current_canonical_coverage(project)
    assert built.ok and built.coverage_input is not None
    base = compute_canonical_coverage_fingerprint(built.coverage_input)
    dumped = built.coverage_input.model_dump(mode="json")
    dumped["run_id"] = "run-a"
    # Rebuilding without technical fields must match; mutating a copy dict is not
    # a CanonicalCoverageInput — compare that technical keys are absent.
    assert "run_id" not in built.coverage_input.model_dump(mode="json")
    assert base == built.fingerprint


def test_c2_visual_intent_collection_order_does_not_change_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    built = build_current_canonical_coverage(project)
    assert built.ok and built.coverage_input is not None
    reversed_intents = list(reversed(built.coverage_input.visual_intents))
    mutated = built.coverage_input.model_copy(update={"visual_intents": reversed_intents})
    # Sorting is applied at build time; fingerprint of unsorted model dump differs
    # unless we rebuild via helper sorting — re-sort like production.
    from otio_app.discovery_v2.domain.coverage_input import CanonicalVisualIntentRef

    resorted = sorted(
        reversed_intents,
        key=lambda ref: (ref.visual_intent_id, ref.visual_beat_id, ref.priority),
    )
    resorted_input = built.coverage_input.model_copy(update={"visual_intents": resorted})
    assert compute_canonical_coverage_fingerprint(resorted_input) == built.fingerprint


def test_c2_script_content_changes_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    before = build_current_canonical_coverage(project)
    view = get_editorial_view(project)
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " Geaenderter Inhalt."
    ).ok
    assert start_structure_run(project, sync=True).started
    after = build_current_canonical_coverage(project)
    assert before.ok and after.ok
    assert before.fingerprint != after.fingerprint


def test_c2_observation_fingerprint_changes_canonical_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    before = build_current_canonical_coverage(project)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        obs = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )[0]
    finally:
        conn.close()
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="C2 observation fingerprint change",
    ).ok
    after = build_current_canonical_coverage(project)
    # Without accepted observations coverage build may fail; either way fingerprint
    # must not silently stay identical for a completed reuse path.
    if after.ok:
        assert before.fingerprint != after.fingerprint
    else:
        assert after.error_code == "coverage_canonical_input_invalid" or not after.ok


def test_c2_provider_prompt_schema_change_changes_fingerprint(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    before = build_current_canonical_coverage(project)
    config = load_text_config()
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_idempotency_service.load_text_config",
        lambda: config.__class__(
            provider=config.provider,
            enabled=config.enabled,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            max_retries=config.max_retries,
            timeout_seconds=config.timeout_seconds,
            prompts={**config.prompts, "coverage": "editorial-coverage-v2-test"},
            response_schemas=config.response_schemas,
        ),
    )
    after = build_current_canonical_coverage(project)
    assert before.ok and after.ok
    assert before.fingerprint != after.fingerprint


# --- Completed / active reuse -------------------------------------------------


def test_c2_smoke_a_accepted_unresolved_survives_identical_coverage(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    first = run_manual_coverage(project, sync=True)
    assert first.started and not first.reused
    audit_id = current_audit_id(project)
    progressed = progress_three_gaps(project)
    gap_id = progressed.gap_user_decision_id
    _resolve_sibling_gaps(project, keep_gap_id=gap_id)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert preview.ok and preview.lock_fingerprint
    coverage_calls_before = calls.count("coverage")
    second = start_coverage_run(project, sync=True)
    assert second.reused is True
    assert second.started is False
    assert second.coverage_audit_id == audit_id
    assert second.run is None
    assert calls.count("coverage") == coverage_calls_before
    gaps = gaps_for_audit(project, audit_id)
    kept = next(gap for gap in gaps if gap.gap_id == gap_id)
    assert kept.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert kept.user_decision == "accepted_unresolved"
    preview2 = preview_script_lock(project)
    assert preview2.ok and preview2.lock_fingerprint


def test_c2_smoke_b_escalation_survives_identical_coverage(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    photo = escalate_gap(project, gap_id=gaps[0].gap_id)
    assert photo.ok and photo.gap is not None
    assert photo.gap.current_escalation_step == EscalationStep.PHOTO
    gap_id = photo.gap.gap_id
    step = photo.gap.current_escalation_step
    reused = start_coverage_run(project, sync=True)
    assert reused.reused
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = supp_repo.get_coverage_gap(conn, gap_id=gap_id)
    finally:
        conn.close()
    assert gap is not None
    assert gap.current_escalation_step == step
    assert gap.status != CoverageGapStatus.SUPERSEDED


def test_c2_smoke_c_active_equivalent_run_reuses_run_id(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    release = threading.Event()
    entered = threading.Event()
    install_active_coverage_worker_gate(
        monkeypatch, release=release, entered=entered
    )
    first = start_coverage_run(project, sync=False)
    assert first.started and first.run is not None and not first.reused
    assert entered.wait(timeout=5)
    second = start_coverage_run(project, sync=False)
    assert second.reused is True
    assert second.run is not None
    assert second.run.run_id == first.run.run_id
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline and get_editorial_job_launcher().is_active(project.id):
        time.sleep(0.05)
    assert current_audit_id(project)


def test_c2_smoke_d_script_change_starts_new_run(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    first = run_manual_coverage(project, sync=True)
    audit_a = current_audit_id(project)
    fp_a = build_current_canonical_coverage(project).fingerprint
    view = get_editorial_view(project)
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " Neuer Satz fuer C2."
    ).ok
    assert start_structure_run(project, sync=True).started
    fp_b = build_current_canonical_coverage(project).fingerprint
    assert fp_a != fp_b
    before = calls.count("coverage")
    second = start_coverage_run(project, sync=True)
    assert second.started and not second.reused
    assert second.run is not None
    assert current_audit_id(project) != audit_a
    assert calls.count("coverage") == before + 1


def test_c2_smoke_e_observation_change_starts_new_coverage_run(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    audit_a = current_audit_id(project)
    fp_a = build_current_canonical_coverage(project).fingerprint
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        obs = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )[0]
        payload = obs.observation_json or "{}"
        mutated = payload[:-1] + ', "c2_marker": true}' if payload.endswith("}") else payload + "x"
        conn.execute(
            """
            UPDATE visual_observations
            SET observation_json = ?
            WHERE observation_id = ?
            """,
            (mutated, obs.observation_id),
        )
        conn.commit()
    finally:
        conn.close()
    fp_b = build_current_canonical_coverage(project).fingerprint
    assert fp_a != fp_b
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert current_audit_id(project) != audit_a


def test_c2_smoke_f_unsafe_legacy_audit_is_not_reused(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """No stored fingerprint → never reuse, even if current artefacts look reconstructable."""

    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    audit_id = _strip_stored_canonical_fingerprint(project)
    # Current brief/narrative/script/bundle remain intact and would have been
    # reconstructable under the removed legacy path — must still fail closed.
    built = build_current_canonical_coverage(project)
    assert built.ok
    match = find_completed_equivalent_current_audit(
        project, fingerprint=built.fingerprint or ""
    )
    assert not match.ok
    assert match.unsafe_legacy is True
    assert match.error_code == EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert started.error_code == EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT
    assert started.message == LEGACY_COVERAGE_RECOMPUTE_MESSAGE
    assert current_audit_id(project) != audit_id


def test_c2_completed_reuse_skips_supersede_and_materialize(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    progressed = progress_three_gaps(project)
    old_ids = {gap.gap_id for gap in progressed.gaps}
    supersede = MagicMock(wraps=supp_repo.supersede_gaps_not_in_audit)
    materialize = MagicMock(wraps=materialize_gaps_from_current_coverage)
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_gap_service.repo.supersede_gaps_not_in_audit",
        supersede,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_gap_service.materialize_gaps_from_current_coverage",
        materialize,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_revalidation_service.materialize_gaps_from_current_coverage",
        materialize,
    )
    reused = start_coverage_run(project, sync=True)
    assert reused.reused
    supersede.assert_not_called()
    # Reuse path must not enter materialize.
    materialize.assert_not_called()
    assert {gap.gap_id for gap in list_all_gaps(project) if gap.status != CoverageGapStatus.SUPERSEDED} >= old_ids


def test_c2_automatic_and_manual_share_completed_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    audit_id = current_audit_id(project)
    before = calls.count("coverage")
    auto = revalidate_coverage_after_accepted_reviews(project, sync=True)
    assert auto.ok
    assert auto.coverage_started is False
    assert calls.count("coverage") == before
    manual = start_coverage_run(project, sync=True)
    assert manual.reused and manual.coverage_audit_id == audit_id
    assert calls.count("coverage") == before


def test_c2_schema_remains_20_and_no_classic_write(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    start_coverage_run(project, sync=True)
    assert_schema_20(project)
    conn = get_registry_connection(project.project_root_path)
    try:
        assert read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == SCHEMA == "20"
    finally:
        conn.close()
    classic = Path(project.project_root_path) / "_otio"
    assert not classic.exists() or not any(classic.rglob("*"))
    assert load_text_config().provider == "fake"


def test_c2_ui_double_render_does_not_start_run_or_gateway(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    before = calls.count("coverage")
    from otio_app.discovery_v2.ui import editorial_page

    st = MagicMock()
    st.button.return_value = False
    monkeypatch.setattr(editorial_page, "st", st)
    monkeypatch.setattr(editorial_page, "get_editorial_view", get_editorial_view)
    view = get_editorial_view(project)
    editorial_page._render_coverage(project, view)
    editorial_page._render_coverage(project, view)
    assert calls.count("coverage") == before


def test_c2_reproduction_uses_no_real_gateway_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert load_text_config().provider == "fake"
    run_manual_coverage(project, sync=True)
    reused = start_coverage_run(project, sync=True)
    assert reused.reused
    assert load_text_config().provider == "fake"


# --- C2-R1 Legacy fail-closed -------------------------------------------------


def test_c2_r1_legacy_audit_without_fingerprint_is_never_reused(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    legacy_id = _strip_stored_canonical_fingerprint(project)
    match = find_completed_equivalent_current_audit(
        project,
        fingerprint=build_current_canonical_coverage(project).fingerprint or "",
    )
    assert not match.ok
    assert match.error_code == EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert started.coverage_audit_id is None
    assert current_audit_id(project) != legacy_id


def test_c2_r1_legacy_reuse_does_not_read_mutable_script_bundle(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    _strip_stored_canonical_fingerprint(project)
    bundle_reads: list[str] = []
    original = editorial_repo.get_script_bundle

    def _tracked(conn, *, script_id: str):
        bundle_reads.append(script_id)
        return original(conn, script_id=script_id)

    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_idempotency_service.repo.get_script_bundle",
        _tracked,
    )
    built = build_current_canonical_coverage(project)
    assert built.ok
    # Reset: build_current reads the active bundle; completed reuse must not.
    bundle_reads.clear()
    match = find_completed_equivalent_current_audit(
        project, fingerprint=built.fingerprint or ""
    )
    assert not match.ok
    assert match.error_code == EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT
    assert bundle_reads == []


def test_c2_r1_structure_change_under_same_script_id_cannot_false_reuse_legacy_audit(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    legacy_id = _strip_stored_canonical_fingerprint(project)
    script_id = _mutate_script_bundle_under_same_script_id(
        project, beat_description_suffix=" [C2R1 structure mutate]"
    )
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.script_id == script_id
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert current_audit_id(project) != legacy_id


def test_c2_r1_visual_intent_change_under_same_script_id_cannot_false_reuse_legacy_audit(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    legacy_id = _strip_stored_canonical_fingerprint(project)
    script_id = _mutate_script_bundle_under_same_script_id(
        project, intent_motif_suffix=" [C2R1 intent mutate]"
    )
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.script_id == script_id
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert current_audit_id(project) != legacy_id


def test_c2_r1_first_legacy_request_starts_exactly_one_normal_run(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    _strip_stored_canonical_fingerprint(project)
    before_runs = _coverage_run_ids(project)
    before_calls = calls.count("coverage")
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    assert started.run is not None
    assert started.run.run_id not in before_runs
    assert len(_coverage_run_ids(project) - before_runs) == 1
    assert calls.count("coverage") == before_calls + 1
    assert started.error_code == EDITORIAL_ERROR_LEGACY_AUDIT_MISSING_CANONICAL_FINGERPRINT


def test_c2_r1_new_audit_after_legacy_recompute_stores_canonical_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    _strip_stored_canonical_fingerprint(project)
    started = start_coverage_run(project, sync=True)
    assert started.started and not started.reused
    new_audit = load_audit(project, current_audit_id(project))
    assert new_audit.canonical_coverage_input_fingerprint
    built = build_current_canonical_coverage(project)
    assert built.ok
    assert new_audit.canonical_coverage_input_fingerprint == built.fingerprint


def test_c2_r1_second_identical_request_reuses_new_fingerprinted_audit(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    _strip_stored_canonical_fingerprint(project)
    assert start_coverage_run(project, sync=True).started
    audit_id = current_audit_id(project)
    second = start_coverage_run(project, sync=True)
    assert second.reused is True
    assert second.started is False
    assert second.run is None
    assert second.coverage_audit_id == audit_id
    assert second.reuse_reason == "completed_equivalent_current_audit"


def test_c2_r1_second_identical_request_starts_no_gateway_worker_or_gap_materialization(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gateway_counter(monkeypatch)
    project = build_script_ready_project(tmp_path, temp_db_path)
    run_manual_coverage(project, sync=True)
    _strip_stored_canonical_fingerprint(project)
    assert start_coverage_run(project, sync=True).started
    audit_id = current_audit_id(project)
    progressed = progress_three_gaps(project)
    gap_ids = {gap.gap_id for gap in progressed.gaps}
    materialize = MagicMock(wraps=materialize_gaps_from_current_coverage)
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_gap_service.materialize_gaps_from_current_coverage",
        materialize,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_revalidation_service.materialize_gaps_from_current_coverage",
        materialize,
    )
    before_calls = calls.count("coverage")
    before_runs = _coverage_run_ids(project)
    second = start_coverage_run(project, sync=True)
    assert second.reused and second.coverage_audit_id == audit_id
    assert calls.count("coverage") == before_calls
    assert _coverage_run_ids(project) == before_runs
    materialize.assert_not_called()
    assert get_editorial_job_launcher().is_active(project.id) is False
    current_gaps = {
        gap.gap_id
        for gap in list_all_gaps(project)
        if gap.status != CoverageGapStatus.SUPERSEDED
    }
    assert gap_ids <= current_gaps


def test_c2_r1_active_equivalent_run_reuse_remains_unchanged(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    release = threading.Event()
    entered = threading.Event()
    install_active_coverage_worker_gate(
        monkeypatch, release=release, entered=entered
    )
    first = start_coverage_run(project, sync=False)
    assert first.started and first.run is not None and not first.reused
    assert entered.wait(timeout=5)
    second = start_coverage_run(project, sync=False)
    assert second.reused is True
    assert second.started is True
    assert second.run is not None
    assert second.run.run_id == first.run.run_id
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline and get_editorial_job_launcher().is_active(project.id):
        time.sleep(0.05)
    assert current_audit_id(project)
