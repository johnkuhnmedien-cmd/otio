"""E2: AppTest über echte Import-Seite → JobManager.start → download_research_import."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.defaults import (
    ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
    ADOBE_STOCK_FILES_ENDPOINT,
    ADOBE_STOCK_LICENSE_ENDPOINT,
)
from otio_app.services.adobe_download_projects import AdobeDownloadProject
from otio_app.services.adobe_research_import import (
    AdobeResearchAsset,
    AdobeResearchChapter,
    AdobeResearchImportPlan,
)
from otio_app.services.adobe_research_import_job import (
    JobStatus,
    ResearchImportJobManager,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter

SCRIPT = Path(__file__).parent / "_apptest_scripts" / "adobe_e2_route_import_smoke.py"
TINY = Path(__file__).parent / "fixtures" / "adobe_tiny_valid.mp4"
PROJECT_ID = "e2-route-smoke"


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
    def __init__(self, code: int, *, headers: dict | None = None, body: bytes = b"", url: str = ""):
        super().__init__(url or "https://stock.adobe.io/x", code, str(code), headers or {}, BytesIO(body))


@pytest.mark.skipif(not TINY.is_file(), reason="video fixture missing")
def test_apptest_route_start_runs_real_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "e2-apptest"
    media = root / "media"
    state_dir = root / "state" / PROJECT_ID
    media.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    monkeypatch.setenv("ADOBE_E2_SMOKE_ROOT", str(root))
    monkeypatch.setenv("ADOBE_E2_SMOKE_PROJECT_ID", PROJECT_ID)

    import otio_app.services.adobe_download_projects as projects_mod
    import otio_app.services.adobe_research_import as import_mod
    import otio_app.services.adobe_research_import_job as job_mod
    import otio_app.services.adobe_stock_oauth as oauth_svc
    import otio_app.services.api_keys as api_keys_mod
    import otio_app.ui.adobe_oauth_panel as oauth_mod
    import otio_app.ui.adobe_research_import_page as page_mod
    import urllib.request

    proj = AdobeDownloadProject(
        id=PROJECT_ID,
        name="E2 Route Smoke",
        target_root=str(media),
        excel_filename="smoke.xlsx",
        sheet_name="Sheet1",
        selected_chapters=["E2"],
        skip_existing_ids=False,
        chapter_count=1,
        asset_count=1,
    )

    def fake_list():
        return [proj]

    def fake_get(pid):
        return proj if pid == PROJECT_ID else None

    def fake_load_plan(pid):
        return AdobeResearchImportPlan(
            sheet_name="Sheet1",
            chapters=(
                AdobeResearchChapter(
                    title="E2",
                    folder_name="E2",
                    assets=(AdobeResearchAsset(asset_id="8100", media_hint="video"),),
                ),
            ),
        )

    def fake_update(pid, **kwargs):
        return proj

    for mod in (projects_mod, page_mod):
        monkeypatch.setattr(mod, "list_download_projects", fake_list)
        monkeypatch.setattr(mod, "get_download_project", fake_get)
        monkeypatch.setattr(mod, "update_download_project", fake_update)
        monkeypatch.setattr(mod, "project_dir", lambda pid: state_dir)
    monkeypatch.setattr(page_mod, "load_project_plan", fake_load_plan)
    monkeypatch.setattr(job_mod, "project_dir", lambda pid: state_dir)
    monkeypatch.setattr(projects_mod, "project_dir", lambda pid: state_dir)

    class _Ready:
        acquire_enabled = True
        search_enabled = True
        message = "E2 Smoke: Adobe ready (mocked)."

    monkeypatch.setattr(AdobeStockAdapter, "readiness", lambda self: _Ready())
    monkeypatch.setattr(
        AdobeStockAdapter,
        "probe_video_entitlement",
        lambda self, *_a, **_k: {"available_entitlement": {"quota": 999}, "lacks_video": False},
    )

    def fake_oauth(*, key_prefix: str = "adobe_oauth") -> None:
        import streamlit as st

        st.subheader("Adobe-Anmeldung (OAuth)")
        st.success("OAuth aktiv (E2-Mock — kein echtes Token).")

    monkeypatch.setattr(oauth_mod, "render_adobe_oauth_panel", fake_oauth)
    monkeypatch.setattr(page_mod, "render_adobe_oauth_panel", fake_oauth)

    for name in (
        "_ASSET_PAUSE_SECONDS",
        "_API_CALL_PAUSE_SECONDS",
        "_LICENSE_RETRY_PAUSE_SECONDS",
        "_DOWNLOAD_START_PAUSE_SECONDS",
        "_POST_ASSET_PAUSE_SECONDS",
    ):
        monkeypatch.setattr(import_mod, name, 0)

    monkeypatch.setattr(oauth_svc, "get_adobe_access_token", lambda **_k: "t")
    monkeypatch.setattr(api_keys_mod, "get_api_key", lambda key: "k")
    monkeypatch.setattr(import_mod, "get_api_key", lambda key: "k")
    monkeypatch.setattr(import_mod, "get_adobe_access_token", lambda **_k: "t")
    monkeypatch.setattr(
        import_mod,
        "decode_access_token_claims",
        lambda *_a, **_k: {"sub": "e2-smoke-sub", "email": "e2@example.com"},
    )

    fixture = TINY.read_bytes()
    _real_urlopen = urllib.request.urlopen

    def fake_urlopen(request, timeout=20):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        host = urllib.parse.urlparse(url).hostname or ""
        adobe_ish = (
            "adobe" in host
            or host.endswith("example-adobe-test.test")
            or ADOBE_STOCK_FILES_ENDPOINT in url
            or ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url
            or ADOBE_STOCK_LICENSE_ENDPOINT in url
        )
        if not adobe_ish:
            return _real_urlopen(request, timeout=timeout)
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(
                json.dumps(
                    {"files": [{"id": "8100", "media_type_id": 4, "content_type": "video/mp4"}]}
                ).encode()
            )
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(
                json.dumps(
                    {
                        "contents": {
                            "8100": {
                                "content_id": "8100",
                                "size": "Comp",
                                "purchase_details": {"state": "not_purchased"},
                            }
                        }
                    }
                ).encode()
            )
        if ADOBE_STOCK_LICENSE_ENDPOINT in url:
            return _FakeHTTPResponse(
                json.dumps(
                    {
                        "contents": {
                            "8100": {
                                "content_id": "8100",
                                "size": "Original",
                                "purchase_details": {
                                    "state": "purchased",
                                    "license": "Video_4K",
                                    "url": "https://stock.adobe.io/Rest/Libraries/Download/8100/4",
                                    "content_type": "video/mp4",
                                },
                            }
                        }
                    }
                ).encode()
            )
        if host == "stock.adobe.io" and "/Download/" in url:
            raise _HTTPError(
                302,
                headers={"Location": "https://cdn.example-adobe-test.test/e2.mp4?sig=REDACT_ME"},
                url=url,
            )
        return _FakeHTTPResponse(fixture)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # RUNNING-Polling der Seite begrenzt (monkeypatch = auto-restore).
    import streamlit as st

    _rerun_calls = {"n": 0}
    _orig_rerun = st.rerun

    def _limited_rerun(*_a, **_k):
        _rerun_calls["n"] += 1
        if _rerun_calls["n"] > 4:
            return None
        return _orig_rerun()

    monkeypatch.setattr(st, "rerun", _limited_rerun)

    # Frischer Job-Manager — kein Result-Seed.
    job_mod._MANAGER = ResearchImportJobManager()  # noqa: SLF001

    at = AppTest.from_file(str(SCRIPT), default_timeout=20)
    at.run()
    assert not at.exception, at.exception

    mgr = job_mod.get_research_import_job_manager()
    before = mgr.get_state(PROJECT_ID)
    assert before.result is None
    assert before.status == JobStatus.IDLE

    start_buttons = [
        b
        for b in at.button
        if "Lizenzieren" in (b.label or "") or "herunterladen" in (b.label or "")
    ]
    assert start_buttons, f"Start-Button fehlt: {[b.label for b in at.button]}"
    start_buttons[0].click().run(timeout=20)
    assert not at.exception, at.exception

    deadline = time.time() + 30
    final = None
    while time.time() < deadline:
        final = mgr.get_state(PROJECT_ID)
        if final.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            break
        time.sleep(0.05)

    assert final is not None
    assert final.status == JobStatus.COMPLETED, final.error or final.message
    assert final.result is not None
    assert final.result.downloaded >= 1
    assert final.result.diagnostics["request_counters"]["license_history"] == 0
    assert list(media.rglob("*.mp4")), "keine lokale MP4 nach Job"
