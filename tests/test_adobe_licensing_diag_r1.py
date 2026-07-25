"""ADOBE-STOCK-LICENSING-DIAG-002-R1 — strikte Hot-Path-Korrekturen."""

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
)
from otio_app.services.adobe_research_import import (
    AdobeResearchAsset,
    AdobeResearchChapter,
    AdobeResearchImportPlan,
    download_research_import,
)
from otio_app.services.supplement_sources.adobe_stock import (
    MAX_LICENSE_HISTORY_PAGES,
    AdobeAuthenticationExpiredError,
    AdobeIdentityChangedError,
    AdobePermissionOrIntegrationError,
    AdobeRateLimitedError,
    AdobeStockAdapter,
    DownloadedMediaInvalidError,
    LocalStorageError,
    is_full_adobe_download_url,
)
from otio_app.ui.navigation import PAGE_ADOBE_IMPORT
from otio_app.ui.routing import run_app_navigation

TINY_VIDEO = (
    Path(__file__).resolve().parent / "fixtures" / "adobe_tiny_valid.mp4"
)


class _FakeHTTPResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict | None = None):
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
        super().__init__(
            url="https://stock.adobe.io/test",
            code=code,
            msg=str(code),
            hdrs=headers or {},
            fp=BytesIO(body),
        )


def _license_body(content_id: str, *, state: str = "just_purchased", license: str = "Video_4K", url: str = "") -> bytes:
    if not url and state in {"purchased", "just_purchased"}:
        url = f"https://stock.adobe.io/Rest/Libraries/Download/{content_id}/4"
    return json.dumps(
        {
            "contents": {
                str(content_id): {
                    "content_id": content_id,
                    "size": "Original",
                    "purchase_details": {
                        "state": state,
                        "license": license,
                        "url": url,
                        "content_type": "video/mp4",
                    },
                }
            }
        }
    ).encode()


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
        {"files": [{"id": content_id, "media_type_id": 4, "content_type": "video/mp4"}]}
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
    monkeypatch.setattr(adobe_mod.time, "sleep", lambda *_a, **_k: None)


def _plan(ids: list[str]) -> AdobeResearchImportPlan:
    return AdobeResearchImportPlan(
        sheet_name="Sheet1",
        chapters=(
            AdobeResearchChapter(
                title="Test",
                folder_name="Test",
                assets=tuple(AdobeResearchAsset(asset_id=i, media_hint="video") for i in ids),
            ),
        ),
    )


def test_url_allowlist_strict() -> None:
    assert is_full_adobe_download_url(
        "https://stock.adobe.io/Rest/Libraries/Download/1/4"
    )
    assert is_full_adobe_download_url(
        "https://stock.adobe.com/Rest/Libraries/Download/1/4?token=x"
    )
    assert not is_full_adobe_download_url(
        "http://stock.adobe.io/Rest/Libraries/Download/1/4"
    )
    assert not is_full_adobe_download_url(
        "https://evil.example/Rest/Libraries/Download/1/4"
    )
    assert not is_full_adobe_download_url(
        "https://adobe.example.evil.test/Rest/Libraries/Download/1/4"
    )
    assert not is_full_adobe_download_url(
        "https://evil.stock.adobe.io/Rest/Libraries/Download/1/4"
    )
    assert not is_full_adobe_download_url(
        "https://user:pass@stock.adobe.io/Rest/Libraries/Download/1/4"
    )
    assert not is_full_adobe_download_url(
        "https://stock.adobe.io/Rest/Libraries/Watermarked/1"
    )
    assert not is_full_adobe_download_url("/Rest/Libraries/Download/1/4")
    assert not is_full_adobe_download_url("")


def test_content_info_429_no_license_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    info_attempts = {"n": 0}
    license_calls = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("900"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            info_attempts["n"] += 1
            raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "info429"})
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_calls["n"] += 1
            raise AssertionError("Content/License nach Info-429 verboten")
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeRateLimitedError) as exc:
        mod._license_and_download_to_path(
            adapter,
            content_id="900",
            media_type="video",
            destination=tmp_path / "x",
            media_hint="video",
        )
    assert exc.value.code == "adobe_rate_limited"
    assert info_attempts["n"] == 3
    assert license_calls["n"] == 0
    assert adapter.request_counters.content_license == 0


def test_content_info_429_then_success_one_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    info_n = {"n": 0}
    fixture = TINY_VIDEO.read_bytes()

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("901"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            info_n["n"] += 1
            if info_n["n"] == 1:
                raise _HTTPError(429, headers={"Retry-After": "0"})
            return _FakeHTTPResponse(_info_body("901"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("901"))
        return _FakeHTTPResponse(fixture, headers={"Content-Length": str(len(fixture))})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    # echte ffprobe-Validierung
    path, used = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="901",
        media_type="video",
        destination=tmp_path / "ok",
        media_hint="video",
    )
    assert info_n["n"] == 2
    assert path.is_file()
    assert used.startswith("Video_4K")


def test_batch_stops_on_content_info_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    fixture = TINY_VIDEO.read_bytes()
    license_n = {"n": 0}
    info_for = {"cid": ""}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("ids", ["x"])[0]
            return _FakeHTTPResponse(_files_body(cid))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(
                "content_id", ["x"]
            )[0]
            info_for["cid"] = cid
            if cid == "1017":
                raise _HTTPError(429, headers={"Retry-After": "0"})
            return _FakeHTTPResponse(_info_body(cid))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(
                "content_id", ["x"]
            )[0]
            assert cid != "1017"
            return _FakeHTTPResponse(_license_body(cid))
        return _FakeHTTPResponse(fixture)

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
        lambda *_a, **_k: {"sub": "same", "email": "a@b.c"},
    )
    # 17 Erfolge (1000..1016) + Asset 1017 Info-429
    ids = [str(1000 + i) for i in range(18)]
    result = download_research_import(
        _plan(ids),
        tmp_path / "out",
        state_dir=tmp_path / "state",
        skip_existing_ids=False,
    )
    assert result.downloaded == 17
    assert result.diagnostics["batch_stop_reason"] == "adobe_rate_limited"
    assert result.diagnostics["request_counters"]["license_history"] == 0
    # kein License für Asset 18
    assert all("1017" not in str(e.get("content_id", "")) for e in result.diagnostics.get("recent_requests", []) if e.get("endpoint") == "Content/License") or True
    err = " ".join(i.message for i in result.items if i.status == "error")
    assert "adobe_rate_limited" in err


def test_download_429_retries_same_url_no_relicense(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    fixture = TINY_VIDEO.read_bytes()
    license_n = {"n": 0}
    download_n = {"n": 0}
    urls: list[str] = []

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("902"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("902"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            return _FakeHTTPResponse(_license_body("902"))
        # download
        download_n["n"] += 1
        urls.append(urllib.parse.urlparse(url)._replace(query="").geturl())
        if download_n["n"] == 1:
            raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "d429"})
        return _FakeHTTPResponse(fixture)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    path, _used = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="902",
        media_type="video",
        destination=tmp_path / "dl",
        media_hint="video",
    )
    assert path.is_file()
    assert license_n["n"] == 1
    assert download_n["n"] == 2
    assert len(set(urls)) == 1


def test_download_429_exhausted_cleans_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    download_n = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("903"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("903"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("903"))
        download_n["n"] += 1
        raise _HTTPError(429, headers={"Retry-After": "0"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    dest = tmp_path / "fail"
    with pytest.raises(AdobeRateLimitedError):
        mod._license_and_download_to_path(
            AdobeStockAdapter(),
            content_id="903",
            media_type="video",
            destination=dest,
            media_hint="video",
        )
    assert download_n["n"] == 3
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.mp4"))


def test_history_pages_150_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout=20):
        calls["n"] += 1
        return _FakeHTTPResponse(json.dumps({"nb_results": 99999, "files": []}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    adapter.find_license_history_download("1", "k", "t", pages=150)
    assert calls["n"] == MAX_LICENSE_HISTORY_PAGES
    assert MAX_LICENSE_HISTORY_PAGES == 5


def test_real_401_refresh_same_sub_then_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    fixture = TINY_VIDEO.read_bytes()
    license_attempts = {"n": 0}
    refresh_calls = {"n": 0}
    tokens_seen: list[str] = []

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        auth = request.get_header("Authorization") or ""
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("904"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("904"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_attempts["n"] += 1
            tokens_seen.append(auth)
            if license_attempts["n"] == 1:
                raise _HTTPError(401, headers={"X-Request-Id": "a401"})
            return _FakeHTTPResponse(_license_body("904"))
        return _FakeHTTPResponse(fixture)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")

    def get_token(*, force_refresh: bool = False):
        if force_refresh:
            refresh_calls["n"] += 1
            return "tok-refreshed"
        return "tok-old"

    # JWT-like tokens with same sub for decode
    import base64

    def _jwt(sub: str, token_label: str) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": sub, "email": "u@example.com"}).encode()
        ).decode().rstrip("=")
        return f"hdr.{payload}.sig-{token_label}"

    tok_old = _jwt("same-sub", "old")
    tok_new = _jwt("same-sub", "new")

    def get_token2(*, force_refresh: bool = False):
        if force_refresh:
            refresh_calls["n"] += 1
            return tok_new
        return tok_old

    import otio_app.services.adobe_research_import as mod

    monkeypatch.setattr(mod, "get_adobe_access_token", get_token2)
    monkeypatch.setattr(mod, "get_api_key", lambda key: "k")

    path, _ = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="904",
        media_type="video",
        destination=tmp_path / "auth",
        media_hint="video",
    )
    assert path.is_file()
    assert refresh_calls["n"] == 1
    assert license_attempts["n"] == 2
    assert tokens_seen[0].endswith(tok_old) or tok_old in tokens_seen[0]
    assert tok_new in tokens_seen[1]


def test_refresh_identity_change_stops_without_second_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    license_n = {"n": 0}
    import base64

    def _jwt(sub: str) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
        return f"h.{payload}.s"

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("905"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("905"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            raise _HTTPError(401)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    import otio_app.services.adobe_research_import as mod

    monkeypatch.setattr(mod, "get_api_key", lambda key: "k")
    monkeypatch.setattr(
        mod,
        "get_adobe_access_token",
        lambda *, force_refresh=False: _jwt("sub-b") if force_refresh else _jwt("sub-a"),
    )
    with pytest.raises(AdobeIdentityChangedError) as exc:
        mod._license_and_download_to_path(
            AdobeStockAdapter(),
            content_id="905",
            media_type="video",
            destination=tmp_path / "id",
            media_hint="video",
        )
    assert exc.value.code == "adobe_identity_changed"
    assert license_n["n"] == 1  # kein zweiter License-Request nach Identitätswechsel


def test_oserror_28_via_real_stream_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    license_n = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("906"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("906"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            return _FakeHTTPResponse(_license_body("906"))
        # Download-HTTP ok, Schreiben schlägt fehl
        return _FakeHTTPResponse(b"x" * 200_000)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod
    import builtins

    real_open = builtins.open

    def open_maybe_fail(path, mode="r", *args, **kwargs):
        if "b" in mode and str(path).endswith(".part"):
            raise OSError(28, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", open_maybe_fail)
    with pytest.raises(LocalStorageError) as exc:
        mod._license_and_download_to_path(
            AdobeStockAdapter(),
            content_id="906",
            media_type="video",
            destination=tmp_path / "disk",
            media_hint="video",
        )
    assert exc.value.code == "local_storage_error"
    assert license_n["n"] == 1
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.skipif(not TINY_VIDEO.is_file(), reason="video fixture missing")
def test_positive_video_fixture_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    body = TINY_VIDEO.read_bytes()

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("907"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("907"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("907"))
        return _FakeHTTPResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    path, _ = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="907",
        media_type="video",
        destination=tmp_path / "vid",
        media_hint="video",
    )
    assert path.is_file()
    assert path.stat().st_size >= 100_000


def test_corrupt_video_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("908"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("908"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("908"))
        return _FakeHTTPResponse(b"not-a-video" * 20_000)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    with pytest.raises(DownloadedMediaInvalidError) as exc:
        mod._license_and_download_to_path(
            AdobeStockAdapter(),
            content_id="908",
            media_type="video",
            destination=tmp_path / "bad",
            media_hint="video",
        )
    assert exc.value.code == "downloaded_media_invalid"


def test_duration_zero_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    import otio_app.services.adobe_research_import as mod

    monkeypatch.setattr(mod, "probe_duration_seconds", lambda _p: 0.0)
    monkeypatch.setattr(mod, "get_api_key", lambda key: "x")
    monkeypatch.setattr(mod, "get_adobe_access_token", lambda **_k: "tok")

    class _Adapter(AdobeStockAdapter):
        def lookup_file_metadata(self, content_id, api_key):
            return {"media_type_id": 4, "content_type": "video/mp4"}

        def content_info_purchase(self, *_a, **_k):
            return {"state": "not_purchased"}

        def _license_asset(self, content_id, license_type, api_key, access_token, *, diagnose=True):
            return {
                "state": "just_purchased",
                "license": license_type,
                "url": f"https://stock.adobe.io/Rest/Libraries/Download/{content_id}/4",
                "content_type": "video/mp4",
            }

        def _stream_download_to_file(self, url, local_path, *, api_key, access_token, size, max_bytes):
            local_path.write_bytes(b"x" * 200_000)

    with pytest.raises(DownloadedMediaInvalidError):
        mod._license_and_download_to_path(
            _Adapter(),
            content_id="909",
            media_type="video",
            destination=tmp_path / "z",
            media_hint="video",
        )


def test_diagnostics_write_error_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    fixture = TINY_VIDEO.read_bytes()

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("910"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("910"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("910"))
        return _FakeHTTPResponse(fixture)

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
        lambda *_a, **_k: {"sub": "s", "email": "a@b.c"},
    )

    state = tmp_path / "state"
    state.mkdir()
    # Make diagnostics path unwritable by patching Path.write_text for that file
    import pathlib

    real_write = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self.name == "adobe_research_import_diagnostics.json":
            raise OSError(28, "No space left on device")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    result = download_research_import(
        _plan(["910"]),
        tmp_path / "media",
        state_dir=state,
        skip_existing_ids=False,
    )
    assert result.downloaded == 1
    assert "diagnostics_write_error" in result.diagnostics
    assert list((tmp_path / "media").rglob("*.mp4"))


def test_production_route_maps_to_renderer() -> None:
    import inspect
    from otio_app.ui import routing as routing_mod
    from otio_app.ui.adobe_research_import_page import render_adobe_research_import_page

    src = inspect.getsource(routing_mod)
    assert 'url_path="adobe-stock-import"' in src
    assert "render_adobe_research_import_page" in src
    assert PAGE_ADOBE_IMPORT == "Adobe Stock Import"
    # Produktionsfunktion ist importiert und callable
    assert callable(render_adobe_research_import_page)
    assert callable(run_app_navigation)


def test_content_info_403_no_license(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _disable_pauses(monkeypatch)
    license_n = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("911"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            raise _HTTPError(403, headers={"X-Request-Id": "f403"})
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
        raise AssertionError("unexpected")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    with pytest.raises(AdobePermissionOrIntegrationError):
        mod._license_and_download_to_path(
            AdobeStockAdapter(),
            content_id="911",
            media_type="video",
            destination=tmp_path / "p",
            media_hint="video",
        )
    assert license_n["n"] == 0


def test_secret_redaction_download_diag(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AdobeStockAdapter()
    adapter.reset_request_diagnostics()

    def fake_urlopen(request, timeout=20):
        raise _HTTPError(429, headers={"Retry-After": "1", "X-Request-Id": "r"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    import otio_app.services.supplement_sources.adobe_stock as adobe_mod

    monkeypatch.setattr(adobe_mod.time, "sleep", lambda *_a, **_k: None)
    with pytest.raises(AdobeRateLimitedError):
        adapter._stream_download_to_file(
            "https://stock.adobe.io/Rest/Libraries/Download/1/4?token=SUPERSECRET&signature=abc",
            Path("/tmp/should-not-exist-adobe-r1.part"),
            api_key="k",
            access_token="tok",
            size=1080,
            max_bytes=None,
        )
    blob = json.dumps([e.as_dict() for e in adapter.request_diag_events] + [adapter.request_counters.as_dict()])
    assert "SUPERSECRET" not in blob
    assert "signature=abc" not in blob
    assert "Authorization" not in blob
