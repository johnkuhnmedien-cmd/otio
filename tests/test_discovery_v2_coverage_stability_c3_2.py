"""Coverage Stability C3.2 — Semantic Gap Identity (domain contract only)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import (
    assert_schema_20,
    build_script_ready_project,
    install_no_media_io_guards,
)
from fixtures.coverage_stability_c3_1 import (
    build_audit_pair_fixture,
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
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_identity_service import (
    GapSemanticIdentityResult,
    build_gap_semantic_identity,
    build_gap_semantic_identity_for_gap,
    compare_gap_semantic_identity_results,
    require_compatible_gap_semantic_identity_results,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION as SCHEMA
from otio_app.discovery_v2.domain.coverage_gap_identity import (
    COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID,
    COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_SEMANTIC_KEY_COLLISION,
    COVERAGE_GAP_SEMANTIC_KEY_PREFIX,
    COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
    CanonicalCoverageGapSemanticIdentity,
    CoverageGapIdentityError,
    build_canonical_gap_semantic_identity,
    compute_canonical_payload_sha256,
    compute_gap_semantic_key,
    normalize_semantic_text,
)
from otio_app.discovery_v2.domain.editorial import VisualIntent
from otio_app.discovery_v2.domain.supplementation import (
    CoverageLevel,
    CoverageRiskFlag,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    REGISTRY_SCHEMA_VERSION,
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.persistence.supplementation_repository import new_gap_id


KEY_RE = re.compile(
    rf"^{re.escape(COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION)}:[0-9a-f]{{64}}$"
)


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


def _base_parts(**overrides):
    parts = {
        "project_id": "proj-alpha",
        "desired_motif": "historic marketplace",
        "action": "crowd walking",
        "setting": "outdoor square",
        "geographic_requirements": "Central Europe",
        "authenticity_requirements": ["period clothing", "natural light"],
        "allowed_media_kinds": ["video", "image"],
        "coverage_level": CoverageLevel.PARTIALLY_COVERED,
        "missing_properties": ["exact_match_not_verified"],
        "risk_codes": [CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value],
    }
    parts.update(overrides)
    return parts


def _build(**overrides) -> GapSemanticIdentityResult:
    parts = _base_parts(**overrides)
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


def _identity(**overrides) -> CanonicalCoverageGapSemanticIdentity:
    return build_canonical_gap_semantic_identity(**_base_parts(**overrides))


# --- Core contract tests -------------------------------------------------


def test_c3_2_identical_gap_semantics_produce_identical_key() -> None:
    a = _build()
    b = _build()
    assert a.ok and b.ok
    assert a.semantic_key == b.semantic_key
    assert a.canonical_payload_sha256 == b.canonical_payload_sha256
    assert a.canonical_identity == b.canonical_identity


def test_c3_2_gap_id_does_not_affect_semantic_key() -> None:
    # gap_id is not an input to the builder; keys stay equal across instances.
    a = _build()
    b = _build()
    assert a.semantic_key == b.semantic_key
    assert new_gap_id() != new_gap_id()


def test_c3_2_coverage_audit_id_does_not_affect_semantic_key() -> None:
    a = _build()
    b = _build()
    payload = a.canonical_identity.model_dump(mode="json")
    assert "coverage_audit_id" not in json.dumps(payload)
    assert a.semantic_key == b.semantic_key


def test_c3_2_visual_intent_id_does_not_affect_semantic_key() -> None:
    intent_a = VisualIntent(
        visual_intent_id="vi-a",
        visual_beat_id="vb-1",
        desired_motif="historic marketplace",
        action="crowd walking",
        setting="outdoor square",
        geographic_requirements="Central Europe",
        authenticity_requirements=["period clothing", "natural light"],
        allowed_media_kinds=["video", "image"],
        priority=1,
    )
    intent_b = intent_a.model_copy(
        update={"visual_intent_id": "vi-b", "visual_beat_id": "vb-9", "priority": 99}
    )
    a = build_gap_semantic_identity(
        project_id="proj-alpha",
        visual_intent=intent_a,
        coverage_level=CoverageLevel.PARTIALLY_COVERED,
        missing_properties=["exact_match_not_verified"],
        risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED],
    )
    b = build_gap_semantic_identity(
        project_id="proj-alpha",
        visual_intent=intent_b,
        coverage_level=CoverageLevel.PARTIALLY_COVERED,
        missing_properties=["exact_match_not_verified"],
        risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED],
    )
    assert a.ok and b.ok
    assert a.semantic_key == b.semantic_key
    dumped = a.canonical_identity.model_dump(mode="json")
    assert "visual_intent_id" not in json.dumps(dumped)
    assert "priority" not in dumped["visual_intent"]


def test_c3_2_collection_order_does_not_affect_semantic_key() -> None:
    a = _build(
        authenticity_requirements=["natural light", "period clothing"],
        allowed_media_kinds=["image", "video"],
        missing_properties=["exact_match_not_verified", "local_asset_candidate"],
        risk_codes=[
            CoverageRiskFlag.TOO_GENERIC.value,
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
        ],
    )
    b = _build(
        authenticity_requirements=["period clothing", "natural light"],
        allowed_media_kinds=["video", "image"],
        missing_properties=["local_asset_candidate", "exact_match_not_verified"],
        risk_codes=[
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
            CoverageRiskFlag.TOO_GENERIC.value,
        ],
    )
    assert a.semantic_key == b.semantic_key


def test_c3_2_duplicate_set_values_do_not_affect_semantic_key() -> None:
    a = _build(
        authenticity_requirements=["period clothing", "period clothing", "natural light"],
        missing_properties=["exact_match_not_verified", "exact_match_not_verified"],
        risk_codes=[
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
        ],
    )
    b = _build(
        authenticity_requirements=["period clothing", "natural light"],
        missing_properties=["exact_match_not_verified"],
        risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value],
    )
    assert a.semantic_key == b.semantic_key


def test_c3_2_whitespace_is_normalized_conservatively() -> None:
    a = _build(desired_motif="  historic   marketplace  ", action="crowd\twalking")
    b = _build(desired_motif="historic marketplace", action="crowd walking")
    assert a.semantic_key == b.semantic_key
    # Case is preserved — fail-closed for case-only differences.
    c = _build(desired_motif="Historic Marketplace")
    assert c.ok
    assert c.semantic_key != b.semantic_key
    assert normalize_semantic_text("  a\n\tb  ") == "a b"


def test_c3_2_project_id_changes_semantic_key() -> None:
    assert _build(project_id="proj-a").semantic_key != _build(project_id="proj-b").semantic_key


def test_c3_2_motif_change_changes_semantic_key() -> None:
    assert (
        _build(desired_motif="historic marketplace").semantic_key
        != _build(desired_motif="modern skyline").semantic_key
    )


def test_c3_2_action_change_changes_semantic_key() -> None:
    assert (
        _build(action="crowd walking").semantic_key
        != _build(action="solo standing").semantic_key
    )


def test_c3_2_setting_change_changes_semantic_key() -> None:
    assert (
        _build(setting="outdoor square").semantic_key
        != _build(setting="indoor hall").semantic_key
    )


def test_c3_2_geographic_requirement_change_changes_semantic_key() -> None:
    assert (
        _build(geographic_requirements="Central Europe").semantic_key
        != _build(geographic_requirements="North America").semantic_key
    )


def test_c3_2_authenticity_requirement_change_changes_semantic_key() -> None:
    assert (
        _build(authenticity_requirements=["period clothing"]).semantic_key
        != _build(authenticity_requirements=["period clothing", "hand-held"]).semantic_key
    )


def test_c3_2_allowed_media_kind_change_changes_semantic_key() -> None:
    assert (
        _build(allowed_media_kinds=["video"]).semantic_key
        != _build(allowed_media_kinds=["image"]).semantic_key
    )


def test_c3_2_coverage_level_change_changes_semantic_key() -> None:
    assert (
        _build(coverage_level=CoverageLevel.PARTIALLY_COVERED).semantic_key
        != _build(coverage_level=CoverageLevel.NOT_COVERED).semantic_key
    )


def test_c3_2_missing_property_change_changes_semantic_key() -> None:
    assert (
        _build(missing_properties=["exact_match_not_verified"]).semantic_key
        != _build(missing_properties=["geographic_match_not_verified"]).semantic_key
    )


def test_c3_2_risk_set_change_changes_semantic_key() -> None:
    assert (
        _build(risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value]).semantic_key
        != _build(
            risk_codes=[
                CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
                CoverageRiskFlag.POSSIBLE_SYNTHETIC_RISK.value,
            ]
        ).semantic_key
    )


def test_c3_2_observation_fingerprint_is_not_part_of_semantic_key() -> None:
    identity = _identity()
    payload = identity.model_dump(mode="json")
    blob = json.dumps(payload, sort_keys=True)
    assert "observation" not in blob
    assert "fingerprint" not in blob


def test_c3_2_candidate_and_asset_ids_are_not_part_of_semantic_key() -> None:
    identity = _identity()
    blob = json.dumps(identity.model_dump(mode="json"), sort_keys=True)
    for token in (
        "candidate_asset_ids",
        "resolved_asset_id",
        "graphic_plan_id",
        "working_media",
    ):
        assert token not in blob


def test_c3_2_status_escalation_and_user_decision_are_not_part_of_key() -> None:
    identity = _identity()
    blob = json.dumps(identity.model_dump(mode="json"), sort_keys=True)
    for token in (
        "status",
        "escalation",
        "user_decision",
        "accepted_unresolved",
        "gap_id",
        "run_id",
    ):
        assert token not in blob


def test_c3_2_schema_identifier_and_key_format_are_stable() -> None:
    result = _build()
    assert result.ok
    assert result.schema_version == COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION
    assert result.semantic_key is not None
    assert KEY_RE.match(result.semantic_key)
    assert result.semantic_key.startswith(COVERAGE_GAP_SEMANTIC_KEY_PREFIX)
    assert result.canonical_payload_sha256 == result.semantic_key.split(":", 1)[1]
    # Central serialization contract (no Python repr).
    identity = result.canonical_identity
    assert identity is not None
    expected = hashlib.sha256(
        json.dumps(
            identity.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert result.canonical_payload_sha256 == expected
    assert compute_gap_semantic_key(identity) == result.semantic_key


def test_c3_2_invalid_identity_fails_closed() -> None:
    empty_motif = _build(desired_motif="   ")
    assert not empty_motif.ok
    assert empty_motif.error_code == COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID

    bad_level = build_gap_semantic_identity(
        project_id="proj-alpha",
        visual_intent={
            "desired_motif": "m",
            "action": "a",
            "setting": "s",
        },
        coverage_level="not-a-level",
    )
    assert not bad_level.ok
    assert bad_level.error_code == COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID

    bad_risk = _build(risk_codes=["not_a_real_risk"])
    assert not bad_risk.ok
    assert bad_risk.error_code == COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID

    missing_intent = build_gap_semantic_identity(
        project_id="proj-alpha",
        visual_intent=None,  # type: ignore[arg-type]
        coverage_level=CoverageLevel.NOT_COVERED,
    )
    assert not missing_intent.ok
    assert missing_intent.error_code == COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE


def test_c3_2_equal_key_with_different_payload_raises_collision() -> None:
    left = _build()
    right = _build(desired_motif="completely different motif")
    assert left.ok and right.ok
    assert left.semantic_key != right.semantic_key

    # Forge equal keys with different payloads (collision contract).
    forged_right = GapSemanticIdentityResult(
        ok=True,
        schema_version=right.schema_version,
        semantic_key=left.semantic_key,
        canonical_identity=right.canonical_identity,
        canonical_payload_sha256=right.canonical_payload_sha256,
    )
    compared = compare_gap_semantic_identity_results(left, forged_right)
    assert not compared.ok
    assert compared.error_code == COVERAGE_GAP_SEMANTIC_KEY_COLLISION

    with pytest.raises(CoverageGapIdentityError) as excinfo:
        require_compatible_gap_semantic_identity_results(left, forged_right)
    assert excinfo.value.error_code == COVERAGE_GAP_SEMANTIC_KEY_COLLISION


def test_c3_2_builder_reads_no_media_and_calls_no_gateway(
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
    result = _build(project_id=project.id)
    assert result.ok
    assert calls == []


def test_c3_2_schema20_classic_and_without_vo_isolation(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = build_script_ready_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    with get_registry_connection(project.project_root_path) as conn:
        assert read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == SCHEMA == "20"
    # Isolation: classic / without-vo modules are not imported by identity builders.
    import otio_app.discovery_v2.domain.coverage_gap_identity as domain_mod
    import otio_app.discovery_v2.application.coverage_gap_identity_service as app_mod

    src = Path(domain_mod.__file__).read_text(encoding="utf-8")
    src += Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "without_vo" not in src
    assert "cut_plan" not in src
    assert "otio_exporter" not in src


# --- Smokes A–E ----------------------------------------------------------


def test_c3_2_smoke_a_audit_pair_same_semantics_same_key(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke A: Audit A/Gap A and Audit B/Gap B → same semantic key."""

    pair = build_audit_pair_fixture(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert pair.audit_a_id != pair.audit_b_id
    assert pair.gap_a.gap_id != pair.gap_b_id

    gap_a = next(g for g in pair.gaps_a if g.gap_id == pair.gap_a.gap_id)
    gap_b = next(g for g in pair.gaps_b if g.gap_id == pair.gap_b_id)

    # Instance IDs differ; intent IDs may be shared — key must ignore both.
    assert gap_a.coverage_audit_id != gap_b.coverage_audit_id
    assert gap_a.gap_id != gap_b.gap_id

    key_a = build_gap_semantic_identity_for_gap(pair.project, gap_a)
    key_b = build_gap_semantic_identity_for_gap(pair.project, gap_b)
    assert key_a.ok and key_b.ok
    assert key_a.semantic_key == key_b.semantic_key

    # C3.1 helper overlap (diagnostic only) — product key adds project_id + text NFC.
    diag_a = normalized_gap_semantics(pair.project, gap_a)
    diag_b = normalized_gap_semantics(pair.project, gap_b)
    assert diag_a == diag_b


def test_c3_2_smoke_b_new_authenticity_risk_changes_key() -> None:
    """Smoke B: same intent + additional authenticity risk → different key."""

    base = _build(risk_codes=[CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value])
    richer = _build(
        risk_codes=[
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED.value,
            CoverageRiskFlag.POSSIBLE_SYNTHETIC_RISK.value,
        ]
    )
    assert base.ok and richer.ok
    assert base.semantic_key != richer.semantic_key


def test_c3_2_smoke_c_missing_property_change_changes_key() -> None:
    """Smoke C: exact_match_not_verified → geographic_match_not_verified."""

    a = _build(missing_properties=["exact_match_not_verified"])
    b = _build(missing_properties=["geographic_match_not_verified"])
    assert a.semantic_key != b.semantic_key


def test_c3_2_smoke_d_status_change_does_not_affect_key(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke D: accepted_unresolved / status do not change semantic key."""

    pair = build_audit_pair_fixture(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    gap_a = next(g for g in pair.gaps_a if g.gap_id == pair.gap_a.gap_id)
    assert pair.gap_a.accepted_unresolved

    from_accepted = build_gap_semantic_identity_for_gap(pair.project, gap_a)
    assert from_accepted.ok

    # Same coverage-problem semantics without status / user_decision inputs.
    from_parts = build_gap_semantic_identity(
        project_id=gap_a.project_id,
        visual_intent={
            "desired_motif": pair.gap_a.semantics.desired_motif,
            "action": pair.gap_a.semantics.action,
            "setting": pair.gap_a.semantics.setting,
            "geographic_requirements": pair.gap_a.semantics.geographic_requirements,
            "authenticity_requirements": list(pair.gap_a.semantics.authenticity_requirements),
            "allowed_media_kinds": list(pair.gap_a.semantics.allowed_media_kinds),
        },
        coverage_level=gap_a.coverage_level,
        missing_properties=gap_a.missing_properties,
        risk_codes=gap_a.risk_flags,
    )
    assert from_parts.ok
    assert from_parts.semantic_key == from_accepted.semantic_key
    blob = json.dumps(from_accepted.canonical_identity.model_dump(mode="json"))
    assert "accepted_unresolved" not in blob
    assert "user_decision" not in blob
    assert "status" not in blob


def test_c3_2_smoke_e_collision_protection() -> None:
    """Smoke E: equal forged digest + different payload → collision."""

    left = _build()
    right = _build(setting="totally other setting")
    forged = GapSemanticIdentityResult(
        ok=True,
        schema_version=right.schema_version,
        semantic_key=left.semantic_key,
        canonical_identity=right.canonical_identity,
        canonical_payload_sha256=compute_canonical_payload_sha256(right.canonical_identity),
    )
    with pytest.raises(CoverageGapIdentityError) as excinfo:
        require_compatible_gap_semantic_identity_results(left, forged)
    assert excinfo.value.error_code == COVERAGE_GAP_SEMANTIC_KEY_COLLISION
