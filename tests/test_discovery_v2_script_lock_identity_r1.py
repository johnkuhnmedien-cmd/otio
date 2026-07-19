"""R1.1 Script-Lock identity rework: gap_id risk keys + fingerprint preview."""

from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import get_editorial_view
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    get_effective_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_claim_decision,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    CoverageRiskFlag,
    EscalationStep,
    ScriptLockStatus,
    make_lock_risk_confirmation_key,
    parse_lock_risk_confirmation_key,
    persisted_accepted_lock_risk_keys,
)
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.ui import editorial_page as editorial_ui

from test_discovery_v2_script_lock import (  # noqa: PLC2701
    _decide_all_claims,
    _script_coverage_project,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _force_three_accepted_unresolved_gaps(project) -> list:
    """Build the real Alpha blocker: 3 accepted_unresolved, gap_id != visual_intent_id."""
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert len(gaps) >= 1
    # Ensure we have exactly three terminal accepted gaps with distinct IDs.
    while len(gaps) < 3:
        # Clone first gap row with new ids when Fake coverage yields fewer gaps.
        base = gaps[0]
        conn = repo.open_supplementation_registry(project.project_root_path)
        try:
            clone = base.model_copy(
                update={
                    "gap_id": repo.new_gap_id(),
                    "visual_intent_id": f"intent-clone-{len(gaps)}-{base.visual_intent_id}",
                    "status": CoverageGapStatus.OPEN,
                    "gap_version": 1,
                }
            )
            relative = repo.save_coverage_gap_json(project.project_root_path, clone)
            repo.insert_coverage_gap(conn, clone, relative)
            conn.commit()
        finally:
            conn.close()
        gaps = materialize_gaps_from_current_coverage(project).gaps

    selected = sorted(gaps, key=lambda item: item.gap_id)[:3]
    for index, gap in enumerate(selected):
        forced_intent = f"visual-intent-forced-{index:02d}-{gap.visual_intent_id}"
        assert forced_intent != gap.gap_id
        conn = repo.open_supplementation_registry(project.project_root_path)
        try:
            updated = gap.model_copy(
                update={
                    "visual_intent_id": forced_intent,
                    "risk_flags": [CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED],
                    "current_escalation_step": EscalationStep.USER_DECISION,
                    "status": CoverageGapStatus.IN_PROGRESS,
                    "missing_properties": ["exact_match_not_verified"],
                }
            )
            repo.update_coverage_gap(conn, updated)
            conn.commit()
        finally:
            conn.close()
        accepted = accept_gap_unresolved(
            project,
            gap_id=gap.gap_id,
            confirmed_risks=["coverage_exact_match_not_verified"],
            user_confirmed=True,
        )
        assert accepted.ok and accepted.gap is not None
        assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
        assert accepted.gap.gap_id != accepted.gap.visual_intent_id

    # Resolve any leftover non-selected open gaps so open_gap_count == 0.
    remaining = materialize_gaps_from_current_coverage(project).gaps
    selected_ids = {gap.gap_id for gap in selected}
    for gap in remaining:
        if gap.gap_id in selected_ids:
            continue
        if gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED:
            continue
        from otio_app.discovery_v2.application.coverage_gap_service import (
            mark_gap_resolved_with_local_asset,
        )

        mark_gap_resolved_with_local_asset(
            project, gap_id=gap.gap_id, asset_id="asset-local"
        )

    _decide_all_claims(project)
    final = [
        gap
        for gap in materialize_gaps_from_current_coverage(project).gaps
        if gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    ]
    assert len(final) >= 3
    for gap in final:
        assert gap.gap_id != gap.visual_intent_id
        assert gap.accepted_unresolved_risks == [
            CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
        ]
    return sorted(final, key=lambda item: item.gap_id)[:3]


def _risk_keys(gaps) -> list[str]:
    return [
        make_lock_risk_confirmation_key(
            gap.gap_id, CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
        )
        for gap in gaps
    ]


# --- Smoke A -----------------------------------------------------------------


def test_smoke_a_three_accepted_unresolved_fingerprint_then_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    keys = _risk_keys(gaps)
    preview = preview_script_lock(project)
    assert preview.ok, preview.blockers
    assert preview.lock_fingerprint
    assert preview.fingerprint_display
    assert not preview.can_lock
    assert sorted(preview.accepted_open_risks) == sorted(keys)
    confirmations = {key: True for key in keys}
    gated = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=confirmations,
    )
    assert gated.error_code == "script_lock_confirmation_required"
    assert gated.preview is not None
    assert gated.preview.can_lock
    locked = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=confirmations,
    )
    assert locked.ok and locked.lock is not None
    assert locked.lock.status == ScriptLockStatus.LOCKED
    assert sorted(locked.lock.accepted_open_risks) == sorted(keys)


# --- Smoke B -----------------------------------------------------------------


def test_smoke_b_missing_one_risk_confirmation_keeps_button_gated(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    keys = _risk_keys(gaps)
    preview = preview_script_lock(project)
    assert preview.lock_fingerprint
    partial = {keys[0]: True, keys[1]: True, keys[2]: False}
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=partial,
    )
    assert not result.ok
    assert result.error_code == "script_lock_requirements_not_met"
    assert result.preview is not None
    assert any(keys[2] in item for item in result.preview.confirmation_blockers)
    assert "Bestaetigung" in result.message or "Risiko" in result.message


# --- Smoke C -----------------------------------------------------------------


def test_smoke_c_visual_intent_id_is_not_silent_risk_identity(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    preview = preview_script_lock(project)
    assert preview.lock_fingerprint
    # Wrong identity: visual_intent_id instead of gap_id.
    wrong = {
        make_lock_risk_confirmation_key(
            gap.visual_intent_id, CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
        ): True
        for gap in gaps
    }
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=wrong,
    )
    assert not result.ok
    assert result.preview is not None
    assert result.preview.confirmation_blockers
    assert any(
        "unknown_gap" in item or "unconfirmed" in item
        for item in result.preview.confirmation_blockers
    )


# --- Smoke D -----------------------------------------------------------------


def test_smoke_d_stale_preview_fingerprint_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    keys = _risk_keys(gaps)
    preview = preview_script_lock(project)
    assert preview.lock_fingerprint
    # Relevant input change: alter a claim decision after preview.
    view = get_editorial_view(project)
    claim = view.script_bundle["claims"][0]
    record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="accepted_as_uncertain",
        reason="stale-preview",
    )
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={key: True for key in keys},
    )
    assert not result.ok
    assert result.error_code == "script_lock_fingerprint_mismatch"


# --- Contract regressions ----------------------------------------------------


def test_fingerprint_visible_without_ui_checkboxes(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _force_three_accepted_unresolved_gaps(project)
    preview = preview_script_lock(project)
    assert preview.ok
    assert preview.lock_fingerprint
    assert not preview.can_lock
    assert preview.confirmation_blockers


def test_canonical_key_helpers_reject_blank_and_parse() -> None:
    key = make_lock_risk_confirmation_key(
        "gap-1", CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
    )
    assert key == "gap-1:coverage_exact_match_not_verified"
    assert parse_lock_risk_confirmation_key(key) == (
        "gap-1",
        "coverage_exact_match_not_verified",
    )
    with pytest.raises(ValueError):
        make_lock_risk_confirmation_key("", "too_generic")
    with pytest.raises(ValueError):
        parse_lock_risk_confirmation_key("no-separator")


def test_gap_order_does_not_change_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    first = preview_script_lock(project).lock_fingerprint
    # Re-read / reverse iteration must not change sorted risk keys.
    assert persisted_accepted_lock_risk_keys(list(reversed(gaps))) == sorted(
        _risk_keys(gaps)
    )
    second = preview_script_lock(project).lock_fingerprint
    assert first == second


def test_wrong_gap_risk_code_pairing_rejected(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    preview = preview_script_lock(project)
    # Pair gap A's id with a risk that is not on that gap (unknown code).
    bad_key = make_lock_risk_confirmation_key(gaps[0].gap_id, "too_generic")
    good = {
        make_lock_risk_confirmation_key(
            gap.gap_id, CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
        ): True
        for gap in gaps[1:]
    }
    good[bad_key] = True
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=good,
    )
    assert not result.ok
    assert result.preview is not None
    assert any(
        "not_on_gap" in item or "unconfirmed" in item
        for item in result.preview.confirmation_blockers
    )


def test_double_create_does_not_create_second_active_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = _force_three_accepted_unresolved_gaps(project)
    keys = {key: True for key in _risk_keys(gaps)}
    preview = preview_script_lock(project)
    first = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=keys,
    )
    assert first.ok and first.lock is not None
    second = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=keys,
    )
    # Second create supersedes with a new lock version — still exactly one effective.
    assert second.ok and second.lock is not None
    effective = get_effective_script_lock(project)
    assert effective.ok and effective.lock is not None
    assert effective.lock.lock_id == second.lock.lock_id
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        locked = [
            item
            for item in repo.list_script_locks(conn, project_id=project.id)
            if item.status == ScriptLockStatus.LOCKED
        ]
    finally:
        conn.close()
    assert len(locked) == 1


def test_ui_source_uses_canonical_gap_risk_keys_not_intent_only() -> None:
    source = Path(editorial_ui.__file__).read_text(encoding="utf-8")
    assert "make_lock_risk_confirmation_key" in source
    # L3: UI reads required_risk_keys from the Editorial gate (preview risks).
    assert "required_risk_keys" in source
    assert "discovery_v2_lock_risk_" in source
    # Must not build confirmation keys from visual_intent_id alone.
    tree = ast.parse(source)
    text = source
    assert 'f"{gap.visual_intent_id}:' not in text
    assert "Script-Lock-Fingerprint verfuegbar" in source


def test_schema_remains_20() -> None:
    assert str(REGISTRY_SCHEMA_VERSION) == "20"


def test_open_gap_count_zero_with_three_accepted(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _force_three_accepted_unresolved_gaps(project)
    preview = preview_script_lock(project)
    gap_req = next(
        item for item in preview.requirement_details if item.code == "coverage_gaps"
    )
    assert gap_req.ok
    assert "0 Coverage Gaps" in gap_req.label or "terminal" in gap_req.label.lower()
