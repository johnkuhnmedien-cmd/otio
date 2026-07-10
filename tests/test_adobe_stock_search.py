"""Tests für die Adobe-Stock-Suche (Phase 12.1/12.2a).

Deckt ab:
- readiness() ohne/mit ADOBE_STOCK_API_KEY
- search() ohne Key liefert [] (kein Mock mehr)
- gentech=false wird bei jeder Suchanfrage mitgeschickt
- is_gentech-Treffer werden zusätzlich code-seitig verworfen
- Foto/Video werden anhand media_type_id korrekt unterschieden, andere
  media_type_id-Werte (Illustration/Vektor/...) werden übersprungen
- max_candidates aus dem Request wird respektiert
- LLM-generierte Queries (request.llm_generated_queries) haben Vorrang vor
  der deterministischen Fallback-Query
- x-api-key/x-product/Authorization-Header werden korrekt gesetzt
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from otio_app.analysis_models import SupplementRequest
from otio_app.defaults import PROVIDER_STATUS_CONFIG_MISSING, PROVIDER_STATUS_READY
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter


def _request(**overrides) -> SupplementRequest:
    defaults = dict(
        supplement_request_id="supp_req_adobe_test",
        section_id="section_havasu",
        folder_name="Havasu Falls",
        beat_id="beat_001",
        passage_text="Test",
    )
    defaults.update(overrides)
    return SupplementRequest(**defaults)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _files_payload(files: list[dict]) -> bytes:
    return json.dumps({"files": files}).encode("utf-8")


def _video_file(**overrides) -> dict:
    base = {
        "id": 111,
        "title": "Waterfall stock video",
        "description": "",
        "creator_name": "Creator",
        "width": 1920,
        "height": 1080,
        "duration": 8000,
        "details_url": "https://stock.adobe.com/111",
        "content_type": "video",
        "media_type_id": 4,
        "comps": {
            "Video_HD": {"url": "https://stock.adobe.io/hd/111", "width": 1920, "height": 1080},
            "Video_4K": {"url": "https://stock.adobe.io/4k/111", "width": 3840, "height": 2160},
        },
        "is_gentech": False,
    }
    base.update(overrides)
    return base


def _photo_file(**overrides) -> dict:
    base = {
        "id": 222,
        "title": "Waterfall stock photo",
        "description": "",
        "creator_name": "Creator",
        "width": 5000,
        "height": 3000,
        "details_url": "https://stock.adobe.com/222",
        "content_type": "photo",
        "media_type_id": 1,
        "comps": {"Standard": {"url": "https://stock.adobe.io/std/222", "width": 1000, "height": 600}},
        "is_gentech": False,
    }
    base.update(overrides)
    return base


def test_readiness_config_missing_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADOBE_STOCK_API_KEY", raising=False)
    readiness = AdobeStockAdapter().readiness()
    assert readiness.status == PROVIDER_STATUS_CONFIG_MISSING
    assert readiness.search_enabled is True
    assert readiness.acquire_enabled is False


def test_readiness_ready_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    readiness = AdobeStockAdapter().readiness()
    assert readiness.status == PROVIDER_STATUS_READY
    assert readiness.search_enabled is True
    assert readiness.acquire_enabled is False


def test_search_without_key_returns_no_candidates_and_no_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADOBE_STOCK_API_KEY", raising=False)

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("Adobe-API darf ohne Key nicht aufgerufen werden.")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    assert AdobeStockAdapter().search(_request()) == []


def test_search_maps_video_and_photo_by_media_type_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([_video_file(), _photo_file()])),
    )
    candidates = AdobeStockAdapter().search(_request())
    assert {c.media_type for c in candidates} == {"video", "image"}
    video = next(c for c in candidates if c.media_type == "video")
    assert video.width == 3840 and video.height == 2160  # Video_4K bevorzugt
    assert video.duration_sec == pytest.approx(8.0)
    photo = next(c for c in candidates if c.media_type == "image")
    assert photo.width == 5000 and photo.height == 3000


def test_search_skips_unsupported_media_type_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    illustration = _video_file(id=999, media_type_id=2)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([illustration, _photo_file()])),
    )
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request())
    assert [c.provider_asset_id for c in candidates] == ["222"]
    assert adapter.last_debug_report["skipped_unsupported_media_type_count"] == 1


def test_search_rejects_gentech_candidates_even_though_filter_was_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    gentech_video = _video_file(id=555, is_gentech=True)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([gentech_video, _photo_file()])),
    )
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request())
    assert [c.provider_asset_id for c in candidates] == ["222"]
    assert adapter.last_debug_report["gentech_rejected_count"] == 1
    assert adapter.last_debug_report["rejected_reasons"][0]["reason"] == "ADOBE_GENTECH_REJECTED"


def test_search_sends_gentech_false_filter_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    captured: dict = {}

    def fake_urlopen(request, timeout=20):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        return FakeResponse(_files_payload([]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    AdobeStockAdapter().search(_request())
    assert "filters%5D%5Bgentech%5D=false" in captured["url"]
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["x-api-key"] == "test-key"
    assert headers["x-product"]
    assert headers["authorization"] == "Bearer test-token"


def test_search_respects_max_candidates_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    files = [_video_file(id=idx, title=f"video {idx}") for idx in range(5)]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload(files)),
    )
    candidates = AdobeStockAdapter().search(_request(max_candidates=2))
    assert len(candidates) == 2


def test_search_prefers_llm_generated_queries_over_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    seen_queries: list[str] = []

    def fake_urlopen(request, timeout=20):
        import urllib.parse as _urlparse

        parsed = _urlparse.urlparse(request.full_url)
        params = _urlparse.parse_qs(parsed.query)
        seen_queries.append(params["search_parameters[words]"][0])
        return FakeResponse(_files_payload([]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    AdobeStockAdapter().search(
        _request(llm_generated_queries=["Havasu Falls waterfall woman", "Havasu Falls blue water"])
    )
    assert seen_queries == ["Havasu Falls waterfall woman", "Havasu Falls blue water"]


def test_search_stops_at_first_query_variant_that_yields_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    seen_queries: list[str] = []

    def fake_urlopen(request, timeout=20):
        import urllib.parse as _urlparse

        parsed = _urlparse.urlparse(request.full_url)
        query = _urlparse.parse_qs(parsed.query)["search_parameters[words]"][0]
        seen_queries.append(query)
        if "first query" in query:
            return FakeResponse(_files_payload([_video_file()]))
        return FakeResponse(_files_payload([]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    AdobeStockAdapter().search(_request(llm_generated_queries=["first query", "second query"]))
    assert len(seen_queries) == 1
    assert "first query" in seen_queries[0]


def test_search_http_error_is_captured_in_debug_report_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")

    def fail_urlopen(request, timeout=20):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request())
    assert candidates == []
    assert adapter.last_debug_report["errors"][0]["status"] == 401


def test_candidates_are_real_but_acquire_still_raises_until_licensing_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # download_enabled bleibt True (echter, technisch verwendbarer Treffer),
    # aber acquire() lehnt weiterhin ab, bis Phase 12.4 (Lizenzierung/
    # Download) implementiert ist — kein Wasserzeichen-Preview wird
    # fälschlich als fertiges Asset ausgegeben.
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([_video_file()])),
    )
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request())
    assert candidates
    assert candidates[0].download_enabled is True
    assert candidates[0].is_mock is False
    with pytest.raises(PermissionError):
        adapter.acquire(candidates[0], destination_folder=None)


def test_adobe_specific_snapshot_fields_are_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([_video_file()])),
    )
    candidate = AdobeStockAdapter().search(_request())[0]
    assert candidate.adobe_media_type_id == 4
    assert candidate.adobe_is_gentech is False
    assert "Video_4K" in candidate.adobe_comps
    assert candidate.adobe_content_type == "video"
