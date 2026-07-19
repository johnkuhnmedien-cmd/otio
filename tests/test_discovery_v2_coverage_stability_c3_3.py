"""Coverage Stability C3.3 — Exact Gap Match Engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import (
    assert_schema_20,
    build_script_ready_project,
    install_no_media_io_guards,
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
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_identity_service import (
    GapSemanticIdentityResult,
    build_gap_semantic_identity,
)
from otio_app.discovery_v2.application.coverage_gap_matching_service import (
    build_match_candidate,
    run_exact_gap_match,
    verified_identity_from_result,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION as SCHEMA
from otio_app.discovery_v2.domain.coverage_gap_identity import (
    COVERAGE_GAP_SEMANTIC_KEY_COLLISION,
    COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
    compute_canonical_payload_sha256,
    compute_gap_semantic_key,
)
from otio_app.discovery_v2.domain.coverage_gap_matching import (
    COVERAGE_GAP_MATCH_AUDIT_MISMATCH,
    COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE,
    COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_MATCH_PROJECT_MISMATCH,
    COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION,
    COVERAGE_GAP_MATCH_SCHEMA_MISMATCH,
    CoverageGapMatchClass,
    CoverageGapMatchError,
    CoverageGapMatchRequest,
    VerifiedGapSemanticIdentity,
    compute_match_report_fingerprint,
    match_coverage_gaps,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageLevel,
    CoverageRiskFlag,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    REGISTRY_SCHEMA_VERSION,
    get_registry_connection,
    read_schema_version,
)


PROJECT = "proj-match"
SOURCE_AUDIT = "audit-source"
TARGET_AUDIT = "audit-target"


@pytest.fixture(autouse=True)
def _reset_launchers_and_hooks():
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()


def _identity(**overrides) -> GapSemanticIdentityResult:
    parts = {
        "project_id": PROJECT,
        "desired_motif": "historic marketplace",
        "action": "crowd walking",
        "setting": "outdoor square",
        "geographic_requirements": "Central Europe",
        "authenticity_requirements": ["period clothing"],
        "allowed_media_kinds": ["video"],
        "coverage_level": CoverageLevel.PARTIALLY_COVERED,
        "missing_properties": ["exact_match_not_verified"],
        "risk_codes": [CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value],
    }
    parts.update(overrides)
    return build_gap_semantic_identity(
        project_id=parts["project_id"],
        visual_intent={
            "desired_motif": parts["desired_motif"],
            "action": parts["action"],
            "setting": parts["setting"],
            "geographic_requirements": parts["geographic_requirements"],
            "authenticity_requirements": parts["authenticity_requirements"],
            "allowed_media_kinds": parts["allowed_media_kinds"],
        },
        coverage_level=parts["coverage_level"],
        missing_properties=parts["missing_properties"],
        risk_codes=parts["risk_codes"],
    )


def _candidate(
    gap_id: str,
    *,
    audit_id: str,
    identity: GapSemanticIdentityResult | None = None,
    project_id: str = PROJECT,
):
    result = identity or _identity()
    assert result.ok
    return build_match_candidate(
        project_id=project_id,
        coverage_audit_id=audit_id,
        gap_id=gap_id,
        semantic_identity_result=result,
    )


def _run(sources, targets):
    return run_exact_gap_match(
        project_id=PROJECT,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=sources,
        target_candidates=targets,
    )


# --- Core contract tests -------------------------------------------------


def test_c3_3_exact_one_to_one_is_only_carry_forward_eligible_class() -> None:
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert result.ok and result.report is not None
    groups = result.report.groups
    assert len(groups) == 1
    group = groups[0]
    assert group.match_class == CoverageGapMatchClass.EXACT_ONE_TO_ONE
    assert group.carry_forward_evaluation_allowed is True
    assert group.source_gap_ids == ["gap-s1"]
    assert group.target_gap_ids == ["gap-t1"]
    assert result.report.summary.carry_forward_evaluation_allowed_count == 1


def test_c3_3_one_to_many_is_ambiguous_and_not_eligible() -> None:
    identity = _identity()
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity)],
        [
            _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity),
            _candidate("gap-t2", audit_id=TARGET_AUDIT, identity=identity),
        ],
    )
    assert result.ok and result.report is not None
    group = result.report.groups[0]
    assert group.match_class == CoverageGapMatchClass.AMBIGUOUS_ONE_TO_MANY
    assert group.carry_forward_evaluation_allowed is False


def test_c3_3_many_to_one_is_ambiguous_and_not_eligible() -> None:
    identity = _identity()
    result = _run(
        [
            _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity),
            _candidate("gap-s2", audit_id=SOURCE_AUDIT, identity=identity),
        ],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity)],
    )
    assert result.ok and result.report is not None
    group = result.report.groups[0]
    assert group.match_class == CoverageGapMatchClass.AMBIGUOUS_MANY_TO_ONE
    assert group.carry_forward_evaluation_allowed is False


def test_c3_3_many_to_many_is_ambiguous_and_not_eligible() -> None:
    identity = _identity()
    result = _run(
        [
            _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity),
            _candidate("gap-s2", audit_id=SOURCE_AUDIT, identity=identity),
        ],
        [
            _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity),
            _candidate("gap-t2", audit_id=TARGET_AUDIT, identity=identity),
        ],
    )
    assert result.ok and result.report is not None
    group = result.report.groups[0]
    assert group.match_class == CoverageGapMatchClass.AMBIGUOUS_MANY_TO_MANY
    assert group.carry_forward_evaluation_allowed is False


def test_c3_3_unmatched_source_is_reported() -> None:
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=_identity(desired_motif="other"))],
    )
    assert result.ok and result.report is not None
    classes = {g.match_class for g in result.report.groups}
    assert CoverageGapMatchClass.UNMATCHED_SOURCE in classes
    assert "gap-s1" in result.report.unmatched_source_gap_ids


def test_c3_3_unmatched_target_is_reported() -> None:
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=_identity(desired_motif="alpha"))],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=_identity(desired_motif="beta"))],
    )
    assert result.ok and result.report is not None
    assert "gap-t1" in result.report.unmatched_target_gap_ids
    assert any(
        g.match_class == CoverageGapMatchClass.UNMATCHED_TARGET for g in result.report.groups
    )


def test_c3_3_input_order_does_not_change_report_or_fingerprint() -> None:
    id_a = _identity(desired_motif="motif-a")
    id_b = _identity(desired_motif="motif-b")
    sources_a = [
        _candidate("gap-s2", audit_id=SOURCE_AUDIT, identity=id_b),
        _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=id_a),
    ]
    targets_a = [
        _candidate("gap-t2", audit_id=TARGET_AUDIT, identity=id_b),
        _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=id_a),
    ]
    sources_b = list(reversed(sources_a))
    targets_b = list(reversed(targets_a))
    r1 = _run(sources_a, targets_a)
    r2 = _run(sources_b, targets_b)
    assert r1.ok and r2.ok
    assert r1.report is not None and r2.report is not None
    assert r1.report.report_fingerprint == r2.report.report_fingerprint
    assert r1.report.model_dump(mode="json") == r2.report.model_dump(mode="json")


def test_c3_3_same_key_and_different_payload_blocks_collision_cluster() -> None:
    left = _identity()
    right = _identity(setting="completely different setting")
    assert left.semantic_key != right.semantic_key

    forged = GapSemanticIdentityResult(
        ok=True,
        schema_version=right.schema_version,
        semantic_key=left.semantic_key,  # forged equal key
        canonical_identity=right.canonical_identity,
        canonical_payload_sha256=right.canonical_payload_sha256,
    )
    # verified_identity_from_result rejects inconsistent key/payload
    with pytest.raises(CoverageGapMatchError) as excinfo:
        verified_identity_from_result(forged)
    assert excinfo.value.error_code == COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE

    # Inject forged key/payload pair via model_construct (defense-in-depth path).
    v_left = verified_identity_from_result(left)
    v_right_payload = verified_identity_from_result(right)
    forged_verified = VerifiedGapSemanticIdentity.model_construct(
        schema_version=v_right_payload.schema_version,
        semantic_key=v_left.semantic_key,
        canonical_identity=v_right_payload.canonical_identity,
        canonical_payload_sha256=v_right_payload.canonical_payload_sha256,
    )
    from otio_app.discovery_v2.domain.coverage_gap_matching import CoverageGapMatchCandidate

    request = CoverageGapMatchRequest.model_construct(
        project_id=PROJECT,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=[
            CoverageGapMatchCandidate.model_construct(
                project_id=PROJECT,
                coverage_audit_id=SOURCE_AUDIT,
                gap_id="gap-s1",
                semantic_identity_result=v_left,
            )
        ],
        target_candidates=[
            CoverageGapMatchCandidate.model_construct(
                project_id=PROJECT,
                coverage_audit_id=TARGET_AUDIT,
                gap_id="gap-t1",
                semantic_identity_result=forged_verified,
            )
        ],
    )
    report = match_coverage_gaps(request)
    assert len(report.groups) == 1
    assert report.groups[0].match_class == CoverageGapMatchClass.BLOCKED_COLLISION
    assert report.groups[0].carry_forward_evaluation_allowed is False
    assert COVERAGE_GAP_SEMANTIC_KEY_COLLISION in report.groups[0].reason_codes


def test_c3_3_schema_mismatch_fails_closed() -> None:
    good = _identity()
    bad = GapSemanticIdentityResult(
        ok=True,
        schema_version="coverage-gap-semantic-key-v0",
        semantic_key=good.semantic_key,
        canonical_identity=good.canonical_identity,
        canonical_payload_sha256=good.canonical_payload_sha256,
    )
    with pytest.raises(CoverageGapMatchError) as excinfo:
        verified_identity_from_result(bad)
    assert excinfo.value.error_code == COVERAGE_GAP_MATCH_SCHEMA_MISMATCH


def test_c3_3_project_mismatch_fails_closed() -> None:
    result = run_exact_gap_match(
        project_id=PROJECT,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=[_candidate("gap-s1", audit_id=SOURCE_AUDIT, project_id="other")],
        target_candidates=[_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_PROJECT_MISMATCH


def test_c3_3_source_audit_mismatch_fails_closed() -> None:
    result = run_exact_gap_match(
        project_id=PROJECT,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=[_candidate("gap-s1", audit_id="wrong-audit")],
        target_candidates=[_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_AUDIT_MISMATCH


def test_c3_3_target_audit_mismatch_fails_closed() -> None:
    result = run_exact_gap_match(
        project_id=PROJECT,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=[_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        target_candidates=[_candidate("gap-t1", audit_id="wrong-audit")],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_AUDIT_MISMATCH


def test_c3_3_duplicate_source_gap_id_fails_closed() -> None:
    identity = _identity()
    result = _run(
        [
            _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity),
            _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity),
        ],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity)],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE


def test_c3_3_duplicate_target_gap_id_fails_closed() -> None:
    identity = _identity()
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity)],
        [
            _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity),
            _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity),
        ],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE


def test_c3_3_same_gap_cannot_be_source_and_target() -> None:
    identity = _identity()
    result = _run(
        [_candidate("gap-shared", audit_id=SOURCE_AUDIT, identity=identity)],
        [_candidate("gap-shared", audit_id=TARGET_AUDIT, identity=identity)],
    )
    assert not result.ok
    assert result.error_code == COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE


def test_c3_3_missing_identity_fails_closed() -> None:
    missing = GapSemanticIdentityResult(
        ok=False,
        message="missing",
        error_code="coverage_gap_semantic_identity_unavailable",
    )
    with pytest.raises(CoverageGapMatchError) as excinfo:
        build_match_candidate(
            project_id=PROJECT,
            coverage_audit_id=SOURCE_AUDIT,
            gap_id="gap-s1",
            semantic_identity_result=missing,
        )
    assert excinfo.value.error_code == COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE


def test_c3_3_risk_change_produces_unmatched_gaps_not_similarity_match() -> None:
    base = _identity(risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value])
    richer = _identity(
        risk_codes=[
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
            CoverageRiskFlag.POSSIBLE_SYNTHETIC_RISK.value,
        ]
    )
    assert base.semantic_key != richer.semantic_key
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=base)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=richer)],
    )
    assert result.ok and result.report is not None
    classes = {g.match_class for g in result.report.groups}
    assert CoverageGapMatchClass.EXACT_ONE_TO_ONE not in classes
    assert CoverageGapMatchClass.UNMATCHED_SOURCE in classes
    assert CoverageGapMatchClass.UNMATCHED_TARGET in classes
    assert result.report.summary.carry_forward_evaluation_allowed_count == 0


def test_c3_3_missing_property_change_produces_unmatched_gaps() -> None:
    a = _identity(missing_properties=["exact_match_not_verified"])
    b = _identity(missing_properties=["geographic_match_not_verified"])
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=a)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=b)],
    )
    assert result.ok and result.report is not None
    assert result.report.summary.exact_one_to_one_count == 0
    assert result.report.unmatched_source_gap_ids == ["gap-s1"]
    assert result.report.unmatched_target_gap_ids == ["gap-t1"]


def test_c3_3_visual_intent_change_produces_unmatched_gaps() -> None:
    a = _identity(desired_motif="historic marketplace")
    b = _identity(desired_motif="modern skyline")
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=a)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=b)],
    )
    assert result.ok and result.report is not None
    assert result.report.summary.exact_one_to_one_count == 0


def test_c3_3_exact_match_uses_full_payload_not_key_only() -> None:
    left = _identity()
    right = _identity(action="solo standing")
    # Same key alone is never enough — different payloads are different keys here.
    assert left.semantic_key != right.semantic_key
    # And when keys collide artificially, cluster is blocked (see collision test).
    v_left = verified_identity_from_result(left)
    assert v_left.semantic_key == compute_gap_semantic_key(left.canonical_identity)
    assert v_left.canonical_payload_sha256 == compute_canonical_payload_sha256(
        left.canonical_identity
    )


def test_c3_3_report_schema_and_fingerprint_are_stable() -> None:
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert result.ok and result.report is not None
    report = result.report
    assert report.schema_version == COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION
    payload = report.model_dump(mode="json")
    expected = compute_match_report_fingerprint(payload)
    assert report.report_fingerprint == expected
    assert len(report.report_fingerprint) == 64
    # Recompute via stable JSON contract.
    without_fp = dict(payload)
    without_fp.pop("report_fingerprint")
    digest = hashlib.sha256(
        json.dumps(
            without_fp,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert digest == report.report_fingerprint


def test_c3_3_engine_performs_no_repository_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    def _boom(*_a, **_k):  # noqa: ANN001
        writes.append("write")
        raise AssertionError("repository write attempted")

    monkeypatch.setattr(
        "otio_app.discovery_v2.persistence.supplementation_repository.save_coverage_gap",
        _boom,
        raising=False,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.persistence.editorial_repository.save_coverage_audit",
        _boom,
        raising=False,
    )
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert result.ok
    assert writes == []


def test_c3_3_engine_calls_no_gateway_and_reads_no_media(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import otio_app.discovery_v2.adapters.text_gateway as gateway_mod

    project = build_script_ready_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    calls: list[object] = []
    original = gateway_mod.DiscoveryTextGateway.generate

    def _wrapped(self, request):  # noqa: ANN001
        calls.append(request)
        return original(self, request)

    monkeypatch.setattr(gateway_mod.DiscoveryTextGateway, "generate", _wrapped)
    identity = _identity(project_id=project.id)
    result = run_exact_gap_match(
        project_id=project.id,
        source_audit_id=SOURCE_AUDIT,
        target_audit_id=TARGET_AUDIT,
        source_candidates=[
            build_match_candidate(
                project_id=project.id,
                coverage_audit_id=SOURCE_AUDIT,
                gap_id="gap-s1",
                semantic_identity_result=identity,
            )
        ],
        target_candidates=[
            build_match_candidate(
                project_id=project.id,
                coverage_audit_id=TARGET_AUDIT,
                gap_id="gap-t1",
                semantic_identity_result=identity,
            )
        ],
    )
    assert result.ok
    assert calls == []


def test_c3_3_schema20_classic_without_vo_isolation(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    with get_registry_connection(project.project_root_path) as conn:
        assert read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == SCHEMA == "20"
    import otio_app.discovery_v2.domain.coverage_gap_matching as domain_mod
    import otio_app.discovery_v2.application.coverage_gap_matching_service as app_mod

    src = Path(domain_mod.__file__).read_text(encoding="utf-8")
    src += Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "without_vo" not in src
    assert "cut_plan" not in src
    assert "otio_exporter" not in src
    assert "sqlite" not in src.lower()


# --- Smokes A–F ----------------------------------------------------------


def test_c3_3_smoke_a_exact_one_to_one() -> None:
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT)],
    )
    assert result.ok and result.report is not None
    group = result.report.groups[0]
    assert group.match_class == CoverageGapMatchClass.EXACT_ONE_TO_ONE
    assert group.carry_forward_evaluation_allowed is True


def test_c3_3_smoke_b_one_to_many_ambiguous() -> None:
    identity = _identity()
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity)],
        [
            _candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity),
            _candidate("gap-t2", audit_id=TARGET_AUDIT, identity=identity),
        ],
    )
    assert result.report is not None
    assert result.report.groups[0].match_class == CoverageGapMatchClass.AMBIGUOUS_ONE_TO_MANY
    assert result.report.groups[0].carry_forward_evaluation_allowed is False


def test_c3_3_smoke_c_many_to_one_ambiguous() -> None:
    identity = _identity()
    result = _run(
        [
            _candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=identity),
            _candidate("gap-s2", audit_id=SOURCE_AUDIT, identity=identity),
        ],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=identity)],
    )
    assert result.report is not None
    assert result.report.groups[0].match_class == CoverageGapMatchClass.AMBIGUOUS_MANY_TO_ONE
    assert result.report.groups[0].carry_forward_evaluation_allowed is False


def test_c3_3_smoke_d_new_risk_unmatched_no_similarity() -> None:
    base = _identity(risk_codes=[CoverageRiskFlag.TOO_GENERIC.value])
    richer = _identity(
        risk_codes=[
            CoverageRiskFlag.TOO_GENERIC.value,
            CoverageRiskFlag.POSSIBLE_SYNTHETIC_RISK.value,
        ]
    )
    result = _run(
        [_candidate("gap-s1", audit_id=SOURCE_AUDIT, identity=base)],
        [_candidate("gap-t1", audit_id=TARGET_AUDIT, identity=richer)],
    )
    assert result.report is not None
    assert result.report.summary.exact_one_to_one_count == 0
    assert len(result.report.unmatched_source_gap_ids) == 1
    assert len(result.report.unmatched_target_gap_ids) == 1


def test_c3_3_smoke_e_collision_blocked() -> None:
    left = _identity()
    right = _identity(setting="other-setting")
    forged = VerifiedGapSemanticIdentity.model_construct(
        schema_version=right.schema_version,
        semantic_key=left.semantic_key,
        canonical_identity=right.canonical_identity,
        canonical_payload_sha256=right.canonical_payload_sha256,
    )
    from otio_app.discovery_v2.domain.coverage_gap_matching import CoverageGapMatchCandidate

    report = match_coverage_gaps(
        CoverageGapMatchRequest.model_construct(
            project_id=PROJECT,
            source_audit_id=SOURCE_AUDIT,
            target_audit_id=TARGET_AUDIT,
            source_candidates=[
                CoverageGapMatchCandidate.model_construct(
                    project_id=PROJECT,
                    coverage_audit_id=SOURCE_AUDIT,
                    gap_id="gap-s1",
                    semantic_identity_result=verified_identity_from_result(left),
                )
            ],
            target_candidates=[
                CoverageGapMatchCandidate.model_construct(
                    project_id=PROJECT,
                    coverage_audit_id=TARGET_AUDIT,
                    gap_id="gap-t1",
                    semantic_identity_result=forged,
                )
            ],
        )
    )
    assert report.groups[0].match_class == CoverageGapMatchClass.BLOCKED_COLLISION


def test_c3_3_smoke_f_idempotent_fingerprint() -> None:
    id_a = _identity(desired_motif="a")
    id_b = _identity(desired_motif="b")
    r1 = _run(
        [
            _candidate("s1", audit_id=SOURCE_AUDIT, identity=id_a),
            _candidate("s2", audit_id=SOURCE_AUDIT, identity=id_b),
        ],
        [
            _candidate("t1", audit_id=TARGET_AUDIT, identity=id_a),
            _candidate("t2", audit_id=TARGET_AUDIT, identity=id_b),
        ],
    )
    r2 = _run(
        [
            _candidate("s2", audit_id=SOURCE_AUDIT, identity=id_b),
            _candidate("s1", audit_id=SOURCE_AUDIT, identity=id_a),
        ],
        [
            _candidate("t2", audit_id=TARGET_AUDIT, identity=id_b),
            _candidate("t1", audit_id=TARGET_AUDIT, identity=id_a),
        ],
    )
    assert r1.report is not None and r2.report is not None
    assert r1.report.report_fingerprint == r2.report.report_fingerprint
