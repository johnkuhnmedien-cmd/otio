"""Coverage Stability C3.1 — reproduce current gap identity boundaries (no fix)."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import (
    assert_schema_20,
    gaps_for_audit,
    list_all_gaps,
    load_audit,
)
from fixtures.coverage_stability_c3_1 import (
    RISK_CODE,
    assert_gap_id_is_uuid4_generator,
    assert_no_semantic_or_predecessor_fields,
    build_audit_pair_fixture,
    coarse_result_signature,
    install_no_media_io_guards,
    normalized_gap_semantics,
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
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION as SCHEMA
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    make_lock_risk_confirmation_key,
)
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    REGISTRY_SCHEMA_VERSION,
    get_registry_connection,
    read_schema_version,
)


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


@pytest.fixture
def audit_pair(tmp_path: Path, temp_db_path: Path):
    return build_audit_pair_fixture(tmp_path, temp_db_path)


def test_c3_1_new_audit_mints_new_uuid_gap_instance_for_same_semantic_problem(
    audit_pair,
) -> None:
    assert_gap_id_is_uuid4_generator()
    gap_a_id = audit_pair.gap_a.gap_id
    gap_b_id = audit_pair.gap_b_id
    assert gap_a_id != gap_b_id
    gap_a = next(gap for gap in audit_pair.gaps_a if gap.gap_id == gap_a_id)
    gap_b = next(gap for gap in audit_pair.gaps_b if gap.gap_id == gap_b_id)
    assert gap_a.coverage_audit_id == audit_pair.audit_a_id
    assert gap_b.coverage_audit_id == audit_pair.audit_b_id
    assert gap_a.visual_intent_id == gap_b.visual_intent_id
    assert gap_a.coverage_level == gap_b.coverage_level
    assert sorted(gap_a.missing_properties) == sorted(gap_b.missing_properties)
    # Same diagnostic intent/result semantics (test helper, not product key).
    assert audit_pair.gap_a.semantics.desired_motif == audit_pair.gap_b_semantics.desired_motif
    assert (
        audit_pair.gap_a.semantics.coverage_level
        == audit_pair.gap_b_semantics.coverage_level
    )
    assert audit_pair.fingerprint_a != audit_pair.fingerprint_b


def test_c3_1_gap_events_remain_bound_to_source_gap_id(audit_pair) -> None:
    conn = supp_repo.open_supplementation_registry(audit_pair.project.project_root_path)
    try:
        events_a = supp_repo.list_gap_events(conn, gap_id=audit_pair.gap_a.gap_id)
        events_b = supp_repo.list_gap_events(conn, gap_id=audit_pair.gap_b_id)
    finally:
        conn.close()
    assert events_a
    assert {event.event_id for event in events_a} >= set(audit_pair.gap_a.event_ids)
    assert {event.event_type.value for event in events_a} >= {"materialized", "escalated"}
    event_ids_b = {event.event_id for event in events_b}
    assert not set(audit_pair.gap_a.event_ids) & event_ids_b
    # Source gap remains historically present (superseded), events unchanged.
    all_gaps = list_all_gaps(audit_pair.project)
    source = next(gap for gap in all_gaps if gap.gap_id == audit_pair.gap_a.gap_id)
    assert source.status == CoverageGapStatus.SUPERSEDED


def test_c3_1_candidate_decisions_do_not_apply_to_successor_gap(audit_pair) -> None:
    conn = supp_repo.open_supplementation_registry(audit_pair.project.project_root_path)
    try:
        decisions_a = supp_repo.list_candidate_decisions(
            conn, gap_id=audit_pair.gap_a.gap_id
        )
        decisions_b = supp_repo.list_candidate_decisions(
            conn, gap_id=audit_pair.gap_b_id
        )
    finally:
        conn.close()
    assert decisions_a
    assert audit_pair.gap_a.candidate_decision_ids
    assert {item.decision_id for item in decisions_a} >= set(
        audit_pair.gap_a.candidate_decision_ids
    )
    assert decisions_b == []


def test_c3_1_accepted_unresolved_does_not_apply_to_successor_gap(audit_pair) -> None:
    gap_a = next(
        gap for gap in list_all_gaps(audit_pair.project) if gap.gap_id == audit_pair.gap_a.gap_id
    )
    assert gap_a.user_decision == "accepted_unresolved"
    assert gap_a.accepted_unresolved_risks
    # Fresh B instance (pre-terminalization snapshot) did not inherit A's decision.
    assert audit_pair.gap_b_initial_status != CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert audit_pair.gap_b_initial_user_decision != "accepted_unresolved"
    assert not audit_pair.gap_b_initial_accepted_risks
    gap_b = next(gap for gap in audit_pair.gaps_b if gap.gap_id == audit_pair.gap_b_id)
    assert gap_b.gap_id == audit_pair.gap_b_id
    assert gap_b.user_decision != "accepted_unresolved"


def test_c3_1_script_lock_risk_key_changes_with_new_gap_id(audit_pair) -> None:
    key_a = make_lock_risk_confirmation_key(audit_pair.gap_a.gap_id, RISK_CODE)
    key_b = make_lock_risk_confirmation_key(audit_pair.gap_b_id, RISK_CODE)
    assert key_a == audit_pair.gap_a.risk_key
    assert key_a != key_b
    assert key_a.startswith(f"{audit_pair.gap_a.gap_id}:")
    assert key_b.startswith(f"{audit_pair.gap_b_id}:")
    assert key_a.endswith(f":{RISK_CODE.value}")
    assert key_b.endswith(f":{RISK_CODE.value}")


def test_c3_1_old_lock_confirmation_does_not_confirm_new_gap_instance(
    audit_pair,
) -> None:
    assert audit_pair.lock_fingerprint_a
    assert audit_pair.lock_fingerprint_b
    assert audit_pair.lock_fingerprint_a != audit_pair.lock_fingerprint_b
    preview_b = preview_script_lock(audit_pair.project)
    # Old fingerprint must not create a lock on the new audit stand.
    stale = create_script_lock(
        audit_pair.project,
        user_confirmed=True,
        confirmed_fingerprint=audit_pair.lock_fingerprint_a,
        accepted_unresolved_risk_confirmations={
            audit_pair.gap_a.risk_key: True,
        },
    )
    assert not stale.ok
    assert stale.error_code in {
        "script_lock_fingerprint_mismatch",
        "script_lock_requirements_not_met",
    }
    # Old risk key is not among current accepted_open_risks for B.
    if preview_b.accepted_open_risks:
        assert audit_pair.gap_a.risk_key not in preview_b.accepted_open_risks


def test_c3_1_no_semantic_gap_key_or_predecessor_relation_is_persisted(
    audit_pair,
) -> None:
    for gap in audit_pair.gaps_a + audit_pair.gaps_b:
        assert_no_semantic_or_predecessor_fields(gap)
    audit_a = load_audit(audit_pair.project, audit_pair.audit_a_id)
    audit_b = load_audit(audit_pair.project, audit_pair.audit_b_id)
    for audit in (audit_a, audit_b):
        dumped = audit.model_dump(mode="json")
        assert "semantic_gap_key" not in dumped
        assert "predecessor_gap_id" not in dumped


def test_c3_1_one_to_many_fixture_is_ambiguous(audit_pair) -> None:
    shape = audit_pair.shapes["one_to_many"]
    assert shape.shape == "one_to_many"
    assert len(shape.source_gap_ids) == 1
    assert len(shape.target_gap_ids) >= 2
    # No persisted assignment from source to any single target.
    source = next(
        gap for gap in list_all_gaps(audit_pair.project) if gap.gap_id == shape.source_gap_ids[0]
    )
    assert_no_semantic_or_predecessor_fields(source)
    for target_id in shape.target_gap_ids:
        target = next(gap for gap in audit_pair.gaps_b if gap.gap_id == target_id)
        assert_no_semantic_or_predecessor_fields(target)
        from fixtures.coverage_stability_c3_1 import coarse_result_signature_dict

        assert coarse_result_signature_dict(
            normalized_gap_semantics(audit_pair.project, target)
        ) == shape.shared_coarse_signature


def test_c3_1_many_to_one_fixture_is_ambiguous(audit_pair) -> None:
    shape = audit_pair.shapes["many_to_one"]
    assert shape.shape == "many_to_one"
    assert len(shape.source_gap_ids) >= 2
    assert len(shape.target_gap_ids) == 1
    # Source decisions can conflict (accepted vs open/graphic) under one coarse signature.
    statuses = {
        next(
            gap for gap in list_all_gaps(audit_pair.project) if gap.gap_id == gap_id
        ).status
        for gap_id in shape.source_gap_ids
    }
    assert len(statuses) >= 1
    target = next(
        gap for gap in audit_pair.gaps_b if gap.gap_id == shape.target_gap_ids[0]
    )
    assert_no_semantic_or_predecessor_fields(target)


def test_c3_1_new_unmatched_gap_has_no_predecessor(audit_pair) -> None:
    shape = audit_pair.shapes["none"]
    assert shape.shape == "none"
    assert shape.source_gap_ids == []
    assert shape.target_gap_ids
    target = next(
        gap for gap in audit_pair.gaps_b if gap.gap_id == shape.target_gap_ids[0]
    )
    assert_no_semantic_or_predecessor_fields(target)
    dumped = target.model_dump(mode="json")
    assert dumped.get("predecessor_gap_id") is None
    assert "semantic_gap_key" not in dumped


def test_c3_1_reproduction_uses_schema20_fake_only_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert load_text_config().provider == "fake"
    pair = build_audit_pair_fixture(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(pair.project)
    conn = get_registry_connection(pair.project.project_root_path)
    try:
        assert read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == SCHEMA == "20"
    finally:
        conn.close()
    classic = Path(pair.project.project_root_path) / "_otio"
    assert not classic.exists() or not any(classic.rglob("*"))
    assert load_text_config().provider == "fake"
    # one-to-one shape documents comparable semantics without persisted link
    one = pair.shapes["one_to_one"]
    assert one.target_gap_ids
    assert one.source_gap_ids[0] != one.target_gap_ids[0]


def test_c3_1_one_to_one_fixture_has_no_persisted_link(audit_pair) -> None:
    shape = audit_pair.shapes["one_to_one"]
    assert shape.shape == "one_to_one"
    assert shape.source_gap_ids and shape.target_gap_ids
    source_id, target_id = shape.source_gap_ids[0], shape.target_gap_ids[0]
    assert source_id != target_id
    source = next(
        gap for gap in list_all_gaps(audit_pair.project) if gap.gap_id == source_id
    )
    target = next(gap for gap in audit_pair.gaps_b if gap.gap_id == target_id)
    assert source.visual_intent_id == target.visual_intent_id
    assert_no_semantic_or_predecessor_fields(source)
    assert_no_semantic_or_predecessor_fields(target)
