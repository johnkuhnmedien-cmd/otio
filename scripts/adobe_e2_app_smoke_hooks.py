"""Hooks für ADOBE_E2_APP_SMOKE=1 — nur Evidence-Smoke, kein Produktionsdefault.

Wird aus app.py nur geladen, wenn die Umgebungsvariable gesetzt ist.
Mockt die Adobe-HTTP-Grenze und navigiert zur Route adobe-stock-import.
Kein Seed fertiger AdobeResearchImportResult-Objekte in JobManager._jobs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

ROOT = Path(os.environ.get("ADOBE_E2_SMOKE_ROOT", "/tmp/adobe-e2-app-smoke"))
PROJECT_ID = "e2-app-smoke"
TINY = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "adobe_tiny_valid.mp4"


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


def install_before_navigation() -> None:
    from otio_app.defaults import (
        ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
        ADOBE_STOCK_FILES_ENDPOINT,
        ADOBE_STOCK_LICENSE_ENDPOINT,
    )
    from otio_app.services import adobe_download_projects as projects_mod
    from otio_app.services.adobe_download_projects import AdobeDownloadProject
    from otio_app.services.adobe_research_import import (
        AdobeResearchAsset,
        AdobeResearchChapter,
        AdobeResearchImportPlan,
    )
    import otio_app.services.adobe_research_import as import_mod
    import otio_app.services.adobe_research_import_job as job_mod
    import otio_app.services.supplement_sources.adobe_stock as adobe_mod
    from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
    from otio_app.ui import adobe_oauth_panel as oauth_mod
    from otio_app.ui import adobe_research_import_page as page_mod
    from otio_app.ui.routing import PENDING_SWITCH_URL_PATH_KEY
    import streamlit as st

    media = ROOT / "media"
    media.mkdir(parents=True, exist_ok=True)
    state = ROOT / "state" / PROJECT_ID
    state.mkdir(parents=True, exist_ok=True)

    proj = AdobeDownloadProject(
        id=PROJECT_ID,
        name="E2 App.py Smoke",
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
                    assets=(AdobeResearchAsset(asset_id="8200", media_hint="video"),),
                ),
            ),
        )

    def fake_update(pid, **kwargs):
        return proj

    projects_mod.list_download_projects = fake_list  # type: ignore[assignment]
    projects_mod.get_download_project = fake_get  # type: ignore[assignment]
    projects_mod.update_download_project = fake_update  # type: ignore[assignment]
    projects_mod.project_dir = lambda pid: state  # type: ignore[assignment]
    job_mod.project_dir = lambda pid: state  # type: ignore[assignment]
    page_mod.list_download_projects = fake_list  # type: ignore[assignment]
    page_mod.get_download_project = fake_get  # type: ignore[assignment]
    page_mod.load_project_plan = fake_load_plan  # type: ignore[assignment]
    page_mod.update_download_project = fake_update  # type: ignore[assignment]
    page_mod.project_dir = lambda pid: state  # type: ignore[assignment]
    st.session_state[page_mod._ACTIVE_PROJECT_KEY] = PROJECT_ID
    st.session_state[PENDING_SWITCH_URL_PATH_KEY] = "adobe-stock-import"

    class _Ready:
        acquire_enabled = True
        search_enabled = True
        message = "E2 app.py Smoke: Adobe ready (mocked)."

    AdobeStockAdapter.readiness = lambda self: _Ready()  # type: ignore[method-assign]
    AdobeStockAdapter.probe_video_entitlement = lambda self, *_a, **_k: {  # type: ignore[method-assign]
        "available_entitlement": {"quota": 999},
        "lacks_video": False,
    }

    def fake_oauth(*, key_prefix: str = "adobe_oauth") -> None:
        st.subheader("Adobe-Anmeldung (OAuth)")
        st.success("OAuth aktiv (E2 app.py-Mock — kein echtes Token).")

    oauth_mod.render_adobe_oauth_panel = fake_oauth  # type: ignore[assignment]
    page_mod.render_adobe_oauth_panel = fake_oauth  # type: ignore[assignment]

    for name in (
        "_ASSET_PAUSE_SECONDS",
        "_API_CALL_PAUSE_SECONDS",
        "_LICENSE_RETRY_PAUSE_SECONDS",
        "_DOWNLOAD_START_PAUSE_SECONDS",
        "_POST_ASSET_PAUSE_SECONDS",
    ):
        setattr(import_mod, name, 0)
    adobe_mod.time.sleep = lambda *_a, **_k: None  # type: ignore[method-assign]

    fixture = TINY.read_bytes() if TINY.is_file() else (b"\x00" * 200_000)

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if ADOBE_STOCK_FILES_ENDPOINT in url:
            return _FakeHTTPResponse(
                json.dumps(
                    {"files": [{"id": "8200", "media_type_id": 4, "content_type": "video/mp4"}]}
                ).encode()
            )
        if ADOBE_STOCK_CONTENT_INFO_ENDPOINT in url:
            return _FakeHTTPResponse(
                json.dumps(
                    {
                        "contents": {
                            "8200": {
                                "content_id": "8200",
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
                            "8200": {
                                "content_id": "8200",
                                "size": "Original",
                                "purchase_details": {
                                    "state": "purchased",
                                    "license": "Video_4K",
                                    "url": "https://stock.adobe.io/Rest/Libraries/Download/8200/4",
                                    "content_type": "video/mp4",
                                },
                            }
                        }
                    }
                ).encode()
            )
        host = urllib.parse.urlparse(url).hostname or ""
        if host == "stock.adobe.io" and "/Download/" in url:
            raise _HTTPError(
                302,
                headers={"Location": "https://cdn.example-adobe-test.test/e2app.mp4?sig=REDACT"},
                url=url,
            )
        return _FakeHTTPResponse(fixture)

    urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
    import_mod.get_api_key = lambda key: "k"  # type: ignore[assignment]
    import_mod.get_adobe_access_token = lambda **_k: "t"  # type: ignore[assignment]
    import_mod.decode_access_token_claims = lambda *_a, **_k: {  # type: ignore[assignment]
        "sub": "e2-app-sub",
        "email": "e2app@example.com",
    }
    (ROOT / "app_smoke_mocks.ok").write_text("installed\n", encoding="utf-8")
