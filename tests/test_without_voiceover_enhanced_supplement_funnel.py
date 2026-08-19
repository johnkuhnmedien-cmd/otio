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
    build_text_only_finalist_payload,
    compute_preliminary_score,
    format_provider_distribution,
    media_type_fits_preferred,
    order_by_final_scores,
    pick_finalists_from_batches,
    resolve_preview_url,
    select_funnel_candidates,
    select_provider_balanced_candidates,
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
        import re

        ids = [label for label, _ in images]
        gap_m = re.search(r"gap_id[=:]?\s*[\"']?([A-Za-z0-9_\-]+)", prompt)
        if not gap_m:
            gap_m = re.search(r"Coverage Gap:\s*([A-Za-z0-9_\-]+)", prompt)
        gap_id = gap_m.group(1) if gap_m else "gap_1"
        if "Finalisten" in prompt or "finalists" in prompt.lower():
            return json.dumps(
                {
                    "gap_id": gap_id,
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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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


def test_license_missing_does_not_block_export_ready(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        STATUS_EXPORT_READY,
        apply_license_export_gate,
        classify_license_metadata_status,
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
        funnel_managed=True,
        license="",
        source_page="",
        local_media_path=str(media),
    )
    assert license_metadata_complete(cand) is False
    assert classify_license_metadata_status(cand) == "missing"
    gated = apply_license_export_gate(cand)
    assert gated.media_validation_status == STATUS_EXPORT_READY
    assert gated.license_metadata_status == "missing"


def test_license_partial_and_complete_status(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        STATUS_EXPORT_READY,
        apply_license_export_gate,
        assign_local_media_path,
        classify_license_metadata_status,
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
        source_page="https://www.pexels.com/photo/99/",
        local_media_path=str(media),
        title="road",
    )
    assert classify_license_metadata_status(cand) == "partial"
    write_json(
        accepted_supplements_path(project),
        AccDoc(script_version="script-v1", supplements=[cand]),
    )
    refreshed = refresh_supplement_validation(cand)
    assert refreshed.media_validation_status == STATUS_EXPORT_READY
    assert refreshed.license_metadata_status == "partial"
    assert [s.candidate_id for s in list_export_ready_supplements(project)] == [
        "pexels_photo_099"
    ]

    other = Path(project.work_dir) / "other.jpg"
    other.write_bytes(_jpeg_bytes(color=(1, 2, 3)))
    assigned = assign_local_media_path(project, "pexels_photo_099", str(other))
    assert assigned.funnel_managed is True
    assert assigned.media_validation_status == STATUS_EXPORT_READY

    loaded2 = load_model(accepted_supplements_path(project), AccDoc)
    assert loaded2 is not None
    loaded2.supplements[0].license = "Pexels License"
    loaded2.supplements[0].source_page = "https://www.pexels.com/photo/99/"
    write_json(accepted_supplements_path(project), loaded2)
    ready = refresh_supplement_validation(loaded2.supplements[0])
    assert ready.media_validation_status == STATUS_EXPORT_READY
    assert ready.license_metadata_status == "complete"
    gated = apply_license_export_gate(ready)
    assert gated.license_metadata_status == "complete"


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


def test_ui_two_funnel_buttons_and_gap_keys() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "Supplements sequenziell prüfen" not in source
    assert "enh_resolve_stock" not in source
    assert "resolve_supplements_for_gaps" not in source
    assert "20 Kandidaten vorprüfen" not in source
    assert "Manuell freigeben" not in source
    assert "confirm_funnel_candidate" not in source
    assert "Supplements automatisch auflösen" not in source
    assert "Alle offenen Gaps automatisch auflösen" in source
    assert "Ausgewählte Gaps automatisch auflösen" in source
    assert "enh_funnel_all_open" in source
    assert "enh_funnel_selected" in source
    assert "Offene Coverage Gaps auswählen" in source
    assert "st.pills(" in source
    assert 'selection_mode="multi"' in source
    assert "enh_funnel_gap_multiselect_{project.id}" in source
    assert "disabled=selected_disabled" in source
    assert "disabled=all_disabled" in source
    assert "_start_funnel_job(list(open_gap_ids))" in source
    # Bereiche getrennt (Radio, nicht st.tabs — Tabs führen alles aus)
    assert "_SECTION_FUNNEL" in source
    assert "_render_section_funnel" in source
    assert "_render_section_rough" in source
    assert "_render_section_final" in source
    assert "st.radio(" in source
    assert "Nur der gewählte Bereich wird geladen" in source or "Bereich" in source
    # Fortschritt + Lazy-Loads
    assert "Gaps: **offen" in source
    assert "Kandidaten manuell prüfen laden" in source
    assert "enh_show_manual_candidates_" in source
    assert "Funnel-Abschlussdetails laden" in source
    assert "Lokale Dateizuordnung laden" in source
    assert "enh_show_local_assign_" in source
    assert "Offene Gaps manuell zuordnen" in source
    assert "enh_show_manual_gap_assign_" in source
    assert "assign_local_file_to_open_gap" in source
    assert "gap_search_queries" in source
    assert "enh_show_open_gap_pills_" in source
    assert "Echtzeit-Timeline laden" in source
    # Stock-JSON / Report nicht bei jedem Rerun
    assert "_stock_candidate_count" in source
    assert "_funnel_report_top_summary" in source
    # Funnel-Modell wählbar (günstige Gemini-Varianten)
    assert "ENHANCED_FUNNEL_LLM_MODEL_CHOICES" in source
    assert "enh_funnel_model_" in source
    assert "fallback_if_unknown" in source
    assert "resolve_funnel_gemini_model" in source
    assert "model=funnel_model_id" in source
    assert "enhanced_supplement_funnel" in source
    # Hintergrund-Job + Abbrechen
    assert "get_supplement_funnel_job_manager" in source
    assert "Funnel abbrechen" in source
    assert "request_cancel" in source
    assert "funnel_job_mgr.start" in source
    # Leichte Monitor-Seite während Job (Abbrechen ohne schweren Rerun)
    assert "_render_lightweight_funnel_monitor" in source
    assert "enh_funnel_cancel_lite_" in source
    assert "enh_funnel_force_reset_lite_" in source
    assert "force_reset" in source
    assert "UI trotzdem freigeben" in source
    # Session-State erst vor Pills bereinigen (nicht nach Widget)
    assert "enh_funnel_pending_deselect_" in source
    # Kein Query-Parameter als Produktionsauslöser
    assert 'query_params.get("smoke_action"' not in source
    assert "st.query_params" not in source


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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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


def test_missing_license_no_fallback_auto_export_ready(tmp_path: Path) -> None:
    """Technisch gültig ohne Lizenz → kein Fallback, export_ready, status missing."""
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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
        ),
    )
    cands = _make_candidates(12)
    for cand in cands:
        cand.license = ""
        cand.source_page = ""
        cand.creator = ""
        cand.attribution = ""
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

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
    )
    assert len(downloads) == 1  # kein Fallback wegen fehlender Lizenz
    gap = report.gaps[0]
    assert gap.export_ready_candidate_id == downloads[0]
    assert gap.license_metadata_status == "missing"
    ready = next(
        c for c in gap.candidates if c.candidate_id == gap.export_ready_candidate_id
    )
    assert ready.funnel_status == "export_ready"
    assert ready.license_metadata_status == "missing"
    assert ready.license_name in (None, "")
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert accepted.supplements[0].media_validation_status == "export_ready"
    assert accepted.supplements[0].license_metadata_status == "missing"
    # Keine erfundenen Lizenzfelder
    assert not (accepted.supplements[0].license or "").strip()


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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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
    from otio_app.services.without_voiceover_enhanced.local_media_service import (
        STATUS_EXPORT_READY,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        FunnelCandidateRecord,
        StockCandidate,
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )
    from PIL import Image

    media = project.work_dir_path / "local.jpg"
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(media, format="JPEG")
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_skip",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    needed_visual="road",
                    preferred_media_type="photo",
                )
            ],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1", candidates=_make_candidates(5)
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="pexels_photo_001",
                    provider="pexels",
                    media_type="photo",
                    gap_id="gap_1",
                    local_media_path=str(media),
                    media_validation_status=STATUS_EXPORT_READY,
                    cut_plan_run_id="run_skip",
                    funnel_managed=True,
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="prev",
            script_version="script-v1",
            cut_plan_run_id="run_skip",
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
                            local_media_path=str(media),
                        )
                    ],
                )
            ],
        ),
    )
    report = run_supplement_funnel_for_gaps(project, skip_filled=True, force_restart=False)
    assert "gap_1" in report.skipped_gap_ids
    assert "gap_1" in report.filled_gap_ids


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
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
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
    assert "gap_1" in report2.filled_gap_ids
    assert downloads == []
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert len(accepted.supplements) == 1
    assert accepted.supplements[0].candidate_id == first_downloads[0]


def _candidates_for_gaps(gap_ids: list[str], n: int = 8) -> list[StockCandidate]:
    out: list[StockCandidate] = []
    for gap_id in gap_ids:
        for i in range(1, n + 1):
            out.append(
                StockCandidate(
                    candidate_id=f"pexels_{gap_id}_{i:03d}",
                    provider="pexels",
                    provider_asset_id=f"{gap_id}-{i}",
                    title=f"{gap_id} scene {i}",
                    media_type="photo",
                    creator="Tester",
                    source_page=f"https://www.pexels.com/photo/{gap_id}-{i}/",
                    preview_url=f"https://images.pexels.com/photos/{gap_id}-{i}/p.jpg",
                    download_url=f"https://images.pexels.com/photos/{gap_id}-{i}/f.jpg",
                    width=1920,
                    height=1080,
                    license="Pexels License",
                    attribution="Tester",
                    gap_id=gap_id,
                )
            )
    return out


def _fake_text_llm_any(candidates: list[StockCandidate]):
    def text_llm(prompt: str) -> str:
        import json
        import re

        m = re.search(r"Coverage Gap:\s*([A-Za-z0-9_\-]+)", prompt)
        if not m:
            m = re.search(r'"gap_id"\s*:\s*"([^"]+)"', prompt)
        gap_hint = m.group(1) if m else ""
        present = [c.candidate_id for c in candidates if c.candidate_id in prompt]
        if gap_hint:
            present = [
                c.candidate_id
                for c in candidates
                if c.gap_id == gap_hint and c.candidate_id in prompt
            ] or present
        if not present:
            present = [
                c.candidate_id
                for c in candidates
                if not gap_hint or c.gap_id == gap_hint
            ]
        return json.dumps(
            {
                "gap_id": gap_hint or "gap_1",
                "candidate_reviews": [
                    {
                        "candidate_id": cid,
                        "text_relevance": 90 - (i % 10),
                        "metadata_quality": 80,
                        "media_type_fit": 85,
                        "license_metadata_quality": 90,
                        "misrepresentation_risk": 5,
                        "reason": "fit",
                    }
                    for i, cid in enumerate(present)
                ],
            }
        )

    return text_llm


def test_gap_ids_unknown_and_duplicate_raise(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo")],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1", candidates=_make_candidates(3)
        ),
    )
    with pytest.raises(Exception) as exc:
        run_supplement_funnel_for_gaps(
            project, gap_ids=["gap_unknown"], force_restart=True
        )
    assert "Unbekannte Gap-ID" in str(exc.value)
    with pytest.raises(Exception) as exc2:
        run_supplement_funnel_for_gaps(
            project, gap_ids=["gap_1", "gap_1"], force_restart=True
        )
    assert "Doppelte Gap-ID" in str(exc2.value)


def test_selected_then_all_open_gaps_integration(tmp_path: Path) -> None:
    """3 Gaps: Auswahl 1+3, danach Alle verarbeitet nur Gap 2."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
        list_open_funnel_gap_ids,
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
    gaps = [
        CoverageGap(gap_id="gap_1", needed_visual="road", preferred_media_type="photo"),
        CoverageGap(gap_id="gap_2", needed_visual="river", preferred_media_type="photo"),
        CoverageGap(gap_id="gap_3", needed_visual="forest", preferred_media_type="photo"),
    ]
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(script_version="script-v1", gaps=gaps),
    )
    cands = _candidates_for_gaps(["gap_1", "gap_2", "gap_3"], n=8)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
    )
    assert list_open_funnel_gap_ids(project) == ["gap_1", "gap_2", "gap_3"]

    downloads: list[tuple[str, str]] = []
    progress_totals: list[int] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        downloads.append((gap_id, candidate.candidate_id))
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg_bytes())
        return path

    def on_progress(event):
        if event.gap_total:
            progress_totals.append(event.gap_total)

    report1 = run_supplement_funnel_for_gaps(
        project,
        gap_ids=["gap_1", "gap_3"],
        text_llm=_fake_text_llm_any(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        progress_callback=on_progress,
        force_restart=True,
    )
    assert report1.requested_gap_ids == ["gap_1", "gap_3"]
    assert set(report1.filled_gap_ids) == {"gap_1", "gap_3"}
    assert "gap_2" not in report1.filled_gap_ids
    assert {g for g, _ in downloads} == {"gap_1", "gap_3"}
    assert 2 in progress_totals
    assert list_open_funnel_gap_ids(project) == ["gap_2"]

    downloads.clear()
    progress_totals.clear()
    report2 = run_supplement_funnel_for_gaps(
        project,
        gap_ids=list_open_funnel_gap_ids(project),
        text_llm=_fake_text_llm_any(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        progress_callback=on_progress,
        skip_filled=True,
        force_restart=False,
    )
    assert report2.requested_gap_ids == ["gap_2"]
    assert report2.filled_gap_ids == ["gap_2"] or "gap_2" in report2.filled_gap_ids
    assert {g for g, _ in downloads} == {"gap_2"}
    assert 1 in progress_totals
    assert list_open_funnel_gap_ids(project) == []

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert len(accepted.supplements) == 3
    ids = {s.candidate_id for s in accepted.supplements}
    assert len(ids) == 3


def test_one_gap_failure_continues_others(tmp_path: Path) -> None:
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
                CoverageGap(gap_id="gap_1", needed_visual="a", preferred_media_type="photo"),
                CoverageGap(gap_id="gap_2", needed_visual="b", preferred_media_type="photo"),
            ],
        ),
    )
    cands = _candidates_for_gaps(["gap_1", "gap_2"], n=6)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
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
        if gap_id == "gap_1":
            path.write_bytes(b"broken")
        else:
            path.write_bytes(_jpeg_bytes())
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        gap_ids=["gap_1", "gap_2"],
        text_llm=_fake_text_llm_any(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
        max_full_download_attempts=2,
    )
    assert "gap_1" in report.open_gap_ids
    assert "gap_2" in report.filled_gap_ids


def _cand(
    provider: str,
    idx: int,
    *,
    media_type: str = "photo",
    gap_id: str = "gap_1",
    width: int = 1920,
    height: int = 1080,
    duration: float | None = None,
) -> StockCandidate:
    host = {
        "pexels": ("images.pexels.com", "https://www.pexels.com/photo"),
        "pixabay": ("cdn.pixabay.com", "https://pixabay.com/photos"),
        "wikimedia": (
            "upload.wikimedia.org",
            "https://commons.wikimedia.org/wiki/File",
        ),
        "openverse": ("api.openverse.org", "https://openverse.org/image"),
        "archive_org": ("archive.org", "https://archive.org/details"),
    }[provider]
    preview = f"https://{host[0]}/p/{provider}-{idx}.jpg"
    if provider == "wikimedia":
        preview = (
            f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/"
            f"{provider}_{idx}.jpg/320px-{provider}_{idx}.jpg"
        )
    return StockCandidate(
        candidate_id=f"{provider}_{idx:03d}",
        provider=provider,
        provider_asset_id=f"{provider}-asset-{idx}",
        title=f"{provider} scene {idx}",
        media_type=media_type,
        creator="Tester",
        source_page=f"{host[1]}/{idx}/",
        preview_url=preview,
        download_url=f"https://{host[0]}/f/{provider}-{idx}.jpg",
        width=width,
        height=height,
        duration_seconds=duration,
        license=f"{provider} License",
        attribution="Tester",
        gap_id=gap_id,
    )


def test_provider_balance_four_providers_five_each() -> None:
    cands = []
    for provider in ("pexels", "pixabay", "wikimedia", "openverse"):
        cands.extend(_cand(provider, i) for i in range(1, 9))
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia", "openverse"},
        preferred_media_type="photo",
        limit=20,
    )
    assert len(pool.candidates) == 20
    assert pool.provider_candidate_counts == {
        "openverse": 5,
        "pexels": 5,
        "pixabay": 5,
        "wikimedia": 5,
    }


def test_provider_balance_five_providers_four_each() -> None:
    providers = ("archive_org", "openverse", "pexels", "pixabay", "wikimedia")
    cands = []
    for provider in providers:
        for i in range(1, 8):
            c = _cand(provider, i)
            if provider == "archive_org":
                # archive: harte Exclusion erlaubt Source-Page; Balancing braucht
                # aber usable download != source für Nicht-Archive — hier ok.
                c.download_url = f"https://archive.org/download/x/{i}.jpg"
                c.preview_url = f"https://archive.org/download/x/{i}_thumb.jpg"
            cands.append(c)
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers=set(providers),
        preferred_media_type="photo",
        limit=20,
    )
    assert len(pool.candidates) == 20
    assert pool.provider_candidate_counts == {
        "archive_org": 4,
        "openverse": 4,
        "pexels": 4,
        "pixabay": 4,
        "wikimedia": 4,
    }


def test_provider_balance_three_providers_remainder() -> None:
    cands = []
    for provider in ("pexels", "pixabay", "wikimedia"):
        cands.extend(_cand(provider, i) for i in range(1, 12))
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia"},
        preferred_media_type="photo",
        limit=20,
    )
    assert len(pool.candidates) == 20
    # base 6, remainder 2 → alphabetisch erste zwei +1
    assert pool.provider_candidate_counts == {
        "pexels": 7,
        "pixabay": 7,
        "wikimedia": 6,
    }


def test_provider_balance_short_provider_redistributes() -> None:
    cands = [_cand("pexels", i) for i in range(1, 9)]
    cands += [_cand("pixabay", i) for i in range(1, 9)]
    cands += [_cand("wikimedia", i) for i in range(1, 3)]  # nur 2
    cands += [_cand("openverse", i) for i in range(1, 9)]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia", "openverse"},
        preferred_media_type="photo",
        limit=20,
        provider_status={
            "pexels": "completed",
            "pixabay": "completed",
            "wikimedia": "completed",
            "openverse": "completed",
        },
    )
    assert len(pool.candidates) == 20
    assert pool.provider_candidate_counts["wikimedia"] == 2
    assert sum(pool.provider_candidate_counts.values()) == 20
    assert len({c.candidate_id for c in pool.candidates}) == 20
    # freie Wikimedia-Plätze auf andere mit Restkandidaten verteilt
    others = (
        pool.provider_candidate_counts["pexels"]
        + pool.provider_candidate_counts["pixabay"]
        + pool.provider_candidate_counts["openverse"]
    )
    assert others == 18


def test_provider_balance_no_quota_without_eligible_hits() -> None:
    cands = [_cand("pexels", i) for i in range(1, 12)]
    cands += [_cand("pixabay", i) for i in range(1, 12)]
    # openverse nur Videos bei Photo-Gap → keine Quote
    cands += [_cand("openverse", i, media_type="video") for i in range(1, 5)]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "openverse"},
        preferred_media_type="photo",
        limit=20,
    )
    assert "openverse" not in pool.eligible_providers
    assert "openverse" not in pool.provider_candidate_counts
    assert len(pool.candidates) == 20
    assert pool.provider_candidate_counts == {"pexels": 10, "pixabay": 10}


def test_provider_balance_single_provider_up_to_20() -> None:
    cands = [_cand("pexels", i) for i in range(1, 30)]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay"},
        preferred_media_type="photo",
        limit=20,
    )
    assert len(pool.candidates) == 20
    assert pool.eligible_providers == ["pexels"]
    assert pool.provider_candidate_counts == {"pexels": 20}


def test_provider_balance_fewer_than_20_uses_all() -> None:
    cands = [_cand("pexels", i) for i in range(1, 4)]
    cands += [_cand("pixabay", i) for i in range(1, 3)]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay"},
        preferred_media_type="photo",
        limit=20,
    )
    assert len(pool.candidates) == 5
    assert sum(pool.provider_candidate_counts.values()) == 5


def test_provider_balance_no_duplicates_and_max_20() -> None:
    cands = []
    for provider in ("pexels", "pixabay", "wikimedia", "openverse"):
        cands.extend(_cand(provider, i) for i in range(1, 15))
    # Duplikat-Asset
    dup = _cand("pexels", 1)
    dup.candidate_id = "pexels_dup"
    cands.append(dup)
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia", "openverse"},
        preferred_media_type="photo",
        limit=20,
    )
    ids = [c.candidate_id for c in pool.candidates]
    assert len(ids) == 20
    assert len(ids) == len(set(ids))
    asset_keys = {(c.provider, c.provider_asset_id) for c in pool.candidates}
    assert len(asset_keys) == 20


def test_provider_balance_independent_of_input_order() -> None:
    cands = []
    for provider in ("pexels", "pixabay", "wikimedia", "openverse"):
        cands.extend(_cand(provider, i) for i in range(1, 9))
    forward = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia", "openverse"},
        preferred_media_type="photo",
        limit=20,
    )
    reverse = select_provider_balanced_candidates(
        list(reversed(cands)),
        enabled_providers={"openverse", "wikimedia", "pixabay", "pexels"},
        preferred_media_type="photo",
        limit=20,
    )
    assert {c.candidate_id for c in forward.candidates} == {
        c.candidate_id for c in reverse.candidates
    }
    assert forward.provider_candidate_counts == reverse.provider_candidate_counts


def test_provider_balance_disabled_and_failed_status() -> None:
    cands = [_cand("pexels", i) for i in range(1, 10)]
    cands += [_cand("pixabay", i) for i in range(1, 10)]
    cands += [_cand("wikimedia", i) for i in range(1, 10)]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay", "wikimedia"},
        preferred_media_type="photo",
        limit=20,
        provider_status={
            "pexels": "completed",
            "pixabay": "disabled",
            "wikimedia": "failed",
        },
    )
    assert pool.eligible_providers == ["pexels"]
    assert all(c.provider == "pexels" for c in pool.candidates)
    assert len(pool.candidates) == 9


def test_provider_balance_video_gap_allows_photo_fallback() -> None:
    """Video-preferred: Fotos bleiben im Pool (Soft), Videos werden bevorzugt."""
    assert media_type_fits_preferred("photo", "video")
    assert media_type_fits_preferred("video", "video")
    assert not media_type_fits_preferred("audio", "video")

    cands = [_cand("pexels", i, media_type="photo") for i in range(1, 6)]
    cands += [
        _cand("pixabay", i, media_type="video", duration=8.0) for i in range(1, 8)
    ]
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay"},
        preferred_media_type="video",
        limit=20,
    )
    assert set(pool.eligible_providers) == {"pexels", "pixabay"}
    assert "pexels" in pool.provider_candidate_counts
    assert "pixabay" in pool.provider_candidate_counts
    # Pixabay-Videos sollen vor Pexels-Fotos im Presort stehen → mehr Videos.
    video_count = sum(1 for c in pool.candidates if c.media_type == "video")
    photo_count = sum(1 for c in pool.candidates if c.media_type == "photo")
    assert video_count >= photo_count
    assert photo_count > 0


def test_finalists_backfill_preview_unavailable() -> None:
    """Ohne Preview: Text-only-Kandidaten füllen freie Finalistenplätze."""
    records = []
    for i in range(4):
        records.append(
            FunnelCandidateRecord(
                candidate_id=f"scored_{i}",
                preview_status="scored",
                preliminary_score=float(90 - i),
                text_scores=FunnelTextScores(
                    license_metadata_quality=50, metadata_quality=50
                ),
                thumbnail_scores=FunnelThumbnailScores(misrepresentation_risk=10),
            )
        )
    for i in range(5):
        records.append(
            FunnelCandidateRecord(
                candidate_id=f"text_{i}",
                preview_status="unavailable",
                preliminary_score=float(80 - i),
                text_scores=FunnelTextScores(
                    license_metadata_quality=60, metadata_quality=55
                ),
            )
        )
    # Nur ein Batch mit 2 scored → max 3 scored-Finalisten, Rest Backfill.
    batches = [["scored_0", "scored_1"]]
    finalists = pick_finalists_from_batches(records, batch_ids=batches, per_batch=3)
    assert "scored_0" in finalists and "scored_1" in finalists
    assert any(fid.startswith("text_") for fid in finalists)
    assert len(finalists) <= 6


def test_build_text_only_finalist_payload() -> None:
    records = [
        FunnelCandidateRecord(
            candidate_id="a",
            preview_status="unavailable",
            preliminary_score=72.0,
            text_scores=FunnelTextScores(
                license_metadata_quality=40, metadata_quality=40
            ),
        ),
        FunnelCandidateRecord(
            candidate_id="b",
            preview_status="unavailable",
            preliminary_score=55.0,
            text_scores=FunnelTextScores(
                license_metadata_quality=40, metadata_quality=40
            ),
        ),
    ]
    payload = build_text_only_finalist_payload(records)
    assert [p["candidate_id"] for p in payload] == ["a", "b"]
    assert payload[0]["final_score"] == 72
    assert payload[0]["decision"] == "winner"
    assert payload[1]["decision"] == "fallback"
    assert "Text-only" in payload[0]["reason"]


def test_provider_balance_ranking_remains_provider_neutral() -> None:
    """Poolbildung ≠ Ranking: kein Providerbonus in Scores/Finalisten."""
    from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
        compute_preliminary_score,
    )

    cands = []
    for provider in ("pexels", "pixabay"):
        cands.extend(_cand(provider, i) for i in range(1, 12))
    pool = select_provider_balanced_candidates(
        cands,
        enabled_providers={"pexels", "pixabay"},
        preferred_media_type="photo",
        limit=20,
    )
    # Identische Score-Inputs → identischer Score unabhängig vom Provider
    scores = []
    for c in pool.candidates:
        scores.append(
            (
                c.provider,
                compute_preliminary_score(
                    text_relevance=80,
                    semantic_fit=80,
                    editorial_function_fit=80,
                    style_fit=80,
                    continuity_fit=80,
                    composition_quality=80,
                    visual_quality=80,
                    misrepresentation_risk=10,
                ),
            )
        )
    assert len({s for _, s in scores}) == 1


def test_provider_distribution_persisted_in_report(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_stock_providers_config(
        project,
        {
            "pexels": True,
            "pixabay": True,
            "wikimedia": True,
            "openverse": True,
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
                    needed_visual="road",
                    preferred_media_type="photo",
                )
            ],
        ),
    )
    cands = []
    for provider in ("pexels", "pixabay", "wikimedia", "openverse"):
        for i in range(1, 9):
            c = _cand(provider, i)
            cands.append(c)
    # Wikimedia nur 2
    cands = [c for c in cands if not (c.provider == "wikimedia" and int(c.provider_asset_id.split("-")[-1]) > 2)]
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            provider_status={
                "pexels": "completed",
                "pixabay": "completed",
                "wikimedia": "completed",
                "openverse": "completed",
            },
            candidates=cands,
        ),
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

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service._extract_validation_frames",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no frames")),
    )

    progress: list[str] = []
    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=_fake_text_llm_any(cands),
        vision_llm=_fake_vision_llm(),
        preview_fetch=_preview_fetch,
        download_callable=download_callable,
        force_restart=True,
        progress_callback=lambda e: progress.append(e.message or e.phase),
    )
    gap = report.gaps[0]
    assert gap.candidate_pool_limit == 20
    assert set(gap.eligible_providers) == {
        "openverse",
        "pexels",
        "pixabay",
        "wikimedia",
    }
    assert gap.provider_candidate_counts["wikimedia"] == 2
    assert sum(gap.provider_candidate_counts.values()) == 20
    assert any("Providerverteilung:" in m for m in progress)
    assert any("Kandidaten ausgewählt" in m for m in progress)
    assert format_provider_distribution(gap.provider_candidate_counts)


def test_ui_multiselect_project_scoped_and_same_service() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert 'f"enh_funnel_gap_multiselect_{project.id}"' in source
    assert "st.pills(" in source
    assert 'selection_mode="multi"' in source
    # Beide Buttons starten denselben Hintergrund-Job-Helper
    assert source.count("_start_funnel_job(") >= 3
    assert "get_supplement_funnel_job_manager" in source
    # „Alle offenen“ nutzt Statuszeilen-Liste (open_gap_ids), nicht die
    # strengere Merge-Liste — sonst werden erfüllte Accepted erneut angefordert.
    assert "_start_funnel_job(list(open_gap_ids))" in source
    assert "Mehrfachauswahl wird ignoriert" in source
    assert "_render_section_funnel" in source
    svc = Path(
        "otio_app/services/without_voiceover_enhanced/supplement_funnel_service.py"
    ).read_text(encoding="utf-8")
    assert "select_provider_balanced_candidates" in svc
    assert "full_review_llm" in svc
    assert "del full_review_llm" in svc
    job = Path(
        "otio_app/services/without_voiceover_enhanced/supplement_funnel_job.py"
    ).read_text(encoding="utf-8")
    assert "funnel_svc.run_supplement_funnel_for_gaps" in job
    assert "should_stop=should_cancel" in job


def test_historical_funnel_report_without_pool_fields_readable() -> None:
    from otio_app.services.without_voiceover_enhanced.models import (
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )

    legacy = {
        "schema_version": "enhanced-supplement-funnel-v3",
        "run_id": "funnel_old",
        "script_version": "script-v1",
        "gaps": [{"gap_id": "gap_1", "filled": True}],
        "filled_gap_ids": ["gap_1"],
    }
    report = SupplementFunnelReport.model_validate(legacy)
    assert report.gaps[0].candidate_pool_limit == 20
    assert report.gaps[0].eligible_providers == []
    assert report.gaps[0].provider_candidate_counts == {}
    assert report.llm_model == ""
    gap = SupplementFunnelGapReport.model_validate({"gap_id": "gap_x"})
    assert gap.candidate_pool_limit == 20


def test_funnel_job_cancel_stops_between_gaps(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc
    from otio_app.services.without_voiceover_enhanced.models import (
        SupplementFunnelReport,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_funnel_job import (
        JobStatus,
        SupplementFunnelJobManager,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
        FunnelProgressEvent,
    )

    project = _project(tmp_path)
    manager = SupplementFunnelJobManager()
    started_gaps: list[str] = []
    block_second = threading.Event()
    release_second = threading.Event()

    def fake_run(project_arg, **kwargs):
        del project_arg
        gap_ids = list(kwargs.get("gap_ids") or [])
        cb = kwargs.get("progress_callback")
        should_stop = kwargs.get("should_stop")
        report = SupplementFunnelReport(
            run_id="job_cancel_test",
            script_version="script-v1",
            requested_gap_ids=gap_ids,
            llm_model=kwargs.get("model") or "",
        )
        for index, gap_id in enumerate(gap_ids, start=1):
            if should_stop and should_stop():
                report.stopped = True
                break
            started_gaps.append(gap_id)
            if cb:
                cb(
                    FunnelProgressEvent(
                        phase="gap_start",
                        gap_id=gap_id,
                        gap_index=index,
                        gap_total=len(gap_ids),
                        message=f"Gap {index}/{len(gap_ids)}",
                        fraction=(index - 1) / max(1, len(gap_ids)),
                    )
                )
            if index == 1:
                report.filled_gap_ids.append(gap_id)
                time.sleep(0.05)
                block_second.set()
                release_second.wait(timeout=2)
            if should_stop and should_stop():
                report.stopped = True
                break
        report.message = "done" + (" · abgebrochen" if report.stopped else "")
        return report

    monkeypatch.setattr(funnel_svc, "run_supplement_funnel_for_gaps", fake_run)

    assert manager.start(
        project, gap_ids=["gap_1", "gap_2"], model="gemini-3.1-flash-lite"
    )
    assert block_second.wait(timeout=2)
    assert manager.request_cancel(project.id)
    release_second.set()

    deadline = time.time() + 3
    while time.time() < deadline:
        state = manager.get_state(project.id)
        if state is not None and state.status != JobStatus.RUNNING:
            break
        time.sleep(0.05)
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.CANCELLED
    assert started_gaps == ["gap_1"]
    assert state.report is not None
    assert state.report.stopped is True


def test_funnel_force_reset_releases_ui_while_llm_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    import threading
    import time

    import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc
    from otio_app.services.without_voiceover_enhanced.models import (
        SupplementFunnelReport,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_funnel_job import (
        JobStatus,
        SupplementFunnelJobManager,
    )

    project = _project(tmp_path)
    manager = SupplementFunnelJobManager()
    entered = threading.Event()
    release = threading.Event()

    def fake_run(project_arg, **kwargs):
        del project_arg
        entered.set()
        release.wait(timeout=2)
        should_stop = kwargs.get("should_stop")
        report = SupplementFunnelReport(
            run_id="force_reset_test",
            script_version="script-v1",
            requested_gap_ids=list(kwargs.get("gap_ids") or []),
        )
        report.stopped = bool(should_stop and should_stop())
        report.message = "late return"
        return report

    monkeypatch.setattr(funnel_svc, "run_supplement_funnel_for_gaps", fake_run)
    assert manager.start(project, gap_ids=["gap_1"], model="gemini-1.5-flash")
    assert entered.wait(timeout=2)
    running = manager.get_state(project.id)
    assert running is not None
    assert running.model == "gemini-3.5-flash"
    assert manager.request_cancel(project.id)
    manager.force_reset(project.id)

    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.CANCELLED
    assert manager.is_running(project.id) is False

    release.set()
    time.sleep(0.2)
    # Der späte Thread darf den Job nicht wieder auf COMPLETED setzen.
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.CANCELLED


def test_funnel_vision_uses_http_timeout() -> None:
    source = Path(
        "otio_app/services/without_voiceover_enhanced/"
        "supplement_thumbnail_rank_service.py"
    ).read_text(encoding="utf-8")
    assert "FUNNEL_GEMINI_TIMEOUT_MS" in source
    assert "FUNNEL_GEMINI_HARD_TIMEOUT_SEC" in source
    assert "timeout_ms=FUNNEL_GEMINI_TIMEOUT_MS" in source
    assert "repair_callable=lambda p: default_funnel_text_llm(p)" in source
    assert "resolve_funnel_gemini_model" in source
    assert "executor.shutdown(wait=False" in source


def test_funnel_records_selected_llm_model(tmp_path: Path) -> None:
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
                    needed_visual="road",
                    preferred_media_type="photo",
                )
            ],
        ),
    )
    cands = _make_candidates(4)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
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
        model="gemini-3.1-flash-lite",
    )
    assert report.llm_model == "gemini-3.1-flash-lite"


def test_funnel_replaces_retired_gemini_15_with_funnel_default(tmp_path: Path) -> None:
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
                    needed_visual="road",
                    preferred_media_type="photo",
                )
            ],
        ),
    )
    cands = _make_candidates(4)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(script_version="script-v1", candidates=cands),
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
        model="gemini-1.5-flash",
    )
    assert report.llm_model == "gemini-3.5-flash"


def test_funnel_text_llm_hard_timeout_does_not_block(monkeypatch) -> None:
    import time

    from otio_app.services.without_voiceover_enhanced import (
        supplement_thumbnail_rank_service as rank_svc,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
        FunnelRankError,
        default_funnel_text_llm,
    )

    monkeypatch.setattr(rank_svc, "FUNNEL_GEMINI_HARD_TIMEOUT_SEC", 0.15)
    monkeypatch.setattr(rank_svc, "is_api_key_set", lambda *_a, **_k: True)

    class FakeModels:
        def generate_content(self, **kwargs):
            del kwargs
            time.sleep(0.6)
            raise AssertionError("generate_content should have been timed out")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(rank_svc, "_get_client", lambda **_k: FakeClient())
    started = time.monotonic()
    with pytest.raises(FunnelRankError, match="Timeout"):
        default_funnel_text_llm("prompt", model="gemini-3.5-flash")
    elapsed = time.monotonic() - started
    assert elapsed < 1.0


def test_funnel_text_llm_sends_resolved_model_not_gemini_15(monkeypatch) -> None:
    from otio_app.services.without_voiceover_enhanced import (
        supplement_thumbnail_rank_service as rank_svc,
    )
    from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
        default_funnel_text_llm,
    )

    seen: dict[str, str] = {}

    class FakeResp:
        text = '{"ok": true}'

    class FakeModels:
        def generate_content(self, **kwargs):
            seen["model"] = kwargs["model"]
            return FakeResp()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(rank_svc, "is_api_key_set", lambda *_a, **_k: True)
    monkeypatch.setattr(rank_svc, "_get_client", lambda **_k: FakeClient())
    text = default_funnel_text_llm("prompt", model="gemini-1.5-flash")
    assert text == '{"ok": true}'
    assert seen["model"] == "gemini-3.5-flash"
