"""Deterministic fixtures for Coverage Stability C1 (reproduction only).

Test helpers only — not a product fingerprint / reuse contract.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    escalate_gap,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.coverage_revalidation_service import (
    revalidate_coverage_after_accepted_reviews,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
    start_script_run,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_candidate_decision,
    start_search_run,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    EscalationStep,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db

from test_discovery_v2_editorial_script import _accepted_editorial_project, _brief_to_narrative

# Exact FakeText audit-id construction (otio_app/discovery_v2/adapters/text_fake.py):
#   def _id(*parts: str) -> str:
#       return str(uuid5(NAMESPACE_URL, "otio-discovery-v2-editorial:" + ":".join(parts)))
#   audit_id = _id("coverage", project_id, script_id, input_fingerprint, run_id)
FAKE_COVERAGE_AUDIT_ID_NAMESPACE = "otio-discovery-v2-editorial:"
FAKE_COVERAGE_AUDIT_ID_PARTS = (
    "coverage",
    "<project_id>",
    "<script_id>",
    "<observation_fingerprint>",
    "<run_id>",
)


def expected_fake_coverage_audit_id(
    *,
    project_id: str,
    script_id: str,
    observation_fingerprint: str,
    run_id: str,
) -> str:
    """Mirror FakeText `_id("coverage", ...)` — test diagnosis only."""

    return str(
        uuid5(
            NAMESPACE_URL,
            FAKE_COVERAGE_AUDIT_ID_NAMESPACE
            + ":".join(
                ("coverage", project_id, script_id, observation_fingerprint, run_id)
            ),
        )
    )


def assert_schema_20(project) -> None:
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
    finally:
        conn.close()


def build_script_ready_project(tmp_path: Path, temp_db_path: Path):
    """Temp Discovery project: brief, narrative, hook, script, ≥3 visual intents."""

    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.ok and view.script is not None
    bundle = view.script_bundle or {}
    intents = bundle.get("visual_intents") or []
    assert len(intents) >= 3, f"expected ≥3 visual intents, got {len(intents)}"
    assert view.observation_fingerprint
    assert_schema_20(project)
    return project


def normalize_fachliche_coverage_inputs(project) -> dict[str, Any]:
    """Canonical fachliche input compare for tests (excludes run_id / audit_id)."""

    view = get_editorial_view(project)
    assert view.ok and view.script is not None and view.active_brief is not None
    brief = view.active_brief
    narrative = view.narrative_plan
    script = view.script
    bundle = view.script_bundle or {}
    config = load_text_config()
    intents = sorted(
        [
            {
                "visual_intent_id": item["visual_intent_id"],
                "visual_beat_id": item["visual_beat_id"],
                "desired_motif": item.get("desired_motif"),
                "action": item.get("action"),
                "setting": item.get("setting"),
                "priority": item.get("priority"),
            }
            for item in (bundle.get("visual_intents") or [])
        ],
        key=lambda item: item["visual_intent_id"],
    )
    beats = sorted(
        [
            {
                "visual_beat_id": item["visual_beat_id"],
                "function": item.get("function"),
                "sentence_ids": list(item.get("sentence_ids") or []),
            }
            for item in (bundle.get("visual_beats") or [])
        ],
        key=lambda item: item["visual_beat_id"],
    )
    return {
        "project_id": project.id,
        "brief": {
            "project_brief_id": brief.project_brief_id,
            "brief_version": brief.brief_version,
            "language": brief.language,
            "topic": brief.topic,
            "target_audience": brief.target_audience,
            "tone": brief.tone,
        },
        "narrative_plan_id": None if narrative is None else narrative.narrative_plan_id,
        "selected_hook_id": view.selected_hook_id,
        "script": {
            "script_id": script.script_id,
            "script_version": script.script_version,
            "full_text": script.full_text,
            "content_sha256": script.content_sha256,
            "status": script.status.value,
        },
        "structure": {"visual_beats": beats, "visual_intents": intents},
        "observation_fingerprint": view.observation_fingerprint,
        "provider": config.provider,
        "model_identifier": config.model_identifier,
        "gateway_version": config.gateway_version,
        "prompt_version": config.prompts.get("coverage"),
        "response_schema_version": config.response_schemas.get("coverage"),
        "run_scope": "editorial_coverage_only",
    }


def normalize_audit_fachlich(audit) -> dict[str, Any]:
    """Fachliche Auditfelder without identity / timestamps / artifact paths."""

    return {
        "project_id": audit.project_id,
        "script_id": audit.script_id,
        "script_version": audit.script_version,
        "brief_version": audit.brief_version,
        "narrative_plan_id": audit.narrative_plan_id,
        "input_observation_fingerprint": audit.input_observation_fingerprint,
        "status": audit.status.value,
        "prompt_version": audit.prompt_version,
        "gateway_version": audit.gateway_version,
        "model_identifier": audit.model_identifier,
        "provider": audit.provider,
        "results": sorted(
            [
                {
                    "visual_intent_id": result.visual_intent_id,
                    "coverage_status": result.coverage_status.value,
                    "candidate_asset_ids": list(result.candidate_asset_ids),
                    "accepted_observation_ids": list(result.accepted_observation_ids),
                    "missing_properties": list(result.missing_properties),
                    "rationale": result.rationale,
                    "recommended_next_action": result.recommended_next_action,
                }
                for result in audit.results
            ],
            key=lambda item: item["visual_intent_id"],
        ),
    }


@dataclass
class CoverageRunSnapshot:
    run_id: str
    coverage_audit_id: str
    audit: Any
    fachliche_inputs: dict[str, Any]
    fachliche_audit: dict[str, Any]


def _coverage_runs(project) -> list:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        return [
            run
            for run in editorial_repo.list_editorial_runs(conn, project_id=project.id)
            if run.scope == "editorial_coverage_only"
        ]
    finally:
        conn.close()


def load_audit(project, coverage_audit_id: str):
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        audit = editorial_repo.get_coverage_audit(
            conn, coverage_audit_id=coverage_audit_id
        )
    finally:
        conn.close()
    assert audit is not None, f"missing audit {coverage_audit_id}"
    return audit


def current_audit_id(project) -> str:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
    finally:
        conn.close()
    assert state is not None and state.active_coverage_audit_id
    return state.active_coverage_audit_id


def snapshot_after_coverage_run(project, *, run_id: str) -> CoverageRunSnapshot:
    # Prefer active_coverage_audit_id: FakeText uses a fixed created_at, so
    # get_latest_coverage_audit (used by get_editorial_view) is not reliable.
    view = get_editorial_view(project)
    assert view.script is not None
    audit_id = current_audit_id(project)
    audit = load_audit(project, audit_id)
    expected = expected_fake_coverage_audit_id(
        project_id=project.id,
        script_id=view.script.script_id,
        observation_fingerprint=view.observation_fingerprint or "",
        run_id=run_id,
    )
    assert audit.coverage_audit_id == expected
    return CoverageRunSnapshot(
        run_id=run_id,
        coverage_audit_id=audit.coverage_audit_id,
        audit=audit,
        fachliche_inputs=normalize_fachliche_coverage_inputs(project),
        fachliche_audit=normalize_audit_fachlich(audit),
    )


def run_manual_coverage(
    project, *, sync: bool = True, expect_reuse: bool = False
):
    before = {run.run_id for run in _coverage_runs(project)}
    started = start_coverage_run(project, sync=sync)
    if expect_reuse or started.reused:
        assert started.reused, started.message
        assert started.coverage_audit_id or (
            started.run is not None and started.run.run_id in before
        )
        return started
    assert started.started and started.run is not None, started.message
    assert started.run.run_id not in before
    if sync:
        assert started.run.status.value == "completed"
        assert current_audit_id(project)
    return started


def run_automatic_coverage_revalidation(project, *, sync: bool = True):
    before = {run.run_id for run in _coverage_runs(project)}
    result = revalidate_coverage_after_accepted_reviews(project, sync=sync)
    assert result.ok, result.message
    if result.coverage_started:
        assert result.run_id and result.run_id not in before
        if sync:
            conn = editorial_repo.open_editorial_registry(project.project_root_path)
            try:
                run = editorial_repo.get_editorial_run(conn, run_id=result.run_id)
            finally:
                conn.close()
            assert run is not None
            assert run.status.value == "completed"
    return result


def _escalate_to_user_decision(project, gap_id: str):
    for _ in range(len(EscalationStep)):
        result = escalate_gap(project, gap_id=gap_id)
        assert result.ok and result.gap is not None
        if result.gap.current_escalation_step == EscalationStep.USER_DECISION:
            return result.gap
    raise AssertionError("failed to reach user_decision")


def _reject_all_candidates(project, gap_id: str) -> list[str]:
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        candidates = supp_repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)
    finally:
        conn.close()
    ids: list[str] = []
    for candidate in candidates:
        decided = record_candidate_decision(
            project,
            candidate_id=candidate.candidate_id,
            decision="rejected",
            reason="C1 fixture reject",
        )
        assert decided.ok
        ids.append(candidate.candidate_id)
    return ids


@dataclass
class ProgressedGaps:
    gaps: list
    gap_user_decision_id: str
    gap_photo_id: str
    gap_candidate_id: str
    visual_intent_ids: list[str]
    candidate_decision_ids: list[str]
    gap1_event_ids: list[str]


def progress_three_gaps(project) -> ProgressedGaps:
    """Mutate three gaps via application services only (no SQL writes)."""

    materialized = materialize_gaps_from_current_coverage(project)
    assert materialized.ok
    gaps = sorted(materialized.gaps, key=lambda gap: gap.visual_intent_id)
    assert len(gaps) >= 3, f"expected ≥3 gaps, got {len(gaps)}"
    gap1, gap2, gap3 = gaps[0], gaps[1], gaps[2]

    gap1 = _escalate_to_user_decision(project, gap1.gap_id)
    assert start_search_run(project, gap_ids=[gap1.gap_id], sync=True).started
    rejected = _reject_all_candidates(project, gap1.gap_id)
    assert rejected
    accepted = accept_gap_unresolved(
        project,
        gap_id=gap1.gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
        user_confirmed=True,
    )
    assert accepted.ok and accepted.gap is not None
    assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert accepted.gap.user_decision == "accepted_unresolved"

    photo = escalate_gap(project, gap_id=gap2.gap_id)
    assert photo.ok and photo.gap is not None
    assert photo.gap.current_escalation_step == EscalationStep.PHOTO
    assert photo.gap.status == CoverageGapStatus.IN_PROGRESS

    assert start_search_run(project, gap_ids=[gap3.gap_id], sync=True).started
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        candidates = supp_repo.list_stock_candidates_for_gap(conn, gap_id=gap3.gap_id)
        assert candidates
        candidate_id = candidates[0].candidate_id
    finally:
        conn.close()
    decision = record_candidate_decision(
        project,
        candidate_id=candidate_id,
        decision="needs_review",
        reason="C1 fixture candidate decision",
    )
    assert decision.ok

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        events = supp_repo.list_gap_events(conn, gap_id=gap1.gap_id)
        event_ids = [event.event_id for event in events]
        decisions = supp_repo.list_candidate_decisions(conn, gap_id=gap3.gap_id)
        decision_ids = [item.decision_id for item in decisions]
        refreshed = [
            supp_repo.get_coverage_gap(conn, gap_id=gap.gap_id) for gap in gaps[:3]
        ]
    finally:
        conn.close()
    assert all(item is not None for item in refreshed)
    return ProgressedGaps(
        gaps=refreshed,  # type: ignore[arg-type]
        gap_user_decision_id=gap1.gap_id,
        gap_photo_id=gap2.gap_id,
        gap_candidate_id=gap3.gap_id,
        visual_intent_ids=[gap.visual_intent_id for gap in gaps[:3]],
        candidate_decision_ids=decision_ids,
        gap1_event_ids=event_ids,
    )


def list_all_gaps(project):
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        return supp_repo.list_coverage_gaps(
            conn, project_id=project.id, include_superseded=True
        )
    finally:
        conn.close()


def gaps_for_audit(project, coverage_audit_id: str):
    return [
        gap for gap in list_all_gaps(project) if gap.coverage_audit_id == coverage_audit_id
    ]


@dataclass
class CallOrderRecorder:
    """Test-only instrumentation of existing repository/service calls."""

    events: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def note(self, label: str) -> None:
        with self._lock:
            self.events.append(label)

    def install(self, monkeypatch) -> None:
        recorder = self
        original_insert_audit = editorial_repo.insert_coverage_audit
        original_upsert_state = editorial_repo.upsert_project_state
        original_supersede = supp_repo.supersede_gaps_not_in_audit
        original_insert_gap = supp_repo.insert_coverage_gap
        original_materialize = materialize_gaps_from_current_coverage

        def insert_audit(conn, audit, relative_path):
            recorder.note("insert_coverage_audit")
            return original_insert_audit(conn, audit, relative_path)

        def upsert_state(conn, state):
            if state.active_coverage_audit_id:
                recorder.note(
                    f"upsert_project_state:active_coverage_audit_id={state.active_coverage_audit_id}"
                )
            else:
                recorder.note("upsert_project_state")
            return original_upsert_state(conn, state)

        def supersede(conn, *, project_id: str, coverage_audit_id: str):
            recorder.note(
                f"supersede_gaps_not_in_audit:audit={coverage_audit_id}"
            )
            return original_supersede(
                conn, project_id=project_id, coverage_audit_id=coverage_audit_id
            )

        def insert_gap(conn, gap, relative_path):
            recorder.note(f"insert_coverage_gap:gap_id={gap.gap_id}")
            return original_insert_gap(conn, gap, relative_path)

        def materialize(project):
            recorder.note("materialize_gaps_from_current_coverage:enter")
            result = original_materialize(project)
            recorder.note("materialize_gaps_from_current_coverage:exit")
            return result

        monkeypatch.setattr(editorial_repo, "insert_coverage_audit", insert_audit)
        monkeypatch.setattr(editorial_repo, "upsert_project_state", upsert_state)
        monkeypatch.setattr(supp_repo, "supersede_gaps_not_in_audit", supersede)
        monkeypatch.setattr(supp_repo, "insert_coverage_gap", insert_gap)
        monkeypatch.setattr(
            "otio_app.discovery_v2.application.coverage_gap_service.materialize_gaps_from_current_coverage",
            materialize,
        )
        # Worker imports editorial_repository as repo — patch module used by worker.
        monkeypatch.setattr(
            "otio_app.discovery_v2.jobs.editorial_worker.repo.insert_coverage_audit",
            insert_audit,
        )
        monkeypatch.setattr(
            "otio_app.discovery_v2.jobs.editorial_worker.repo.upsert_project_state",
            upsert_state,
        )
        monkeypatch.setattr(
            "otio_app.discovery_v2.application.coverage_gap_service.repo.supersede_gaps_not_in_audit",
            supersede,
        )
        monkeypatch.setattr(
            "otio_app.discovery_v2.application.coverage_gap_service.repo.insert_coverage_gap",
            insert_gap,
        )


def install_no_media_io_guards(monkeypatch) -> None:
    from fixtures.visual_edit_rework_v1 import (
        install_no_media_io_guards as _install,
    )

    _install(monkeypatch)


def install_active_coverage_worker_gate(
    monkeypatch,
    *,
    release: threading.Event,
    entered: threading.Event,
) -> None:
    """Hold the coverage worker so a second start hits the active-run gate."""

    import otio_app.discovery_v2.jobs.editorial_worker as worker_mod

    original = worker_mod._process_coverage

    def _gated(conn, root, run):
        entered.set()
        assert release.wait(timeout=10), "active-run gate release timed out"
        return original(conn, root, run)

    monkeypatch.setattr(worker_mod, "_process_coverage", _gated)


def dump_normalized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


__all__ = [
    "FAKE_COVERAGE_AUDIT_ID_NAMESPACE",
    "FAKE_COVERAGE_AUDIT_ID_PARTS",
    "CallOrderRecorder",
    "CoverageRunSnapshot",
    "ProgressedGaps",
    "assert_schema_20",
    "build_script_ready_project",
    "current_audit_id",
    "dump_normalized",
    "expected_fake_coverage_audit_id",
    "gaps_for_audit",
    "install_active_coverage_worker_gate",
    "install_no_media_io_guards",
    "list_all_gaps",
    "load_audit",
    "normalize_audit_fachlich",
    "normalize_fachliche_coverage_inputs",
    "progress_three_gaps",
    "run_automatic_coverage_revalidation",
    "run_manual_coverage",
    "snapshot_after_coverage_run",
]
