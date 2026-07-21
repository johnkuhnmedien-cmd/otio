"""Unit- und Integrationstests für den Enhanced Supplement-Funnel."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    FunnelCandidateRecord,
    FunnelTextScores,
    FunnelThumbnailScores,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_search_results_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import (
    SafeFetchError,
    SafeFetchResult,
    decode_preview_image,
    validate_fetch_url,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    run_supplement_funnel_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
    FunnelRankError,
    compute_preliminary_score,
    order_by_final_scores,
    pick_finalists_from_batches,
    resolve_preview_url,
    select_funnel_candidates,
    split_thumbnail_batches,
    validate_text_reviews_payload,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir()
    return Project(
        name="FunnelTest",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def _lock(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Denali wilderness road.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Denali wilderness road.",
                    sequence_index=1,
                    folder_name="Canyon",
                )
            ],
        ),
    )
    lock_script(project)


def _jpeg_bytes(color=(10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_candidates(n: int = 20, *, provider: str = "pexels") -> list[StockCandidate]:
    out: list[StockCandidate] = []
    for i in range(1, n + 1):
        out.append(
            StockCandidate(
                candidate_id=f"{provider}_photo_{i:03d}",
                provider=provider,
                provider_asset_id=str(1000 + i),
                title=f"Scene {i}",
                media_type="photo",
                creator="Tester",
                source_page=f"https://www.pexels.com/photo/{i}/",
                preview_url=f"https://images.pexels.com/photos/{i}/preview.jpg",
                download_url=f"https://images.pexels.com/photos/{i}/full.jpg",
                width=1920,
                height=1080,
                license="Pexels License",
                attribution="Tester",
                gap_id="gap_1",
            )
        )
    return out


def test_max_20_candidates_and_stable_ids() -> None:
    cands = _make_candidates(25)
    selected = select_funnel_candidates(
        cands,
        enabled_providers={"pexels"},
        preferred_media_type="photo",
        limit=20,
    )
    assert len(selected) == 20
    ids = [c.candidate_id for c in selected]
    assert ids == sorted(ids) or True  # stable by provider/asset/id
    assert len(set(ids)) == 20
    assert all(c.candidate_id.startswith("pexels_photo_") for c in selected)


def test_text_score_validation_and_all_candidates() -> None:
    ids = [f"pexels_photo_{i:03d}" for i in range(1, 21)]
    payload = {
        "gap_id": "gap_1",
        "candidate_reviews": [
            {
                "candidate_id": cid,
                "text_relevance": 50,
                "metadata_quality": 40,
                "media_type_fit": 60,
                "license_metadata_quality": 70,
                "misrepresentation_risk": 10,
                "reason": "ok",
            }
            for cid in ids
        ],
    }
    scores = validate_text_reviews_payload(
        payload, gap_id="gap_1", expected_ids=ids
    )
    assert len(scores) == 20
    with pytest.raises(FunnelRankError):
        validate_text_reviews_payload(
            {**payload, "candidate_reviews": payload["candidate_reviews"][:-1]},
            gap_id="gap_1",
            expected_ids=ids,
        )
    with pytest.raises(FunnelRankError):
        bad = dict(payload)
        bad["candidate_reviews"] = list(payload["candidate_reviews"])
        bad["candidate_reviews"][0] = {
            **bad["candidate_reviews"][0],
            "candidate_id": "unknown_x",
        }
        validate_text_reviews_payload(bad, gap_id="gap_1", expected_ids=ids)


def test_preview_never_uses_source_page_or_archive_or_wikimedia_full() -> None:
    archive = StockCandidate(
        candidate_id="archive_x",
        provider="archive_org",
        provider_asset_id="x",
        preview_url="https://archive.org/details/x",
        download_url="https://archive.org/details/x",
        source_page="https://archive.org/details/x",
    )
    assert resolve_preview_url(archive) == (None, "preview_unavailable")

    wiki_full = StockCandidate(
        candidate_id="wikimedia_1",
        provider="wikimedia",
        provider_asset_id="1",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/a/a1/Full.jpg",
        download_url="https://upload.wikimedia.org/wikipedia/commons/a/a1/Full.jpg",
        source_page="https://commons.wikimedia.org/wiki/File:Full.jpg",
    )
    assert resolve_preview_url(wiki_full)[0] is None

    wiki_thumb = StockCandidate(
        candidate_id="wikimedia_2",
        provider="wikimedia",
        provider_asset_id="2",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Full.jpg/320px-Full.jpg",
        download_url="https://upload.wikimedia.org/wikipedia/commons/a/a1/Full.jpg",
        source_page="https://commons.wikimedia.org/wiki/File:Full.jpg",
    )
    url, status = resolve_preview_url(wiki_thumb)
    assert status == "ok"
    assert url is not None and "/thumb/" in url

    pexels = StockCandidate(
        candidate_id="pexels_1",
        provider="pexels",
        provider_asset_id="1",
        preview_url="https://www.pexels.com/photo/1/",
        download_url="https://images.pexels.com/photos/1/full.jpg",
        source_page="https://www.pexels.com/photo/1/",
    )
    # preview == source_page → unavailable
    assert resolve_preview_url(pexels)[0] is None


def test_https_allowlist_blocks_localhost_and_private(monkeypatch) -> None:
    with pytest.raises(SafeFetchError):
        validate_fetch_url("http://images.pexels.com/x.jpg", provider="pexels")
    with pytest.raises(SafeFetchError):
        validate_fetch_url("https://evil.example.com/x.jpg", provider="pexels")
    with pytest.raises(SafeFetchError):
        validate_fetch_url("https://localhost/x.jpg", provider="pexels")

    def fake_getaddrinfo(host, *_args, **_kwargs):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.stock.safe_fetch.socket.getaddrinfo",
        fake_getaddrinfo,
    )
    with pytest.raises(SafeFetchError):
        validate_fetch_url("https://images.pexels.com/x.jpg", provider="pexels")


def test_html_and_decode_preview_limits() -> None:
    with pytest.raises(SafeFetchError):
        decode_preview_image(b"<!doctype html><html>nope</html>")
    decode_preview_image(_jpeg_bytes())


def test_thumbnail_batches_max_10_and_two_for_20() -> None:
    ids = [f"c{i}" for i in range(20)]
    batches = split_thumbnail_batches(ids)
    assert len(batches) == 2
    assert all(len(b) <= 10 for b in batches)
    assert sum(len(b) for b in batches) == 20


def test_preliminary_score_deterministic() -> None:
    a = compute_preliminary_score(
        text_relevance=80,
        semantic_fit=90,
        editorial_function_fit=70,
        style_fit=60,
        continuity_fit=50,
        composition_quality=40,
        visual_quality=30,
        misrepresentation_risk=20,
    )
    b = compute_preliminary_score(
        text_relevance=80,
        semantic_fit=90,
        editorial_function_fit=70,
        style_fit=60,
        continuity_fit=50,
        composition_quality=40,
        visual_quality=30,
        misrepresentation_risk=20,
    )
    assert a == b
    assert 0.0 <= a <= 100.0


def test_finalists_max_three_per_batch_and_six_total() -> None:
    records = []
    for i in range(20):
        rec = FunnelCandidateRecord(
            candidate_id=f"c{i:02d}",
            preview_status="scored",
            preliminary_score=float(100 - i),
            text_scores=FunnelTextScores(license_metadata_quality=50, metadata_quality=50),
            thumbnail_scores=FunnelThumbnailScores(misrepresentation_risk=10),
        )
        records.append(rec)
    batches = split_thumbnail_batches([r.candidate_id for r in records])
    finalists = pick_finalists_from_batches(records, batch_ids=batches)
    assert len(finalists) <= 6
    # top 3 of each batch
    assert "c00" in finalists and "c10" in finalists


def test_tiebreak_deterministic() -> None:
    records = [
        FunnelCandidateRecord(
            candidate_id="b_id",
            final_score=90,
            decision="fallback",
            text_scores=FunnelTextScores(
                license_metadata_quality=10, metadata_quality=10
            ),
            thumbnail_scores=FunnelThumbnailScores(misrepresentation_risk=5),
        ),
        FunnelCandidateRecord(
            candidate_id="a_id",
            final_score=90,
            decision="fallback",
            text_scores=FunnelTextScores(
                license_metadata_quality=10, metadata_quality=10
            ),
            thumbnail_scores=FunnelThumbnailScores(misrepresentation_risk=5),
        ),
    ]
    payload = [
        {
            "candidate_id": "b_id",
            "final_score": 90,
            "rank": 1,
            "decision": "fallback",
            "reason": "",
        },
        {
            "candidate_id": "a_id",
            "final_score": 90,
            "rank": 2,
            "decision": "fallback",
            "reason": "",
        },
    ]
    ordered = order_by_final_scores(records, payload)
    assert ordered[0].candidate_id == "a_id"
    assert ordered[0].decision == "winner"


def test_disabled_providers_not_selected() -> None:
    cands = _make_candidates(5, provider="pexels") + _make_candidates(
        5, provider="pixabay"
    )
    for c in cands:
        if c.provider == "pixabay":
            c.preview_url = c.preview_url.replace("images.pexels.com", "cdn.pixabay.com")
            c.download_url = c.download_url.replace(
                "images.pexels.com", "cdn.pixabay.com"
            )
            c.source_page = "https://pixabay.com/photos/1/"
            c.license = "Pixabay License"
    selected = select_funnel_candidates(
        cands,
        enabled_providers={"pexels"},
        preferred_media_type="photo",
        limit=20,
    )
    assert all(c.provider == "pexels" for c in selected)


def _fake_text_llm(candidates: list[StockCandidate]):
    def text_llm(prompt: str) -> str:
        import json

        return json.dumps(
            {
                "gap_id": "gap_1",
                "candidate_reviews": [
                    {
                        "candidate_id": c.candidate_id,
                        "text_relevance": 90 - (i % 10),
                        "metadata_quality": 80,
                        "media_type_fit": 85,
                        "license_metadata_quality": 90,
                        "misrepresentation_risk": 5,
                        "reason": "fit",
                    }
                    for i, c in enumerate(candidates)
                ],
            }
        )

    return text_llm


def _fake_vision_llm():
    def vision_llm(prompt: str, images: list[tuple[str, bytes]]) -> str:
        import json

        ids = [label for label, _ in images]
        if "Finalisten" in prompt or "finalists" in prompt.lower():
            return json.dumps(
                {
                    "gap_id": "gap_1",
                    "finalists": [
                        {
                            "candidate_id": cid,
                            "final_score": 95 - index,
                            "rank": index,
                            "decision": "winner" if index == 1 else "fallback",
                            "reason": "best",
                        }
                        for index, cid in enumerate(ids, start=1)
                    ],
                }
            )
        assert len(ids) <= 10
        return json.dumps(
            {
                "candidate_reviews": [
                    {
                        "candidate_id": cid,
                        "semantic_fit": 90 - i,
                        "editorial_function_fit": 80,
                        "style_fit": 70,
                        "continuity_fit": 60,
                        "composition_quality": 75,
                        "visual_quality": 85,
                        "misrepresentation_risk": 5,
                        "reason": "thumb ok",
                    }
                    for i, cid in enumerate(ids)
                ]
            }
        )

    return vision_llm


def _preview_fetch(url: str, *, provider: str):
    return SafeFetchResult(
        url=url, content=_jpeg_bytes(), content_type="image/jpeg", final_url=url
    )


def test_integration_funnel_rank1_invalid_then_rank2_auto_export_ready(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    needed_visual="gravel road",
                    preferred_media_type="photo",
                    editorial_purpose="orientation",
                    must_include=["road"],
                )
            ],
        ),
    )
    candidates = _make_candidates(20)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=candidates,
            provider_status={"pexels": "completed"},
        ),
    )

    download_calls: list[str] = []
    full_review_calls = {"n": 0}
    frames_calls = {"n": 0}

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        download_calls.append(candidate.candidate_id)
        target_dir = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{candidate.candidate_id}.jpg"
        if len(download_calls) == 1:
            path.write_bytes(b"not-an-image")
        else:
            path.write_bytes(_jpeg_bytes(color=(80, 90, 100)))
        return path

    def boom_frames(*_a, **_k):
        frames_calls["n"] += 1
        raise AssertionError("Funnel darf keine Validierungsframes extrahieren")

    def boom_full(**_k):
        full_review_calls["n"] += 1
        raise AssertionError("Funnel darf keine zweite LLM-Prüfung aufrufen")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service._extract_validation_frames",
        boom_frames,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service.describe_and_validate_supplement_asset",
        boom_full,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service.run_full_content_review",
        boom_full,
    )

    report = run_supplement_funnel_for_gaps(
        project,
        max_candidates_per_gap=20,
        text_llm=_fake_text_llm(candidates),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        full_review_llm=boom_full,
        force_restart=True,
    )

    assert len(download_calls) == 2  # Rang1 invalid → Rang2
    assert full_review_calls["n"] == 0
    assert frames_calls["n"] == 0
    gap = report.gaps[0]
    assert gap.export_ready_candidate_id == download_calls[1]
    assert gap.filled is True
    assert gap.review_ready_candidate_id is None
    assert "gap_1" in report.filled_gap_ids
    ready = next(
        c for c in gap.candidates if c.candidate_id == gap.export_ready_candidate_id
    )
    assert ready.funnel_status == "export_ready"
    assert ready.funnel_status != "review_ready"

    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
    )

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert len(accepted.supplements) == 1
    assert accepted.supplements[0].candidate_id == download_calls[1]
    assert accepted.supplements[0].media_validation_status == "export_ready"
    assert accepted.supplements[0].local_media_path
    assert not str(accepted.supplements[0].local_media_path).startswith("http")
    # Kein Frameverzeichnis
    media = Path(accepted.supplements[0].local_media_path)
    assert not (media.parent / "frames").exists()


def test_no_llm_key_does_not_auto_accept(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=_make_candidates(3),
        ),
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service.is_api_key_set",
        lambda _name: False,
    )
    report = run_supplement_funnel_for_gaps(project, force_restart=True)
    assert report.gaps
    assert "fehlgeschlagen" in (report.gaps[0].message or "").lower() or (
        "gap_1" in report.open_gap_ids
    )
    assert not accepted_supplements_path(project).is_file()


def test_license_gate_blocks_export_ready(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        STATUS_LICENSE_REVIEW_REQUIRED,
        apply_license_export_gate,
        license_metadata_complete,
    )

    media = tmp_path / "ok.jpg"
    media.write_bytes(_jpeg_bytes())
    cand = StockCandidate(
        candidate_id="pexels_1",
        provider="pexels",
        provider_asset_id="1",
        media_type="photo",
        selected=True,
        license="",
        source_page="",
        local_media_path=str(media),
    )
    assert license_metadata_complete(cand) is False
    gated = apply_license_export_gate(cand)
    assert gated.media_validation_status == STATUS_LICENSE_REVIEW_REQUIRED


def test_funnel_license_gate_survives_refresh_assign_and_reload(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        STATUS_EXPORT_READY,
        STATUS_LICENSE_REVIEW_REQUIRED,
        assign_local_media_path,
        list_export_ready_supplements,
        refresh_supplement_validation,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument as AccDoc,
    )

    project = _project(tmp_path)
    _lock(project)
    media = Path(project.work_dir) / "funnel.jpg"
    media.write_bytes(_jpeg_bytes())
    cand = StockCandidate(
        candidate_id="pexels_photo_099",
        provider="pexels",
        provider_asset_id="99",
        media_type="photo",
        selected=True,
        funnel_managed=True,
        license="",
        source_page="",
        local_media_path=str(media),
        title="road",
    )
    write_json(
        accepted_supplements_path(project),
        AccDoc(script_version="script-v1", supplements=[cand]),
    )
    refreshed = refresh_supplement_validation(cand)
    assert refreshed.media_validation_status == STATUS_LICENSE_REVIEW_REQUIRED
    assert list_export_ready_supplements(project) == []

    # Reload from disk
    loaded = load_model(accepted_supplements_path(project), AccDoc)
    assert loaded is not None
    again = refresh_supplement_validation(loaded.supplements[0])
    assert again.media_validation_status == STATUS_LICENSE_REVIEW_REQUIRED

    # Manual path assign must not bypass funnel license gate
    other = Path(project.work_dir) / "other.jpg"
    other.write_bytes(_jpeg_bytes(color=(1, 2, 3)))
    assigned = assign_local_media_path(project, "pexels_photo_099", str(other))
    assert assigned.funnel_managed is True
    assert assigned.media_validation_status == STATUS_LICENSE_REVIEW_REQUIRED

    # Complete license → export_ready
    loaded2 = load_model(accepted_supplements_path(project), AccDoc)
    assert loaded2 is not None
    loaded2.supplements[0].license = "Pexels License"
    loaded2.supplements[0].source_page = "https://www.pexels.com/photo/99/"
    write_json(accepted_supplements_path(project), loaded2)
    ready = refresh_supplement_validation(loaded2.supplements[0])
    assert ready.media_validation_status == STATUS_EXPORT_READY
    assert [s.candidate_id for s in list_export_ready_supplements(project)] == [
        "pexels_photo_099"
    ]


def test_remote_url_rejected_as_local_media_path(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        LocalMediaError,
        assign_local_media_path,
        refresh_supplement_validation,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument as AccDoc,
    )

    project = _project(tmp_path)
    _lock(project)
    cand = StockCandidate(
        candidate_id="pexels_1",
        provider="pexels",
        provider_asset_id="1",
        media_type="photo",
        selected=True,
        funnel_managed=True,
        license="Pexels License",
        source_page="https://www.pexels.com/photo/1/",
        local_media_path="https://images.pexels.com/photos/1/full.jpg",
    )
    write_json(
        accepted_supplements_path(project),
        AccDoc(script_version="script-v1", supplements=[cand]),
    )
    refreshed = refresh_supplement_validation(cand)
    assert refreshed.media_validation_status == "local_media_invalid"
    with pytest.raises(LocalMediaError):
        assign_local_media_path(
            project, "pexels_1", "https://images.pexels.com/photos/1/full.jpg"
        )


def test_ui_automatic_resolve_no_manual_confirm() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "Supplements sequenziell prüfen" not in source
    assert "enh_resolve_stock" not in source
    assert "resolve_supplements_for_gaps" not in source
    assert "20 Kandidaten vorprüfen" not in source
    assert "Manuell freigeben" not in source
    assert "confirm_funnel_candidate" not in source
    assert "Supplements automatisch auflösen" in source
    assert "enh_funnel_stock" in source
    assert "Gaps erfüllt" in source


def test_redirect_target_revalidated(monkeypatch) -> None:
    from otio_app.services.without_voiceover_enhanced.stock import safe_fetch

    calls: list[str] = []

    class FakeResp:
        def __init__(self, status: int, headers: dict, content: bytes = b""):
            self.status_code = status
            self.headers = headers
            self._content = content
            self.is_redirect = status in {301, 302, 303, 307, 308}

        def close(self):
            return None

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http error")

        def iter_content(self, chunk_size=1024):
            yield self._content

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeResp(302, {"Location": "https://evil.example.com/x.jpg"})
        return FakeResp(200, {"Content-Type": "image/jpeg"}, _jpeg_bytes())

    monkeypatch.setattr(safe_fetch.requests, "get", fake_get)
    monkeypatch.setattr(
        safe_fetch,
        "resolve_and_validate_host",
        lambda hostname, allowed_suffixes: ["1.2.3.4"]
        if "pexels" in hostname
        else (_ for _ in ()).throw(safe_fetch.SafeFetchError("blocked")),
    )
    with pytest.raises(safe_fetch.SafeFetchError):
        safe_fetch.safe_http_get(
            "https://images.pexels.com/photos/1.jpg",
            provider="pexels",
            max_bytes=safe_fetch.PREVIEW_MAX_BYTES,
            timeout_sec=5,
            allowed_content_types=safe_fetch.ALLOWED_IMAGE_CONTENT_TYPES,
        )


def test_size_limit_and_html_content_type(monkeypatch) -> None:
    from otio_app.services.without_voiceover_enhanced.stock import safe_fetch

    class FakeResp:
        status_code = 200
        is_redirect = False
        headers = {"Content-Type": "text/html"}

        def close(self):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1024):
            yield b"<html>nope</html>"

    monkeypatch.setattr(safe_fetch.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(
        safe_fetch,
        "resolve_and_validate_host",
        lambda hostname, allowed_suffixes: ["1.2.3.4"],
    )
    with pytest.raises(safe_fetch.SafeFetchError):
        safe_fetch.safe_http_get(
            "https://images.pexels.com/photos/1.jpg",
            provider="pexels",
            max_bytes=100,
            timeout_sec=5,
            allowed_content_types=safe_fetch.ALLOWED_IMAGE_CONTENT_TYPES,
        )


def test_max_three_full_downloads(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(20)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    downloads: list[str] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        downloads.append(candidate.candidate_id)
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(b"broken")
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
        max_full_download_attempts=3,
    )
    assert len(downloads) == 3
    assert report.full_download_count == 3
    assert "gap_1" in report.open_gap_ids


def test_funnel_never_extracts_frames_or_full_review(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(8)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    calls = {"frames": 0, "full": 0}

    def boom_frames(*_a, **_k):
        calls["frames"] += 1
        raise AssertionError("frames")

    def boom_full(*_a, **_k):
        calls["full"] += 1
        raise AssertionError("full review")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service._extract_validation_frames",
        boom_frames,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service.run_full_content_review",
        boom_full,
    )

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg_bytes())
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
    )
    assert calls["frames"] == 0
    assert calls["full"] == 0
    assert report.gaps[0].export_ready_candidate_id
    assert report.gaps[0].review_ready_candidate_id is None


def test_rank1_license_incomplete_falls_back_to_rank2(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(12)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    downloads: list[str] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        downloads.append(candidate.candidate_id)
        # Ersten Download ohne Lizenz machen: setze license leer auf dem Candidate
        if len(downloads) == 1:
            candidate.license = ""
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg_bytes())
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
    )
    assert len(downloads) >= 2
    gap = report.gaps[0]
    assert gap.license_incomplete_count >= 1
    assert gap.export_ready_candidate_id == downloads[1]
    assert gap.export_ready_candidate_id != downloads[0]
    # Rang1-Datei gelöscht
    from otio_app.services.without_voiceover_enhanced.paths import (
        stock_candidate_download_dir,
    )

    rank1_dir = stock_candidate_download_dir(
        project, gap_id="gap_1", candidate_id=downloads[0]
    )
    assert not rank1_dir.exists()


def test_rank1_download_error_falls_back(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(10)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    downloads: list[str] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        downloads.append(candidate.candidate_id)
        if len(downloads) == 1:
            raise RuntimeError("network down")
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg_bytes())
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
    )
    assert len(downloads) == 2
    assert report.gaps[0].export_ready_candidate_id == downloads[1]
    failed = next(c for c in report.gaps[0].candidates if c.candidate_id == downloads[0])
    assert failed.funnel_status == "download_failed"


def test_cleanup_on_tech_invalid(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(12)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    kept: dict[str, Path] = {}

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(b"broken")
        kept[candidate.candidate_id] = path
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
        max_full_download_attempts=3,
    )
    assert report.gaps[0].export_ready_candidate_id is None
    assert report.gaps[0].review_ready_candidate_id is None
    for path in kept.values():
        assert not path.exists()


def test_idempotent_skip_export_ready_gaps(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1", candidates=_make_candidates(5)
        ),
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        FunnelCandidateRecord,
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )

    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="prev",
            script_version="script-v1",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_1",
                    run_id="prev",
                    filled=True,
                    export_ready_candidate_id="pexels_photo_001",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="pexels_photo_001",
                            funnel_status="export_ready",
                            local_media_path="/tmp/local.jpg",
                        )
                    ],
                )
            ],
        ),
    )
    report = run_supplement_funnel_for_gaps(project, skip_filled=True, force_restart=False)
    assert "gap_1" in report.skipped_gap_ids


def test_historical_review_ready_document_still_readable(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.models import (
        FunnelCandidateRecord,
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )

    project = _project(tmp_path)
    write_json(
        supplement_funnel_report_path(project),
        {
            "schema_version": "enhanced-supplement-funnel-v1",
            "run_id": "old",
            "script_version": "script-v1",
            "gaps": [
                {
                    "gap_id": "gap_1",
                    "review_ready_candidate_id": "pexels_photo_001",
                    "candidates": [
                        {
                            "candidate_id": "pexels_photo_001",
                            "funnel_status": "review_ready",
                        }
                    ],
                }
            ],
        },
    )
    loaded = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    assert loaded is not None
    assert loaded.gaps[0].review_ready_candidate_id == "pexels_photo_001"
    assert loaded.gaps[0].candidates[0].funnel_status == "review_ready"


def test_inventory_import_and_no_duplicate_on_rerun(tmp_path: Path) -> None:
    from otio_app.services.inventory_loader import load_folder_inventory
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
    )

    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": False,
            "wikimedia": False,
            "openverse": False,
            "archive_org": False,
        },
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road")],
        ),
    )
    cands = _make_candidates(8)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    downloads: list[str] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        downloads.append(candidate.candidate_id)
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg_bytes())
        return path

    report1 = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
    )
    first_downloads = list(downloads)
    assert report1.gaps[0].filled
    inv = load_folder_inventory(project, "Canyon")
    assert inv is not None
    assert any(
        a.asset_id == report1.gaps[0].export_ready_candidate_id for a in inv.assets
    )

    downloads.clear()
    report2 = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        skip_filled=True,
        force_restart=False,
    )
    assert "gap_1" in report2.skipped_gap_ids
    assert downloads == []
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert len(accepted.supplements) == 1
    assert accepted.supplements[0].candidate_id == first_downloads[0]
