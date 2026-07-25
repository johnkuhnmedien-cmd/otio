"""ADOBE-STOCK-LICENSING-DIAG-002 — Fehlerklassen, 429, History-Hot-Path, Redaction."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path
import pytest

from otio_app.defaults import (
    ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
    ADOBE_STOCK_FILES_ENDPOINT,
    ADOBE_STOCK_LICENSE_ENDPOINT,
    ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT,
    ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
)
from otio_app.services.adobe_research_import import (
    AdobeResearchAsset,
    AdobeResearchChapter,
    AdobeResearchImportPlan,
    download_research_import,
)
from otio_app.services.supplement_sources.adobe_stock import (
    AdobeAuthenticationExpiredError,
    AdobeLicenseNotPossibleError,
    AdobeLicenseTransactionCancelledError,
    AdobeRateLimitedError,
    AdobeStockAdapter,
    AdobeWatermarkedPreviewError,
    DownloadedMediaInvalidError,
    LocalStorageError,
    classify_adobe_url,
    is_full_adobe_download_url,
    token_fingerprint,
)


class _FakeHTTPResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict | None = None,
    ):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, size: int = -1):
        if size is None or size < 0:
            data = self._body
            self._body = b""
            return data
        data = self._body[:size]
        self._body = self._body[size:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _HTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, *, headers: dict | None = None, body: bytes = b""):
        hdrs = headers or {}
        super().__init__(
            url="https://stock.adobe.io/test",
            code=code,
            msg=str(code),
            hdrs=hdrs,
            fp=BytesIO(body),
        )


def _license_body(
    content_id: str,
    *,
    state: str = "just_purchased",
    license: str = "Video_4K",
    size: str = "Original",
    url: str = "",
    options_state: str = "",
    quota: int | None = None,
) -> bytes:
    if not url and state in {"purchased", "just_purchased"}:
        url = f"https://stock.adobe.io/Rest/Libraries/Download/{content_id}/4?token=SECRET"
    payload: dict = {
        "contents": {
            str(content_id): {
                "content_id": content_id,
                "size": size,
                "purchase_details": {
                    "state": state,
                    "license": license,
                    "url": url,
                    "content_type": "video/mp4",
                },
            }
        }
    }
    if options_state:
        payload["purchase_options"] = {"state": options_state, "message": "nope"}
    if quota is not None:
        payload["available_entitlement"] = {"quota": quota, "full_entitlement_quota": {"image_quota": quota}}
    return json.dumps(payload).encode()


def _info_body(content_id: str, *, state: str = "not_purchased", url: str = "") -> bytes:
    details: dict = {"state": state}
    if url:
        details["url"] = url
        details["license"] = "Video_4K"
        details["content_type"] = "video/mp4"
    return json.dumps(
        {
            "contents": {
                str(content_id): {
                    "content_id": content_id,
                    "size": "Comp",
                    "purchase_details": details,
                }
            }
        }
    ).encode()


def _files_body(content_id: str) -> bytes:
    return json.dumps(
        {
            "files": [
                {
                    "id": content_id,
                    "media_type_id": 4,
                    "content_type": "video/mp4",
                }
            ]
        }
    ).encode()


def _disable_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    import otio_app.services.adobe_research_import as mod
    import otio_app.services.supplement_sources.adobe_stock as adobe_mod

    for name in (
        "_ASSET_PAUSE_SECONDS",
        "_API_CALL_PAUSE_SECONDS",
        "_LICENSE_RETRY_PAUSE_SECONDS",
        "_DOWNLOAD_START_PAUSE_SECONDS",
        "_POST_ASSET_PAUSE_SECONDS",
    ):
        monkeypatch.setattr(mod, name, 0)
    monkeypatch.setattr(mod, "probe_duration_seconds", lambda _p: 8.0)
    monkeypatch.setattr(adobe_mod.time, "sleep", lambda *_a, **_k: None)


def _plan_with_ids(ids: list[str]) -> AdobeResearchImportPlan:
    return AdobeResearchImportPlan(
        sheet_name="Sheet1",
        chapters=(
            AdobeResearchChapter(
                title="Test",
                folder_name="Test",
                assets=tuple(
                    AdobeResearchAsset(asset_id=i, media_hint="video") for i in ids
                ),
            ),
        ),
    )


def test_classify_url_and_comp_download_ok() -> None:
    dl = "https://stock.adobe.io/Rest/Libraries/Download/1/4"
    wm = "https://stock.adobe.io/Rest/Libraries/Watermarked/1"
    assert classify_adobe_url(dl) == "download"
    assert is_full_adobe_download_url(dl)
    assert classify_adobe_url(wm) == "watermarked"
    assert not is_full_adobe_download_url(wm)


def test_token_fingerprint_short_and_stable() -> None:
    fp = token_fingerprint("access-token-secret-value")
    assert 8 <= len(fp) <= 12
    assert fp == token_fingerprint("access-token-secret-value")
    assert "access-token" not in fp


def test_license_again_stripped_from_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_urlopen(request, timeout=20):
        seen.append(request.full_url)
        assert "license_again" not in request.full_url
        auth = request.get_header("Authorization") or request.headers.get("Authorization")
        assert auth and auth.startswith("Bearer ")
        return _FakeHTTPResponse(
            _license_body("99", license="Video_HD"),
            headers={"X-Request-Id": "rid-1"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    adapter.reset_request_diagnostics(batch_id="b1")
    details = adapter._license_asset(
        "99",
        "Video_HD",
        "key",
        "tok",
        diagnose=False,
    )
    assert details["state"] == "just_purchased"
    assert adapter.request_counters.content_license == 1
    assert adapter.request_counters.licensed_ok == 1


def test_no_history_full_scan_in_normal_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    history_calls = {"n": 0}
    endpoints: list[str] = []

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT in url:
            history_calls["n"] += 1
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            endpoints.append("files")
            return _FakeHTTPResponse(_files_body("100"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            endpoints.append("info")
            return _FakeHTTPResponse(_info_body("100"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            endpoints.append("license")
            return _FakeHTTPResponse(
                _license_body("100"),
                headers={"X-Request-Id": "x1"},
            )
        endpoints.append("download")
        return _FakeHTTPResponse(b"x" * 200_000, headers={"Content-Length": "200000"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")

    import otio_app.services.adobe_research_import as mod

    adapter = AdobeStockAdapter()
    adapter.reset_request_diagnostics()
    path, used = mod._license_and_download_to_path(
        adapter,
        content_id="100",
        media_type="video",
        destination=tmp_path / "a",
        media_hint="video",
    )
    assert path.is_file()
    assert history_calls["n"] == 0
    assert adapter.request_counters.license_history == 0
    assert adapter.request_counters.license_history_pages == 0
    assert "info" in endpoints and "license" in endpoints


def test_large_history_not_walked_for_single_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mock mit 150 History-Seiten — Hot-Path darf sie nicht anfassen."""
    _disable_pauses(monkeypatch)
    history_pages = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT in url:
            history_pages["n"] += 1
            return _FakeHTTPResponse(json.dumps({"nb_results": 15000, "files": []}).encode())
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("200"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("200"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("200"))
        return _FakeHTTPResponse(b"x" * 200_000)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    adapter = AdobeStockAdapter()
    mod._license_and_download_to_path(
        adapter,
        content_id="200",
        media_type="video",
        destination=tmp_path / "b",
        media_hint="video",
    )
    assert history_pages["n"] == 0
    # Manuelle Diagnose: pages=150 wird hart auf MAX_LICENSE_HISTORY_PAGES begrenzt.
    from otio_app.services.supplement_sources.adobe_stock import MAX_LICENSE_HISTORY_PAGES

    adapter.find_license_history_download("200", "k", "t", pages=150)
    assert history_pages["n"] == MAX_LICENSE_HISTORY_PAGES
    assert MAX_LICENSE_HISTORY_PAGES == 5


def test_http_429_retries_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_urlopen(request, timeout=20):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "r429"})
        return _FakeHTTPResponse(
            _license_body("301", license="Video_HD"),
            headers={"X-Request-Id": "rok"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    import otio_app.services.supplement_sources.adobe_stock as adobe_mod

    monkeypatch.setattr(adobe_mod.time, "sleep", lambda *_a, **_k: None)
    adapter = AdobeStockAdapter()
    details = adapter._license_asset("301", "Video_HD", "k", "t", diagnose=False)
    assert details["state"] == "just_purchased"
    assert attempts["n"] == 2
    assert adapter.request_counters.http_429 == 1
    assert adapter.request_counters.retries == 1
    assert adapter.request_counters.licensed_ok == 1


def test_http_429_exhausted_max_three(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_urlopen(request, timeout=20):
        attempts["n"] += 1
        raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "r429b"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    import otio_app.services.supplement_sources.adobe_stock as adobe_mod

    monkeypatch.setattr(adobe_mod.time, "sleep", lambda *_a, **_k: None)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeRateLimitedError) as exc:
        adapter._license_asset("302", "Video_HD", "k", "t", diagnose=False)
    assert exc.value.code == "adobe_rate_limited"
    assert "Entitlement" not in str(exc.value)
    assert attempts["n"] == 3
    assert adapter.request_counters.http_429 == 3


def test_seventeen_ok_then_429_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    license_calls = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("ids", ["x"])[0]
            return _FakeHTTPResponse(_files_body(cid))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("content_id", ["x"])[0]
            return _FakeHTTPResponse(_info_body(cid))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_calls["n"] += 1
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("content_id", ["x"])[0]
            if license_calls["n"] > 17:
                raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "burst"})
            return _FakeHTTPResponse(_license_body(cid))
        if ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT in url:
            raise AssertionError("History im Batch-Hot-Path")
        return _FakeHTTPResponse(b"x" * 200_000)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")

    class _Ready:
        acquire_enabled = True
        message = "ok"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.AdobeStockAdapter.readiness",
        lambda self: _Ready(),
    )
    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.decode_access_token_claims",
        lambda *_a, **_k: {"sub": "user-1", "email": "a@b.c"},
    )

    ids = [str(1000 + i) for i in range(18)]
    result = download_research_import(
        _plan_with_ids(ids),
        tmp_path / "out",
        state_dir=tmp_path / "state",
        skip_existing_ids=False,
    )
    assert result.downloaded == 17
    assert result.errors >= 1
    err_msgs = " ".join(i.message for i in result.items if i.status == "error")
    assert "adobe_rate_limited" in err_msgs
    assert "Entitlement-Mismatch" not in err_msgs
    assert "cct_pro_unlimited_images" not in err_msgs or "adobe_rate_limited" in err_msgs
    counters = result.diagnostics["request_counters"]
    assert counters["license_history"] == 0
    assert counters["http_429"] >= 1
    assert counters["licensed_ok"] == 17


def test_cancelled_classified_not_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=20):
        return _FakeHTTPResponse(
            _license_body(
                "401",
                state="cancelled",
                size="Comp",
                url="https://stock.adobe.io/Rest/Libraries/Watermarked/401",
                license="Video_4K",
            ),
            headers={"X-Request-Id": "c1"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeLicenseTransactionCancelledError) as exc:
        adapter._license_asset("401", "Video_4K", "k", "t", diagnose=False)
    assert exc.value.code == "adobe_license_transaction_cancelled"
    assert "rate" not in str(exc.value).lower() or "Nicht als Rate-Limit" in str(exc.value)


def test_not_possible_with_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=20):
        return _FakeHTTPResponse(
            _license_body(
                "402",
                state="not_purchased",
                url="",
                options_state="not_possible",
                quota=0,
                license="Video_HD",
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeLicenseNotPossibleError) as exc:
        adapter._license_asset("402", "Video_HD", "k", "t", diagnose=False)
    assert exc.value.code == "adobe_license_not_possible"
    assert exc.value.details.get("quota") == 0


def test_comp_just_purchased_download_url_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://stock.adobe.io/Rest/Libraries/Download/403/4?sig=SECRET"

    def fake_urlopen(request, timeout=20):
        return _FakeHTTPResponse(
            _license_body(
                "403",
                state="just_purchased",
                size="Comp",
                url=url,
                license="Video_HD",
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    details = adapter._license_asset("403", "Video_HD", "k", "t", diagnose=False)
    assert details["state"] == "just_purchased"
    assert details["url"] == url


def test_watermarked_never_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=20):
        return _FakeHTTPResponse(
            _license_body(
                "404",
                state="purchased",
                size="Comp",
                url="https://stock.adobe.io/Rest/Libraries/Watermarked/404",
                license="Video_HD",
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeWatermarkedPreviewError) as exc:
        adapter._license_asset("404", "Video_HD", "k", "t", diagnose=False)
    assert exc.value.code == "adobe_watermarked_preview_only"


def test_already_licensed_no_license_again(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_urlopen(request, timeout=20):
        urls.append(request.full_url)
        assert "license_again" not in request.full_url
        return _FakeHTTPResponse(
            _license_body(
                "405",
                state="purchased",
                license="Video_HD",
                url="https://stock.adobe.io/Rest/Libraries/Download/405/3",
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    details = adapter._license_asset("405", "Video_HD", "k", "t", diagnose=False)
    assert details["state"] == "purchased"
    assert adapter.request_counters.already_licensed == 1
    assert all("license_again" not in u for u in urls)


def test_4k_to_hd_fallback_request_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    from otio_app.services.supplement_sources.adobe_stock import (
        AdobeLicenseTransactionCancelledError,
    )
    import otio_app.services.adobe_research_import as mod

    license_calls: list[str] = []

    class _Adapter(AdobeStockAdapter):
        def lookup_file_metadata(self, content_id, api_key):
            return {"media_type_id": 4, "content_type": "video/mp4"}

        def content_info_purchase(self, *_a, **_k):
            return {"state": "not_purchased"}

        def _license_asset(self, content_id, license_type, api_key, access_token, *, diagnose=True):
            license_calls.append(license_type)
            if license_type == "Video_4K":
                raise AdobeLicenseTransactionCancelledError("cancelled 4k")
            return {
                "state": "just_purchased",
                "license": "Video_HD",
                "url": f"https://stock.adobe.com/Rest/Libraries/Download/{content_id}/3",
                "content_type": "video/mp4",
            }

        def _stream_download_to_file(self, url, local_path, *, api_key, access_token, size, max_bytes):
            local_path.write_bytes(b"x" * 200_000)

    monkeypatch.setattr(mod, "get_api_key", lambda key: "x")
    monkeypatch.setattr(mod, "get_adobe_access_token", lambda: "tok")
    path, used = mod._license_and_download_to_path(
        _Adapter(),
        content_id="500",
        media_type="video",
        destination=tmp_path / "fb",
        media_hint="video",
    )
    assert license_calls == ["Video_4K", "Video_HD"]
    assert used.startswith("Video_HD")
    assert path.is_file()


def test_local_storage_error_no_relicense(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    import otio_app.services.adobe_research_import as mod

    license_n = {"n": 0}

    class _Adapter(AdobeStockAdapter):
        def lookup_file_metadata(self, content_id, api_key):
            return {"media_type_id": 4, "content_type": "video/mp4"}

        def content_info_purchase(self, *_a, **_k):
            return {"state": "not_purchased"}

        def _license_asset(self, content_id, license_type, api_key, access_token, *, diagnose=True):
            license_n["n"] += 1
            return {
                "state": "just_purchased",
                "license": license_type,
                "url": f"https://stock.adobe.com/Rest/Libraries/Download/{content_id}/4",
                "content_type": "video/mp4",
            }

        def _stream_download_to_file(self, url, local_path, *, api_key, access_token, size, max_bytes):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod, "get_api_key", lambda key: "x")
    monkeypatch.setattr(mod, "get_adobe_access_token", lambda: "tok")
    # OSError in stream is converted inside adapter — wrap to LocalStorageError path
    adapter = _Adapter()

    def boom(*_a, **_k):
        adapter.request_counters.local_storage_errors += 1
        raise LocalStorageError("disk full", details={"path": str(tmp_path)})

    adapter._stream_download_to_file = boom  # type: ignore[method-assign]
    with pytest.raises(LocalStorageError) as exc:
        mod._license_and_download_to_path(
            adapter,
            content_id="600",
            media_type="video",
            destination=tmp_path / "disk",
            media_hint="video",
        )
    assert exc.value.code == "local_storage_error"
    assert license_n["n"] == 1  # keine erneute Lizenzierung nach Speicherfehler


def test_invalid_local_video_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    import otio_app.services.adobe_research_import as mod

    monkeypatch.setattr(mod, "probe_duration_seconds", lambda _p: None)
    monkeypatch.setattr(mod, "get_api_key", lambda key: "x")
    monkeypatch.setattr(mod, "get_adobe_access_token", lambda: "tok")

    class _Adapter(AdobeStockAdapter):
        def lookup_file_metadata(self, content_id, api_key):
            return {"media_type_id": 4, "content_type": "video/mp4"}

        def content_info_purchase(self, *_a, **_k):
            return {"state": "not_purchased"}

        def _license_asset(self, content_id, license_type, api_key, access_token, *, diagnose=True):
            return {
                "state": "just_purchased",
                "license": license_type,
                "url": f"https://stock.adobe.com/Rest/Libraries/Download/{content_id}/4",
                "content_type": "video/mp4",
            }

        def _stream_download_to_file(self, url, local_path, *, api_key, access_token, size, max_bytes):
            local_path.write_bytes(b"x" * 200_000)

    with pytest.raises(DownloadedMediaInvalidError) as exc:
        mod._license_and_download_to_path(
            _Adapter(),
            content_id="700",
            media_type="video",
            destination=tmp_path / "bad",
            media_hint="video",
        )
    assert exc.value.code == "downloaded_media_invalid"


def test_oauth_identity_change_stops_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    subs = iter([{"sub": "aaa", "email": "a@b.c"}, {"sub": "bbb", "email": "a@b.c"}])

    def claims():
        try:
            return next(subs)
        except StopIteration:
            return {"sub": "bbb", "email": "a@b.c"}

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.decode_access_token_claims",
        claims,
    )
    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.get_adobe_access_token",
        lambda **_k: "tok",
    )

    class _Ready:
        acquire_enabled = True
        message = "ok"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.AdobeStockAdapter.readiness",
        lambda self: _Ready(),
    )

    # download_research_import checks identity before each asset; first asset sees bbb != aaa
    result = download_research_import(
        _plan_with_ids(["1", "2"]),
        tmp_path / "id",
        state_dir=tmp_path / "st",
        skip_existing_ids=False,
    )
    assert any("adobe_identity_changed" in (i.message or "") for i in result.items)
    assert result.diagnostics.get("batch_stop_reason") == "adobe_identity_changed"


def test_oauth_refresh_same_sub_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token-Refresh mit gleichem sub stoppt den Batch nicht."""
    _disable_pauses(monkeypatch)
    tokens = {"v": "tok-1"}

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.decode_access_token_claims",
        lambda *_a, **_k: {"sub": "same-sub", "email": "u@example.com"},
    )

    def get_token(*, force_refresh: bool = False):
        if force_refresh:
            tokens["v"] = "tok-2"
        return tokens["v"]

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.get_adobe_access_token",
        get_token,
    )

    class _Ready:
        acquire_enabled = True
        message = "ok"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.AdobeStockAdapter.readiness",
        lambda self: _Ready(),
    )

    def fake_license(adapter, *, content_id, media_type, destination, media_hint="", phase_callback=None):
        # Simuliere einmal 401→Refresh-Pfad indirekt: nur Download
        path = destination.with_suffix(".mp4")
        path.write_bytes(b"x" * 200_000)
        return path, "Video_HD"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import._license_and_download_to_path",
        fake_license,
    )
    result = download_research_import(
        _plan_with_ids(["10", "11"]),
        tmp_path / "ok",
        state_dir=tmp_path / "st2",
        skip_existing_ids=False,
    )
    assert result.downloaded == 2
    assert result.diagnostics.get("batch_stop_reason") in {"", None}


def test_secret_redaction_in_diag_events(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_url = (
        "https://stock.adobe.io/Rest/Libraries/Download/800/4"
        "?token=SUPERSECRET&signature=abc123"
    )

    def fake_urlopen(request, timeout=20):
        # Authorization darf nicht in Diag landen
        return _FakeHTTPResponse(
            _license_body("800", url=secret_url, license="Video_HD"),
            headers={"X-Request-Id": "rid-sec"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    adapter.reset_request_diagnostics(batch_id="sec")
    adapter._license_asset("800", "Video_HD", "k", "Bearer-NOT-THIS", diagnose=False)
    blob = json.dumps([e.as_dict() for e in adapter.request_diag_events])
    assert "Authorization" not in blob
    assert "SUPERSECRET" not in blob
    assert "Bearer-NOT-THIS" not in blob
    assert "signature=abc123" not in blob
    assert adapter.request_diag_events[-1].url_class == "download"
    assert adapter.request_diag_events[-1].has_download_url is True


def test_http_401_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=20):
        raise _HTTPError(401, headers={"X-Request-Id": "a1"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeAuthenticationExpiredError) as exc:
        adapter._license_asset("901", "Video_HD", "k", "t", diagnose=False)
    assert exc.value.code == "adobe_authentication_expired"
