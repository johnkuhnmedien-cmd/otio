"""Phase 8D: observation review gate, editorial-ready, and local Phase-8 E2E."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from otio_app.discovery_v2.adapters import vision_config
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.ffmpeg_runner import ffmpeg_available
from otio_app.discovery_v2.adapters.vision_fake import reset_fake_vision_test_hook
from otio_app.discovery_v2.application import model_analysis_service
from otio_app.discovery_v2.application.model_analysis_service import (
    start_model_analysis,
)
from otio_app.discovery_v2.application.observation_review_service import (
    get_observation_review_view,
    get_phase8_project_summary,
    list_editorial_ready_observations,
    submit_observation_review,
)
from otio_app.discovery_v2.domain.asset_analysis import AnalysisRunStatus
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.observation_review import (
    OBSERVATION_REVIEW_ERROR_REASON_REQUIRED,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING,
    PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED,
    PHASE8_ASSET_STATUS_OBSERVATION_REJECTED,
    compute_observation_sha256,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.discovery_v2.paths import get_discovery_v2_root

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discovery_v2_analysis_prepare import (  # noqa: PLC2701
    _FakeStreamlit,
    _make_color_video,
    _new_project,
    _prepare_project,
    _require_ffmpeg,
)
from test_discovery_v2_model_analysis_fake import (  # noqa: PLC2701
    _prepared_still_project,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_fake_vision_test_hook()
    yield
    reset_fake_vision_test_hook()
    reset_analysis_job_launcher_for_tests()


def _model_analyze(project) -> None:
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.started and result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED


def _observations(project):
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        return analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
    finally:
        conn.close()


def _sha_originals(root: Path) -> dict[str, str]:
    media = root / "Media"
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(media.iterdir())
        if path.is_file()
    }


# --- Schema -----------------------------------------------------------------


def test_schema_13_to_14_preserves_data_and_is_idempotent(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="Phase 8D Schema")
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "20"
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group, file_name,
                extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-keep",
                project.id,
                "Media/still.jpg",
                "Media",
                "still.jpg",
                ".jpg",
                "image",
                1,
                1,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.execute("UPDATE registry_schema SET schema_version = '13'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == REGISTRY_SCHEMA_VERSION == "20"
        assert (
            conn2.execute(
                "SELECT COUNT(*) FROM assets WHERE asset_id = 'asset-keep'"
            ).fetchone()[0]
            == 1
        )
        tables = {
            str(row[0])
            for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "visual_observation_reviews" in tables
        assert {
            "visual_observations",
            "model_analysis_attempts",
            "analysis_consent_events",
        }.issubset(tables)
    finally:
        conn2.close()

    conn3 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn3) == "20"
    finally:
        conn3.close()


def test_schema_14_review_unique_and_decision_checks(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        identity = analysis_repo.get_analysis_identity(
            conn, analysis_identity_id=obs.analysis_identity_id
        )
        assert identity is not None
        base = {
            "review_id": "rev-1",
            "observation_id": obs.observation_id,
            "analysis_identity_id": obs.analysis_identity_id,
            "project_id": project.id,
            "asset_id": obs.asset_id,
            "working_media_id": identity.working_media_id,
            "observation_sha256": compute_observation_sha256(obs.observation_json),
            "frame_set_fingerprint": obs.frame_hash_fingerprint,
            "review_revision": 1,
            "decision": "accepted",
            "reason_code": None,
            "review_note": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "supersedes_review_id": None,
        }
        cols = ", ".join(base)
        placeholders = ", ".join("?" for _ in base)
        conn.execute(
            f"INSERT INTO visual_observation_reviews ({cols}) VALUES ({placeholders})",
            tuple(base.values()),
        )
        with pytest.raises(Exception):
            conn.execute(
                f"INSERT INTO visual_observation_reviews ({cols}) VALUES ({placeholders})",
                tuple({**base, "review_id": "rev-dup"}.values()),
            )
        with pytest.raises(Exception):
            bad = {**base, "review_id": "rev-bad-dec", "review_revision": 2, "decision": "maybe"}
            conn.execute(
                f"INSERT INTO visual_observation_reviews ({cols}) VALUES ({placeholders})",
                tuple(bad.values()),
            )
        with pytest.raises(Exception):
            bad = {**base, "review_id": "rev-bad-rev", "review_revision": 0}
            conn.execute(
                f"INSERT INTO visual_observation_reviews ({cols}) VALUES ({placeholders})",
                tuple(bad.values()),
            )
    finally:
        conn.close()


# --- Review -----------------------------------------------------------------


def test_review_revisions_are_append_only(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    first = submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="blurry",
    )
    second = submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="accepted",
    )
    assert first.ok and second.ok
    assert first.review is not None and second.review is not None
    assert first.review.review_revision == 1
    assert second.review.review_revision == 2
    assert second.review.supersedes_review_id == first.review.review_id
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        reviews = analysis_repo.list_observation_reviews(
            conn, observation_id=obs.observation_id
        )
        assert [r.review_revision for r in reviews] == [1, 2]
        assert reviews[0].decision == "rejected"
        assert reviews[0].reason_code == "blurry"
        assert reviews[1].decision == "accepted"
    finally:
        conn.close()


def test_reject_and_reanalyze_require_reason(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    rejected = submit_observation_review(
        project, observation_id=obs.observation_id, decision="rejected"
    )
    reanalyze = submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="reanalyze_requested",
        reason_code="   ",
    )
    accepted = submit_observation_review(
        project, observation_id=obs.observation_id, decision="accepted"
    )
    assert rejected.ok is False
    assert rejected.error_code == OBSERVATION_REVIEW_ERROR_REASON_REQUIRED
    assert reanalyze.ok is False
    assert reanalyze.error_code == OBSERVATION_REVIEW_ERROR_REASON_REQUIRED
    assert accepted.ok is True


def test_missing_observation_and_hash_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    missing = submit_observation_review(
        project, observation_id="does-not-exist", decision="accepted"
    )
    assert missing.error_code == OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING
    obs = _observations(project)[0]
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        frames = analysis_repo.list_representative_frames(
            conn, analysis_identity_id=obs.analysis_identity_id
        )
        assert frames
        conn.execute(
            "UPDATE representative_frames SET frame_sha256 = ? WHERE frame_id = ?",
            ("b" * 64, frames[0].frame_id),
        )
        conn.commit()
    finally:
        conn.close()
    mismatched = submit_observation_review(
        project, observation_id=obs.observation_id, decision="accepted"
    )
    assert mismatched.error_code == OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH


def test_new_prompt_version_observation_is_unreviewed(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    old = _observations(project)[0]
    assert submit_observation_review(
        project, observation_id=old.observation_id, decision="accepted"
    ).ok
    new_config = replace(
        vision_config.load_vision_config(), prompt_version="vision-prompt-v2"
    )
    import otio_app.discovery_v2.application.observation_review_service as review_svc
    import otio_app.discovery_v2.jobs.model_analysis_worker as worker

    monkeypatch.setattr(model_analysis_service, "load_vision_config", lambda: new_config)
    monkeypatch.setattr(worker, "load_vision_config", lambda: new_config)
    monkeypatch.setattr(review_svc, "load_vision_config", lambda: new_config)
    _model_analyze(project)
    observations = _observations(project)
    assert len(observations) == 2
    ready = list_editorial_ready_observations(project)
    assert [item.observation_id for item in ready] == []
    view = get_observation_review_view(project)
    current = [item for item in view.observations if item.is_current_identity]
    assert len(current) == 1
    assert current[0].current_review_decision == "unreviewed"
    assert current[0].prompt_version == "vision-prompt-v2"


def test_cache_reuse_keeps_accepted_review(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    assert submit_observation_review(
        project, observation_id=obs.observation_id, decision="accepted"
    ).ok
    second = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert second.run is not None
    assert second.run.status == AnalysisRunStatus.COMPLETED
    assert len(_observations(project)) == 1
    ready = list_editorial_ready_observations(project)
    assert len(ready) == 1
    assert ready[0].observation_id == obs.observation_id
    assert ready[0].review.decision == "accepted"


# --- Editorial ready --------------------------------------------------------


def test_editorial_ready_filters_by_current_decision(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    assert list_editorial_ready_observations(project) == []
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="nope",
    ).ok
    assert list_editorial_ready_observations(project) == []
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="reanalyze_requested",
        reason_code="again",
    ).ok
    assert list_editorial_ready_observations(project) == []
    assert submit_observation_review(
        project, observation_id=obs.observation_id, decision="accepted"
    ).ok
    ready = list_editorial_ready_observations(project)
    assert len(ready) == 1
    assert ready[0].observation_id == obs.observation_id


# --- UI ---------------------------------------------------------------------


def test_ui_review_actions_no_media_io_or_gateway(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    starts: list[Any] = []
    gateway_calls: list[Any] = []
    fake = _FakeStreamlit(clicked=False)

    def _text_area(*_a: Any, **_k: Any) -> str:
        return ""

    fake.text_area = _text_area  # type: ignore[attr-defined]
    monkeypatch.setattr(analysis_ui, "st", fake)
    monkeypatch.setattr(analysis_ui, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        analysis_ui,
        "start_model_analysis",
        lambda *a, **k: starts.append((a, k)),
    )
    monkeypatch.setattr(
        analysis_ui,
        "start_analysis_prepare",
        lambda *a, **k: starts.append((a, k)),
    )
    import otio_app.discovery_v2.adapters.vision_gateway as gateway

    monkeypatch.setattr(
        gateway.DiscoveryVisionGateway,
        "analyze",
        lambda self, request: gateway_calls.append(request),
    )
    source = Path(analysis_ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "open_analysis_registry" not in source
    assert "sqlite3" not in source
    assert "ffmpeg" not in source.lower()
    assert "submit_observation_review" in source
    assert "get_observation_review_view" in source
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "open" not in calls
    analysis_ui.render_discovery_asset_analysis_page()
    assert starts == []
    assert gateway_calls == []


def test_ui_accept_button_submits_review_only(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    submitted: list[dict[str, Any]] = []
    model_starts: list[Any] = []

    class _FakeSt(_FakeStreamlit):
        def button(self, label: str, **kwargs: Any) -> bool:
            self.buttons.append({"label": label, **kwargs})
            return label == "Observation akzeptieren"

        def text_area(self, *_a: Any, **_k: Any) -> str:
            return ""

    fake = _FakeSt(clicked=False)
    monkeypatch.setattr(analysis_ui, "st", fake)
    monkeypatch.setattr(analysis_ui, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        analysis_ui,
        "start_model_analysis",
        lambda *a, **k: model_starts.append((a, k)),
    )
    monkeypatch.setattr(
        analysis_ui,
        "submit_observation_review",
        lambda project, **kwargs: submitted.append(kwargs)
        or type(
            "R",
            (),
            {"ok": True, "message": "ok", "error_code": None, "review": None},
        )(),
    )
    analysis_ui.render_discovery_asset_analysis_page()
    assert model_starts == []
    assert submitted
    assert submitted[0]["observation_id"] == obs.observation_id
    assert submitted[0]["decision"] == "accepted"


# --- Recovery ---------------------------------------------------------------


def test_recovery_does_not_mutate_reviews(
    tmp_path: Path, temp_db_path: Path
) -> None:
    from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
        reconcile_orphaned_analysis_run,
    )
    from otio_app.discovery_v2.domain.asset_analysis import (
        ANALYSIS_MODEL_PROFILE,
        ANALYSIS_RUN_SCOPE_MODEL,
        AnalysisRun,
    )
    from otio_app.discovery_v2.domain.visual_observation import AnalysisModelAssetStatus
    from datetime import datetime, timezone

    project = _prepared_still_project(tmp_path, temp_db_path)
    _model_analyze(project)
    obs = _observations(project)[0]
    assert submit_observation_review(
        project, observation_id=obs.observation_id, decision="accepted"
    ).ok
    now = datetime.now(timezone.utc)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        prepared_run = analysis_repo.get_latest_analysis_run(
            conn, project_id=project.id, scope="analysis_prepare_only"
        )
        assert prepared_run is not None
        prepared_asset = analysis_repo.list_analysis_run_assets(
            conn, run_id=prepared_run.run_id
        )[0]
        run = AnalysisRun(
            run_id="model-orphan-8d",
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_MODEL,
            analysis_profile_version=ANALYSIS_MODEL_PROFILE,
            status=AnalysisRunStatus.RUNNING,
            created_at=now,
            started_at=now,
            total_assets=1,
        )
        analysis_repo.insert_analysis_run(conn, run)
        analysis_repo.insert_analysis_run_asset(
            conn,
            prepared_asset.model_copy(
                update={
                    "run_id": run.run_id,
                    "status": AnalysisModelAssetStatus.ANALYZING,
                }
            ),
        )
        conn.commit()
        before = analysis_repo.list_observation_reviews(
            conn, observation_id=obs.observation_id
        )
    finally:
        conn.close()
    updated = reconcile_orphaned_analysis_run(project)
    assert updated is not None
    conn2 = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        after = analysis_repo.list_observation_reviews(
            conn2, observation_id=obs.observation_id
        )
        assert [r.model_dump(mode="json") for r in after] == [
            r.model_dump(mode="json") for r in before
        ]
        assert analysis_repo.list_visual_observations_for_project(
            conn2, project_id=project.id
        )
    finally:
        conn2.close()


# --- Phase 8 E2E ------------------------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg unavailable")
def test_phase8_e2e_video_and_still_review_to_editorial_ready(
    tmp_path: Path, temp_db_path: Path
) -> None:
    _require_ffmpeg()
    root = tmp_path / "Project"
    media = root / "Media"
    media.mkdir(parents=True)
    video_path = media / "clip.mp4"
    still_path = media / "still.jpg"
    _make_color_video(video_path, ["red", "green", "blue"], segment_duration=0.5)
    Image.new("RGB", (48, 32), (20, 40, 60)).save(still_path)
    before_media = _sha_originals(root)

    project = _new_project(root, temp_db_path, name="Phase 8D E2E")
    prepare = _prepare_project(project)
    assert prepare.run is not None
    assert prepare.run.status == AnalysisRunStatus.COMPLETED

    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        prepare_assets = analysis_repo.list_analysis_run_assets(
            conn, run_id=prepare.run.run_id
        )
        shots = analysis_repo.list_technical_shots_for_project(
            conn, project_id=project.id
        )
        frames = analysis_repo.list_representative_frames_for_project(
            conn, project_id=project.id
        )
        working_media_ids = sorted({a.working_media_id for a in prepare_assets})
        identity_ids = sorted(
            {a.analysis_identity_id for a in prepare_assets if a.analysis_identity_id}
        )
    finally:
        conn.close()

    model = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert model.run is not None
    assert model.run.status == AnalysisRunStatus.COMPLETED
    observations = _observations(project)
    assert len(observations) == 2
    by_kind = {}
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        for obs in observations:
            identity = analysis_repo.get_analysis_identity(
                conn, analysis_identity_id=obs.analysis_identity_id
            )
            assert identity is not None
            # resolve media kind via prepare asset
            assets = [
                a
                for run in analysis_repo.list_analysis_runs(conn, project_id=project.id)
                for a in analysis_repo.list_analysis_run_assets(conn, run_id=run.run_id)
                if a.analysis_identity_id == obs.analysis_identity_id
            ]
            kind = assets[0].media_kind
            by_kind[kind] = obs
    finally:
        conn.close()
    assert set(by_kind) >= {"video", "image"}
    asset_a = by_kind["video"]
    asset_b = by_kind["image"]

    accept_a = submit_observation_review(
        project, observation_id=asset_a.observation_id, decision="accepted"
    )
    reject_b = submit_observation_review(
        project,
        observation_id=asset_b.observation_id,
        decision="rejected",
        reason_code="not_useful",
    )
    assert accept_a.ok and reject_b.ok
    ready_mid = list_editorial_ready_observations(project)
    assert {item.observation_id for item in ready_mid} == {asset_a.observation_id}

    accept_b = submit_observation_review(
        project, observation_id=asset_b.observation_id, decision="accepted"
    )
    assert accept_b.ok and accept_b.review is not None
    assert accept_b.review.review_revision == 2
    ready = list_editorial_ready_observations(project)
    assert {item.observation_id for item in ready} == {
        asset_a.observation_id,
        asset_b.observation_id,
    }

    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        history_b = analysis_repo.list_observation_reviews(
            conn, observation_id=asset_b.observation_id
        )
        assert [r.decision for r in history_b] == ["rejected", "accepted"]
        assert history_b[0].review_revision == 1
        # no analysis frames registered as working media
        wm_paths = [
            str(row["working_relative_path"])
            for row in conn.execute(
                "SELECT working_relative_path FROM working_media WHERE project_id = ?",
                (project.id,),
            ).fetchall()
        ]
        assert all("analysis/frames" not in path for path in wm_paths)
        assert all(path.startswith("media/working/") for path in wm_paths)
    finally:
        conn.close()

    assert _sha_originals(root) == before_media
    v2 = get_discovery_v2_root(root)
    assert v2.is_dir()
    assert not (root / "_otio").exists()
    for obs in observations:
        assert obs.relative_json_path.startswith("analysis/observations/")
        assert not obs.relative_json_path.startswith("/")

    summary = get_phase8_project_summary(project)
    assert summary.ok
    accepted_assets = [
        a
        for a in summary.assets
        if a.status == PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED
    ]
    assert len(accepted_assets) == 2

    # Report artifacts for Abschlussbericht
    report = {
        "prepare_run_id": prepare.run.run_id,
        "model_run_id": model.run.run_id,
        "working_media_ids": working_media_ids,
        "analysis_identity_ids": identity_ids,
        "shot_ids": [s.shot_id for s in shots],
        "frame_ids": [f.frame_id for f in frames],
        "observation_ids": [o.observation_id for o in observations],
        "review_ids": [
            accept_a.review.review_id if accept_a.review else None,
            reject_b.review.review_id if reject_b.review else None,
            accept_b.review.review_id if accept_b.review else None,
        ],
        "editorial_ready": [r.observation_id for r in ready],
        "relative_observation_paths": [o.relative_json_path for o in observations],
        "result": "PASS",
    }
    Path("/tmp/cursor/artifacts").mkdir(parents=True, exist_ok=True)
    Path("/tmp/cursor/artifacts/phase8d_e2e_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_phase8_summary_and_no_phase9_models_in_modules() -> None:
    for rel in (
        "otio_app/discovery_v2/domain/observation_review.py",
        "otio_app/discovery_v2/application/observation_review_service.py",
        "otio_app/discovery_v2/ui/asset_analysis_page.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        for needle in (
            "visual beat",
            "visual_beat",
            "dramaturgy",
            "project brief",
            "coverage audit",
            "gemini",
            "openai",
            "anthropic",
            "openrouter",
        ):
            assert needle not in source
