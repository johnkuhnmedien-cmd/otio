"""ADOBE-STOCK-LICENSING-DIAG-002-R1-EVIDENCE-02 — Redirect-Sicherheit + Prod-Pfad."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path

import pytest

from otio_app.defaults import (
    ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
    ADOBE_STOCK_FILES_ENDPOINT,
    ADOBE_STOCK_LICENSE_ENDPOINT,
)
from otio_app.services.adobe_research_import import (
    AdobeResearchAsset,
    AdobeResearchChapter,
    AdobeResearchImportPlan,
)
from otio_app.services.adobe_research_import_job import (
    JobStatus,
    ResearchImportJobManager,
)
from otio_app.services.supplement_sources.adobe_stock import (
    MAX_DOWNLOAD_REDIRECTS,
    AdobeRateLimitedError,
    AdobeStockAdapter,
    AdobeUnsafeRedirectError,
    is_safe_download_redirect_url,
    safe_download_url_label,
)

TINY_VIDEO = Path(__file__).resolve().parent / "fixtures" / "adobe_tiny_valid.mp4"

ADOBE_START = "https://stock.adobe.io/Rest/Libraries/Download/7001/4"
CDN_URL = (
    "https://cdn.example-adobe-test.test/signed/video.mp4"
    "?X-Amz-Signature=SIGNED_SECRET_VALUE&token=CDNTOKEN"
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

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _HTTPError(urllib.error.HTTPError):
    def __init__(
        self,
        code: int,
        *,
        headers: dict | None = None,
        body: bytes = b"",
        url: str = "https://stock.adobe.io/test",
    ):
        super().__init__(
            url=url,
            code=code,
            msg=str(code),
            hdrs=headers or {},
            fp=BytesIO(body),
        )


def _license_body(content_id: str, *, url: str = "") -> bytes:
    if not url:
        url = f"https://stock.adobe.io/Rest/Libraries/Download/{content_id}/4"
    return json.dumps(
        {
            "contents": {
                str(content_id): {
                    "content_id": content_id,
                    "size": "Original",
                    "purchase_details": {
                        "state": "just_purchased",
                        "license": "Video_4K",
                        "url": url,
                        "content_type": "video/mp4",
                    },
                }
            }
        }
    ).encode()


def _info_body(content_id: str) -> bytes:
    return json.dumps(
        {
            "contents": {
                str(content_id): {
                    "content_id": content_id,
                    "size": "Comp",
                    "purchase_details": {"state": "not_purchased"},
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


def test_is_safe_download_redirect_url_rules() -> None:
    assert is_safe_download_redirect_url(CDN_URL)
    assert not is_safe_download_redirect_url("http://cdn.example/x")
    assert not is_safe_download_redirect_url("https://user:pass@cdn.example/x")
    assert not is_safe_download_redirect_url(
        "https://stock.adobe.io/Rest/Libraries/Watermarked/1/comp"
    )
    assert "SIGNED_SECRET" not in safe_download_url_label(CDN_URL)
    assert "cdn.example-adobe-test.test" in safe_download_url_label(CDN_URL)


@pytest.mark.skipif(not TINY_VIDEO.is_file(), reason="video fixture missing")
def test_redirect_adobe_to_https_cdn_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    body = TINY_VIDEO.read_bytes()
    seen_hosts: list[str] = []
    seen_headers: list[dict] = []

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        host = urllib.parse.urlparse(url).hostname or ""
        headers = {k: v for k, v in request.header_items()}
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("7001"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("7001"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(_license_body("7001", url=ADOBE_START))
        seen_hosts.append(host)
        seen_headers.append({k.lower(): v for k, v in headers.items()})
        if host == "stock.adobe.io":
            raise _HTTPError(
                302,
                headers={"Location": CDN_URL},
                url=url,
            )
        if host == "cdn.example-adobe-test.test":
            return _FakeHTTPResponse(body)
        raise AssertionError(f"unexpected host {host}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    path, _ = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="7001",
        media_type="video",
        destination=tmp_path / "ok",
        media_hint="video",
    )
    assert path.is_file()
    assert seen_hosts == ["stock.adobe.io", "cdn.example-adobe-test.test"]
    assert "x-api-key" in seen_headers[0]
    assert "x-product" in seen_headers[0]
    assert "x-api-key" not in seen_headers[1]
    assert "x-product" not in seen_headers[1]
    assert "authorization" not in seen_headers[1]


def test_redirect_to_http_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_pauses(monkeypatch)

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if "stock.adobe.io" in url and "/Download/" in url:
            raise _HTTPError(
                302,
                headers={"Location": "http://cdn.example/file.mp4"},
                url=url,
            )
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    with pytest.raises(AdobeUnsafeRedirectError) as exc:
        adapter._stream_download_to_file(
            ADOBE_START,
            tmp_path / "x.part",
            api_key="k",
            access_token="t",
            size=1080,
            max_bytes=None,
        )
    assert "http://" not in str(exc.value).lower() or "unsicher" in str(exc.value).lower()
    assert not (tmp_path / "x.part").exists()


def test_redirect_userinfo_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_pauses(monkeypatch)

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if "/Download/" in url:
            raise _HTTPError(
                302,
                headers={"Location": "https://user:pass@cdn.example/file.mp4"},
                url=url,
            )
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AdobeUnsafeRedirectError):
        AdobeStockAdapter()._stream_download_to_file(
            ADOBE_START,
            tmp_path / "u.part",
            api_key="k",
            access_token="t",
            size=None,
            max_bytes=None,
        )
    assert not (tmp_path / "u.part").exists()


def test_redirect_loop_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_pauses(monkeypatch)
    a = "https://cdn.example-adobe-test.test/a"
    b = "https://cdn.example-adobe-test.test/b"
    n = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        n["n"] += 1
        if "stock.adobe.io" in url:
            raise _HTTPError(302, headers={"Location": a}, url=url)
        if url.startswith(a):
            raise _HTTPError(302, headers={"Location": b}, url=url)
        if url.startswith(b):
            raise _HTTPError(302, headers={"Location": a}, url=url)
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AdobeUnsafeRedirectError) as exc:
        AdobeStockAdapter()._stream_download_to_file(
            ADOBE_START,
            tmp_path / "loop.part",
            api_key="k",
            access_token="t",
            size=None,
            max_bytes=None,
        )
    assert "schleife" in str(exc.value).lower() or "loop" in str(exc.value).lower()
    assert n["n"] <= MAX_DOWNLOAD_REDIRECTS + 2
    assert not (tmp_path / "loop.part").exists()


def test_redirect_limit_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_pauses(monkeypatch)

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "stock.adobe.io":
            raise _HTTPError(
                302,
                headers={"Location": "https://cdn.example-adobe-test.test/h0"},
                url=url,
            )
        # unendliche Kette unterschiedlicher URLs
        hop_s = parsed.path.rsplit("/", 1)[-1].lstrip("h") or "0"
        hop = int(hop_s)
        raise _HTTPError(
            302,
            headers={"Location": f"https://cdn.example-adobe-test.test/h{hop + 1}"},
            url=url,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AdobeUnsafeRedirectError) as exc:
        AdobeStockAdapter()._stream_download_to_file(
            ADOBE_START,
            tmp_path / "lim.part",
            api_key="k",
            access_token="t",
            size=None,
            max_bytes=None,
        )
    assert str(MAX_DOWNLOAD_REDIRECTS) in str(exc.value) or "limit" in str(exc.value).lower()
    assert not (tmp_path / "lim.part").exists()


def test_signed_url_absent_from_diag_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    secret = "SIGNED_SECRET_VALUE_XYZ"

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if "stock.adobe.io" in url and "/Download/" in url:
            raise _HTTPError(
                302,
                headers={
                    "Location": (
                        f"https://cdn.example-adobe-test.test/v.mp4"
                        f"?X-Amz-Signature={secret}&token=abc"
                    )
                },
                url=url,
            )
        raise _HTTPError(403, headers={"X-Request-Id": "r1"}, url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AdobeStockAdapter()
    adapter.reset_request_diagnostics()
    with pytest.raises(Exception) as exc:
        adapter._stream_download_to_file(
            f"{ADOBE_START}?token=SUPERSECRET&signature=abc",
            tmp_path / "s.part",
            api_key="k",
            access_token="tok",
            size=1080,
            max_bytes=None,
        )
    text = str(exc.value)
    blob = json.dumps(
        [e.as_dict() for e in adapter.request_diag_events]
        + [adapter.request_counters.as_dict()]
        + [getattr(exc.value, "details", {})]
    )
    assert secret not in text
    assert secret not in blob
    assert "SUPERSECRET" not in text
    assert "SUPERSECRET" not in blob
    assert "signature=abc" not in text


@pytest.mark.skipif(not TINY_VIDEO.is_file(), reason="video fixture missing")
def test_download_429_after_redirect_no_relicense(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_pauses(monkeypatch)
    body = TINY_VIDEO.read_bytes()
    license_n = {"n": 0}
    download_n = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("7002"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("7002"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            return _FakeHTTPResponse(_license_body("7002", url=ADOBE_START))
        host = urllib.parse.urlparse(url).hostname or ""
        if host == "stock.adobe.io":
            raise _HTTPError(302, headers={"Location": CDN_URL}, url=url)
        download_n["n"] += 1
        if download_n["n"] == 1:
            raise _HTTPError(429, headers={"Retry-After": "0", "X-Request-Id": "d"})
        return _FakeHTTPResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")
    import otio_app.services.adobe_research_import as mod

    path, _ = mod._license_and_download_to_path(
        AdobeStockAdapter(),
        content_id="7002",
        media_type="video",
        destination=tmp_path / "r429",
        media_hint="video",
    )
    assert path.is_file()
    assert license_n["n"] == 1
    assert download_n["n"] == 2


@pytest.mark.skipif(not TINY_VIDEO.is_file(), reason="video fixture missing")
def test_job_manager_start_runs_download_research_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echter Produktionspfad: JobManager.start → download_research_import.

    Kein vorgefertigtes AdobeResearchImportResult in `_jobs`.
    """
    _disable_pauses(monkeypatch)
    body = TINY_VIDEO.read_bytes()
    license_n = {"n": 0}
    import_calls = {"n": 0}

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(_files_body("8001"))
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(_info_body("8001"))
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            license_n["n"] += 1
            return _FakeHTTPResponse(
                _license_body(
                    "8001",
                    url="https://stock.adobe.io/Rest/Libraries/Download/8001/4",
                )
            )
        return _FakeHTTPResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "k")
    monkeypatch.setenv("ADOBE_STOCK_ACCESS_TOKEN", "t")

    import otio_app.services.adobe_research_import as import_mod
    import otio_app.services.adobe_research_import_job as job_mod

    real_download = import_mod.download_research_import

    def tracking_download(*args, **kwargs):
        import_calls["n"] += 1
        return real_download(*args, **kwargs)

    monkeypatch.setattr(job_mod, "download_research_import", tracking_download)
    monkeypatch.setattr(import_mod, "download_research_import", tracking_download)

    class _Ready:
        acquire_enabled = True
        message = "ok"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.AdobeStockAdapter.readiness",
        lambda self: _Ready(),
    )
    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.decode_access_token_claims",
        lambda *_a, **_k: {"sub": "e2-sub", "email": "e2@example.com"},
    )

    project_id = "e2-job-proj"
    state_dir = tmp_path / "proj" / project_id
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(job_mod, "project_dir", lambda pid: state_dir)

    plan = AdobeResearchImportPlan(
        sheet_name="Sheet1",
        chapters=(
            AdobeResearchChapter(
                title="E2",
                folder_name="E2",
                assets=(AdobeResearchAsset(asset_id="8001", media_hint="video"),),
            ),
        ),
    )
    mgr = ResearchImportJobManager()
    # Bewusst leer — kein Seed eines fertigen Results.
    assert mgr.get_state(project_id).result is None
    started = mgr.start(
        project_id,
        plan,
        tmp_path / "media",
        chapter_titles=["E2"],
        skip_existing_ids=False,
    )
    assert started is True
    deadline = time.time() + 30
    while time.time() < deadline:
        state = mgr.get_state(project_id)
        if state.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            break
        time.sleep(0.05)
    state = mgr.get_state(project_id)
    assert state.status == JobStatus.COMPLETED, state.error or state.message
    assert import_calls["n"] == 1
    assert state.result is not None
    assert state.result.downloaded == 1
    assert license_n["n"] == 1
    assert state.result.diagnostics["request_counters"]["license_history"] == 0
    # Result entstand aus dem Job-Lauf, nicht aus vorherigem Seed.
    assert any((tmp_path / "media").rglob("*.mp4"))
