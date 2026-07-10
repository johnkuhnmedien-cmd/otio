"""Tests für die Adobe-Stock-Suche (Phase 12.1/12.2a/12.2b) und
Lizenzierung/Download (Phase 12.3).

Deckt ab:
- readiness() ohne Key / mit Key ohne Token / mit Key und Token
- search() ohne Key liefert [] (kein Mock mehr)
- gentech=false wird bei jeder Suchanfrage mitgeschickt
- is_gentech-Treffer werden zusätzlich code-seitig verworfen
- Foto/Video werden anhand media_type_id korrekt unterschieden, andere
  media_type_id-Werte (Illustration/Vektor/...) werden übersprungen
- Breite/Höhe stammen aus den echten comps-/Files-API-Werten (Phase 12.2b),
  Video muss 16:9 sein (harte Ablehnung), Foto nicht (weiche Regel)
- max_candidates aus dem Request wird respektiert
- LLM-generierte Queries (request.llm_generated_queries) haben Vorrang vor
  der deterministischen Fallback-Query
- x-api-key/x-product/Authorization-Header werden korrekt gesetzt
- acquire(): sofortige Lizenzierung (Content/License) + Download, 4K/HD-
  600-MB-Regel (Content-Length-Header UND Streaming-Messung), Foto-Standard-
  Lizenz, Fehlerfälle (kein Access-Token, keine Download-URL, HTTP-Fehler)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    ADOBE_STOCK_LICENSE_ENDPOINT,
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeAssetTooLargeError, AdobeStockAdapter


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


def test_readiness_ready_with_key_but_without_token_disables_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.delenv("ADOBE_STOCK_ACCESS_TOKEN", raising=False)
    readiness = AdobeStockAdapter().readiness()
    assert readiness.status == PROVIDER_STATUS_READY
    assert readiness.search_enabled is True
    assert readiness.acquire_enabled is False


def test_readiness_ready_with_key_and_token_enables_acquire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    readiness = AdobeStockAdapter().readiness()
    assert readiness.status == PROVIDER_STATUS_READY
    assert readiness.search_enabled is True
    assert readiness.acquire_enabled is True


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


def test_search_video_dimensions_come_from_comps_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 12.2b: Breite/Höhe eines Videos müssen aus dem tatsächlichen
    # comps-Eintrag stammen statt aus pauschalen 3840x2160/1920x1080 —
    # dieser Test nutzt bewusst ungewöhnliche Maße, um das zu beweisen.
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    unusual_video = _video_file(
        comps={
            "Video_HD": {"url": "https://stock.adobe.io/hd/999", "width": 1920, "height": 1080},
            "Video_4K": {"url": "https://stock.adobe.io/4k/999", "width": 4096, "height": 2304},
        }
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([unusual_video])),
    )
    candidate = AdobeStockAdapter().search(_request())[0]
    assert candidate.width == 4096
    assert candidate.height == 2304


def test_search_photo_dimensions_use_native_files_api_size_not_comps_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Phase 12.2b: comps.Standard bei Fotos ist nur eine kleine Wasserzeichen-
    # Vorschau (hier 1000x600) — width/height des Kandidaten müssen trotzdem
    # die native Auflösung aus der Files-API sein (hier 5000x3000).
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([_photo_file()])),
    )
    candidate = AdobeStockAdapter().search(_request())[0]
    assert candidate.width == 5000
    assert candidate.height == 3000
    assert candidate.preview_url == "https://stock.adobe.io/std/222"


def test_search_rejects_non_16_9_video_but_still_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 12.2b, Nutzerentscheidung: Video MUSS 16:9 sein (harte Ablehnung,
    # analog zu Pexels) — der Kandidat wird trotzdem zurückgegeben (mit
    # download_enabled=False), damit die UI ihn informativ anzeigen kann.
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    portrait_video = _video_file(
        comps={"Video_HD": {"url": "https://stock.adobe.io/hd/777", "width": 1080, "height": 1920}}
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([portrait_video])),
    )
    candidate = AdobeStockAdapter().search(_request())[0]
    assert candidate.is_16_9 is False
    assert candidate.download_enabled is False
    assert candidate.status == "REJECTED_ASPECT_RATIO"


def test_search_does_not_reject_non_16_9_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nutzervorgabe (Juli 2026): Fotos müssen NICHT zwingend 16:9 sein.
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    portrait_photo = _photo_file(width=3000, height=5000)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: FakeResponse(_files_payload([portrait_photo])),
    )
    candidate = AdobeStockAdapter().search(_request())[0]
    assert candidate.is_16_9 is False
    assert candidate.download_enabled is True
    assert candidate.status == "CANDIDATE_FOUND"


def test_search_continues_to_next_query_when_first_query_only_yields_rejected_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    portrait_video = _video_file(
        id=111,
        comps={"Video_HD": {"url": "https://stock.adobe.io/hd/111", "width": 1080, "height": 1920}},
    )

    def fake_urlopen(request, timeout=20):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)[
            "search_parameters[words]"
        ][0]
        if "first" in query:
            return FakeResponse(_files_payload([portrait_video]))
        return FakeResponse(_files_payload([_video_file(id=222)]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    candidates = AdobeStockAdapter().search(_request(llm_generated_queries=["first query", "second query"]))
    assert any(c.download_enabled for c in candidates)
    usable = [c for c in candidates if c.download_enabled]
    assert usable[0].provider_asset_id == "222"


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


def test_search_with_default_required_asset_type_requests_both_content_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 12.7: der Standardwert (required_asset_type nicht explizit
    "video"/"image", z. B. die Produktions-Pipeline-Vorgabe "video_preferred"
    oder der Cut-Plan-Standard "any" für die manuelle Suche) fragt WIE
    BISHER Video UND Foto gemeinsam an — schützt die produktionsseitige
    Supplement-Pipeline vor einer versehentlichen Verhaltensänderung."""
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    captured_urls: list[str] = []

    def fake_urlopen(request, timeout=20):
        captured_urls.append(request.full_url)
        return FakeResponse(_files_payload([_video_file(), _photo_file()]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request(required_asset_type="video_preferred"))

    assert "filters%5D%5Bcontent_type%3Avideo%5D=1" in captured_urls[0]
    assert "filters%5D%5Bcontent_type%3Aphoto%5D=1" in captured_urls[0]
    assert {c.media_type for c in candidates} == {"video", "image"}
    assert adapter.last_debug_report["media_type_filter"] == "any"


def test_search_with_required_asset_type_video_requests_only_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 12.7: der Cut-Plan-Auto-Resolver fragt in seiner Video-Suchstufe
    required_asset_type="video" an — Adobe soll dann NUR nach Video filtern
    (spart irrelevante Foto-Treffer) und ein trotzdem zurückgegebenes Foto
    wird zusätzlich code-seitig als Sicherheitsnetz verworfen."""
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    captured_urls: list[str] = []

    def fake_urlopen(request, timeout=20):
        captured_urls.append(request.full_url)
        # Simuliert, dass Adobe TROTZ Filter noch ein Foto mitliefert —
        # das defensive Sicherheitsnetz in _candidate_from_file muss es
        # trotzdem verwerfen.
        return FakeResponse(_files_payload([_video_file(), _photo_file()]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request(required_asset_type="video"))

    assert "filters%5D%5Bcontent_type%3Avideo%5D=1" in captured_urls[0]
    assert "filters%5D%5Bcontent_type%3Aphoto%5D=1" not in captured_urls[0]
    assert {c.media_type for c in candidates} == {"video"}
    assert adapter.last_debug_report["media_type_filter"] == "video"
    assert adapter.last_debug_report["skipped_wrong_media_type_count"] == 1


def test_search_with_required_asset_type_image_requests_only_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spiegelbildlich zur Video-Suchstufe: required_asset_type="image"
    filtert Adobe auf reine Foto-Suche."""
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    captured_urls: list[str] = []

    def fake_urlopen(request, timeout=20):
        captured_urls.append(request.full_url)
        return FakeResponse(_files_payload([_video_file(), _photo_file()]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    candidates = adapter.search(_request(required_asset_type="image"))

    assert "filters%5D%5Bcontent_type%3Aphoto%5D=1" in captured_urls[0]
    assert "filters%5D%5Bcontent_type%3Avideo%5D=1" not in captured_urls[0]
    assert {c.media_type for c in candidates} == {"image"}
    assert adapter.last_debug_report["media_type_filter"] == "photo"
    assert adapter.last_debug_report["skipped_wrong_media_type_count"] == 1


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


def test_candidates_found_via_search_are_real_and_download_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


# --- Phase 12.3: Lizenzierung + Download ------------------------------------

_MODULE = "otio_app.services.supplement_sources.adobe_stock"


def _video_candidate(**overrides) -> SupplementCandidate:
    base = dict(
        candidate_id="cand_video_1",
        supplement_request_id="supp_req_adobe_test",
        provider="adobe_stock",
        provider_asset_id="555",
        media_type="video",
        download_enabled=True,
        is_mock=False,
        adobe_comps={
            "Video_4K": {"url": "https://stock.adobe.io/4k/555", "width": 3840, "height": 2160},
            "Video_HD": {"url": "https://stock.adobe.io/hd/555", "width": 1920, "height": 1080},
        },
    )
    base.update(overrides)
    return SupplementCandidate(**base)


def _photo_candidate(**overrides) -> SupplementCandidate:
    base = dict(
        candidate_id="cand_photo_1",
        supplement_request_id="supp_req_adobe_test",
        provider="adobe_stock",
        provider_asset_id="666",
        media_type="image",
        download_enabled=True,
        is_mock=False,
        adobe_comps={"Standard": {"url": "https://stock.adobe.io/std/666", "width": 1000, "height": 600}},
    )
    base.update(overrides)
    return SupplementCandidate(**base)


class _FakeStreamResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_length: str | None = None):
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _license_response_body(
    content_id: str,
    *,
    download_url: str = "",
    content_type: str = "video/mp4",
    width: int = 1920,
    height: int = 1080,
    state: str = "just_purchased",
    no_url: bool = False,
) -> bytes:
    purchase_details = {"state": state, "content_type": content_type, "width": width, "height": height}
    if not no_url:
        purchase_details["url"] = download_url
    return json.dumps({"contents": {str(content_id): {"content_id": str(content_id), "purchase_details": purchase_details}}}).encode()


def _dispatching_urlopen(license_bodies: dict, download_bodies: dict):
    """license_bodies: {license_type: bytes}; download_bodies: {url: _FakeStreamResponse}."""

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if url.startswith(ADOBE_STOCK_LICENSE_ENDPOINT):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            license_type = params["license"][0]
            body = license_bodies[license_type]
            return _FakeStreamResponse(body)
        return download_bodies[url]

    return fake_urlopen


def test_acquire_without_api_key_raises_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADOBE_STOCK_API_KEY", raising=False)
    with pytest.raises(PermissionError, match="ADOBE_STOCK_API_KEY"):
        AdobeStockAdapter().acquire(_photo_candidate(), Path("/tmp/does-not-matter"))


def test_acquire_without_access_token_raises_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.delenv("ADOBE_STOCK_ACCESS_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="ADOBE_STOCK_ACCESS_TOKEN"):
        AdobeStockAdapter().acquire(_photo_candidate(), Path("/tmp/does-not-matter"))


def test_acquire_rejects_mock_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    with pytest.raises(PermissionError, match="Mock"):
        AdobeStockAdapter().acquire(_photo_candidate(is_mock=True), Path("/tmp/does-not-matter"))


def test_acquire_rejects_download_disabled_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    with pytest.raises(PermissionError, match="Mock"):
        AdobeStockAdapter().acquire(_photo_candidate(download_enabled=False), Path("/tmp/does-not-matter"))


def test_acquire_photo_licenses_standard_and_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    download_url = "https://stock.adobe.com/Rest/Libraries/Download/666/1"
    body = b"x" * 200_000
    fake_urlopen = _dispatching_urlopen(
        license_bodies={
            "Standard": _license_response_body(
                "666", download_url=download_url, content_type="image/jpeg", width=5000, height=3000
            )
        },
        download_bodies={download_url: _FakeStreamResponse(body, content_length=str(len(body)))},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    asset = AdobeStockAdapter().acquire(_photo_candidate(), tmp_path / "req" / "assets")
    assert asset.local_path.is_file()
    assert asset.local_path.suffix == ".jpg"
    assert asset.local_path.read_bytes() == body
    assert asset.sidecar.license == "Standard"
    assert asset.sidecar.acquisition_method == "adobe_stock_license_api"
    assert asset.sidecar.approval_status == "APPROVED"


def test_acquire_video_prefers_4k_when_under_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_4k = "https://stock.adobe.com/Rest/Libraries/Download/555/4"
    body = b"y" * 200_000
    fake_urlopen = _dispatching_urlopen(
        license_bodies={
            "Video_4K": _license_response_body("555", download_url=url_4k, content_type="video/mp4"),
        },
        download_bodies={url_4k: _FakeStreamResponse(body, content_length=str(len(body)))},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with patch(f"{_MODULE}.probe_duration_seconds", return_value=8.0):
        asset = AdobeStockAdapter().acquire(_video_candidate(), tmp_path / "req" / "assets")
    assert asset.local_path.is_file()
    assert asset.sidecar.license == "Video_4K"


def test_acquire_video_falls_back_to_hd_when_4k_content_length_exceeds_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_4k = "https://stock.adobe.com/Rest/Libraries/Download/555/4"
    url_hd = "https://stock.adobe.com/Rest/Libraries/Download/555/3"
    too_large = str(700 * 1024 * 1024)  # > 600 MB Grenze
    body_hd = b"z" * 200_000
    fake_urlopen = _dispatching_urlopen(
        license_bodies={
            "Video_4K": _license_response_body("555", download_url=url_4k, content_type="video/mp4"),
            "Video_HD": _license_response_body("555", download_url=url_hd, content_type="video/mp4"),
        },
        download_bodies={
            url_4k: _FakeStreamResponse(b"unused-because-too-large", content_length=too_large),
            url_hd: _FakeStreamResponse(body_hd, content_length=str(len(body_hd))),
        },
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with patch(f"{_MODULE}.probe_duration_seconds", return_value=8.0):
        asset = AdobeStockAdapter().acquire(_video_candidate(), tmp_path / "req" / "assets")
    assert asset.local_path.is_file()
    assert asset.local_path.read_bytes() == body_hd
    assert asset.sidecar.license == "Video_HD"


def test_acquire_video_falls_back_to_hd_when_4k_exceeds_limit_during_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Content-Length-Header fehlt (z. B. chunked transfer) — die Grenze muss
    # trotzdem WÄHREND des Streamings erkannt werden (Nutzervorgabe).
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_4k = "https://stock.adobe.com/Rest/Libraries/Download/555/4"
    url_hd = "https://stock.adobe.com/Rest/Libraries/Download/555/3"
    body_hd = b"z" * 200_000
    from otio_app.defaults import ADOBE_STOCK_VIDEO_4K_MAX_BYTES

    oversized_body = b"a" * (ADOBE_STOCK_VIDEO_4K_MAX_BYTES + 1024)
    fake_urlopen = _dispatching_urlopen(
        license_bodies={
            "Video_4K": _license_response_body("555", download_url=url_4k, content_type="video/mp4"),
            "Video_HD": _license_response_body("555", download_url=url_hd, content_type="video/mp4"),
        },
        download_bodies={
            url_4k: _FakeStreamResponse(oversized_body),  # kein Content-Length-Header
            url_hd: _FakeStreamResponse(body_hd, content_length=str(len(body_hd))),
        },
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with patch(f"{_MODULE}.probe_duration_seconds", return_value=8.0):
        asset = AdobeStockAdapter().acquire(_video_candidate(), tmp_path / "req" / "assets")
    assert asset.local_path.read_bytes() == body_hd
    assert asset.sidecar.license == "Video_HD"


def test_acquire_video_hd_only_has_no_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_hd = "https://stock.adobe.com/Rest/Libraries/Download/555/3"
    huge_body_size = str(700 * 1024 * 1024)
    body = b"z" * 200_000
    fake_urlopen = _dispatching_urlopen(
        license_bodies={"Video_HD": _license_response_body("555", download_url=url_hd, content_type="video/mp4")},
        download_bodies={url_hd: _FakeStreamResponse(body, content_length=huge_body_size)},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    candidate = _video_candidate(adobe_comps={"Video_HD": {"url": url_hd, "width": 1920, "height": 1080}})
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=8.0):
        asset = AdobeStockAdapter().acquire(candidate, tmp_path / "req" / "assets")
    assert asset.local_path.is_file()
    assert asset.sidecar.license == "Video_HD"


def test_acquire_video_without_fallback_and_over_limit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kein Video_HD in adobe_comps vorhanden -> keine Ausweich-Lizenz, wenn
    # die einzige verfügbare Variante (hier fälschlich als 4K geführt) zu
    # groß ist. Realistisch unwahrscheinlich (4K impliziert i. d. R. auch
    # HD), aber die Fallback-Logik darf trotzdem nicht crashen.
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_4k = "https://stock.adobe.com/Rest/Libraries/Download/555/4"
    too_large = str(700 * 1024 * 1024)
    fake_urlopen = _dispatching_urlopen(
        license_bodies={"Video_4K": _license_response_body("555", download_url=url_4k, content_type="video/mp4")},
        download_bodies={url_4k: _FakeStreamResponse(b"unused", content_length=too_large)},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    candidate = _video_candidate(adobe_comps={"Video_4K": {"url": url_4k, "width": 3840, "height": 2160}})
    with pytest.raises(RuntimeError, match="600-MB"):
        AdobeStockAdapter().acquire(candidate, tmp_path / "req" / "assets")


def test_acquire_raises_when_license_response_has_no_download_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    fake_urlopen = _dispatching_urlopen(
        license_bodies={"Standard": _license_response_body("666", no_url=True, state="not_possible")},
        download_bodies={},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="keine Download-URL"):
        AdobeStockAdapter().acquire(_photo_candidate(), tmp_path / "req" / "assets")


def test_acquire_raises_runtime_error_on_license_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")

    def fail_urlopen(request, timeout=20):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    with pytest.raises(RuntimeError, match="Adobe-Lizenzierung fehlgeschlagen"):
        AdobeStockAdapter().acquire(_photo_candidate(), tmp_path / "req" / "assets")


def test_acquire_raises_when_downloaded_file_too_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    download_url = "https://stock.adobe.com/Rest/Libraries/Download/666/1"
    tiny_body = b"x" * 10
    fake_urlopen = _dispatching_urlopen(
        license_bodies={
            "Standard": _license_response_body("666", download_url=download_url, content_type="image/jpeg")
        },
        download_bodies={download_url: _FakeStreamResponse(tiny_body, content_length=str(len(tiny_body)))},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="zu klein"):
        AdobeStockAdapter().acquire(_photo_candidate(), tmp_path / "req" / "assets")


def test_acquire_video_raises_when_ffprobe_cannot_read_downloaded_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    url_4k = "https://stock.adobe.com/Rest/Libraries/Download/555/4"
    body = b"y" * 200_000
    fake_urlopen = _dispatching_urlopen(
        license_bodies={"Video_4K": _license_response_body("555", download_url=url_4k, content_type="video/mp4")},
        download_bodies={url_4k: _FakeStreamResponse(body, content_length=str(len(body)))},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=None):
        with pytest.raises(RuntimeError, match="ffprobe"):
            AdobeStockAdapter().acquire(_video_candidate(), tmp_path / "req" / "assets")


def test_acquire_sends_content_id_and_license_params_to_license_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "test-token")
    download_url = "https://stock.adobe.com/Rest/Libraries/Download/666/1"
    body = b"x" * 200_000
    captured: dict = {}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if url.startswith(ADOBE_STOCK_LICENSE_ENDPOINT):
            captured["license_url"] = url
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            return _FakeStreamResponse(
                _license_response_body("666", download_url=download_url, content_type="image/jpeg")
            )
        return _FakeStreamResponse(body, content_length=str(len(body)))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    AdobeStockAdapter().acquire(_photo_candidate(), tmp_path / "req" / "assets")

    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["license_url"]).query)
    assert params["content_id"] == ["666"]
    assert params["license"] == ["Standard"]
    assert captured["headers"]["authorization"] == "Bearer test-token"
    assert captured["headers"]["x-api-key"] == "test-key"


def test_acquire_does_not_raise_too_large_error_type_unused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sicherstellen, dass die interne Exception-Klasse importierbar/instanziierbar
    # ist (u. a. für zukünftige Wiederverwendung) — verhindert stille
    # Signatur-Regressionen.
    assert isinstance(AdobeAssetTooLargeError(), Exception)
