"""DIAG-002-R1 Produktions-Smoke: echte Route-Funktion, kein UI-Nachbau.

Ruft `render_adobe_research_import_page` auf — dieselbe Funktion, die
`otio_app/ui/routing.py` für `url_path=\"adobe-stock-import\"` registriert.

Szenarien über Umgebungsvariable ADOBE_R1_SMOKE_SCENARIO:
  route | info429 | cancelled | watermarked | download429

Service-Grenzen werden gemockt (Job-Manager / Readiness / OAuth-Status).
Keine echten Tokens und keine signierten Download-URLs.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="OTIO Adobe R1 Production Smoke", layout="wide")

SCENARIO = os.environ.get("ADOBE_R1_SMOKE_SCENARIO", "route").strip().lower()


def _seed_mocks() -> None:
    from otio_app.services.adobe_research_import import (
        AdobeResearchImportItemResult,
        AdobeResearchImportResult,
    )
    from otio_app.services.adobe_research_import_job import (
        JobStatus,
        ResearchImportJobState,
        get_research_import_job_manager,
    )
    from otio_app.services import adobe_download_projects as projects_mod
    from otio_app.services.adobe_download_projects import AdobeDownloadProject
    from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
    from otio_app.ui import adobe_oauth_panel as oauth_mod
    from otio_app.ui import adobe_research_import_page as page_mod

    project_id = "r1-smoke-project"
    target = Path("/tmp/adobe-r1-smoke-media")
    target.mkdir(parents=True, exist_ok=True)

    proj = AdobeDownloadProject(
        id=project_id,
        name="R1 Smoke Project",
        target_root=str(target),
        excel_filename="smoke.xlsx",
        sheet_name="Sheet1",
        selected_chapters=[],
        skip_existing_ids=False,
        chapter_count=1,
        asset_count=18,
    )

    st.session_state[page_mod._ACTIVE_PROJECT_KEY] = project_id

    def fake_list():
        return [proj]

    def fake_get(pid):
        return proj if pid == project_id else None

    def fake_load_plan(pid):
        from otio_app.services.adobe_research_import import (
            AdobeResearchAsset,
            AdobeResearchChapter,
            AdobeResearchImportPlan,
        )

        return AdobeResearchImportPlan(
            sheet_name="Sheet1",
            chapters=(
                AdobeResearchChapter(
                    title="Test",
                    folder_name="Test",
                    assets=tuple(
                        AdobeResearchAsset(asset_id=str(1000 + i), media_hint="video")
                        for i in range(18)
                    ),
                ),
            ),
        )

    projects_mod.list_download_projects = fake_list  # type: ignore[assignment]
    projects_mod.get_download_project = fake_get  # type: ignore[assignment]
    page_mod.list_download_projects = fake_list  # type: ignore[assignment]
    page_mod.get_download_project = fake_get  # type: ignore[assignment]
    page_mod.load_project_plan = fake_load_plan  # type: ignore[assignment]

    class _Ready:
        acquire_enabled = True
        search_enabled = True
        message = "Smoke: Adobe ready (mocked, kein echtes Token)."

    AdobeStockAdapter.readiness = lambda self: _Ready()  # type: ignore[method-assign]
    AdobeStockAdapter.probe_video_entitlement = lambda self, *_a, **_k: {  # type: ignore[method-assign]
        "available_entitlement": {"quota": 999, "full_entitlement_quota": {"video_quota": 999}},
        "lacks_video": False,
    }

    def fake_oauth_panel(*, key_prefix: str = "adobe_oauth") -> None:
        st.subheader("Adobe-Anmeldung (OAuth)")
        st.success("OAuth aktiv (Production-Smoke-Mock — kein echtes Token).")
        st.caption(
            "OAuth-Konto: `jo…@example.com` · sub=`smoke-sub-001` · token_fp=`fad424cfb7`"
        )
        with st.expander("API-Konto / Entitlement prüfen", expanded=True):
            st.json(
                {
                    "available_entitlement": {"quota": 999, "is_cce": True},
                    "purchase_options": {"state": "possible"},
                }
            )
            st.caption("URLs redigiert — url_class statt vollständiger Download-URL.")

    oauth_mod.render_adobe_oauth_panel = fake_oauth_panel  # type: ignore[assignment]
    page_mod.render_adobe_oauth_panel = fake_oauth_panel  # type: ignore[assignment]

    counters = {
        "content_info": 36,
        "content_license": 17,
        "member_profile": 0,
        "license_history": 0,
        "license_history_pages": 0,
        "http_429": 3,
        "retries": 2,
        "licensed_ok": 17,
        "already_licensed": 0,
        "cancelled": 0,
        "watermarked": 0,
        "local_storage_errors": 0,
        "invalid_media": 0,
    }
    recent = []
    items = []
    stop_reason = ""
    message = "Import fertig (Smoke)."

    if SCENARIO == "info429":
        stop_reason = "adobe_rate_limited"
        message = "Import gestoppt: Content/Info HTTP 429 bei Asset 18."
        counters["content_license"] = 17  # kein License für Asset 18
        counters["content_info"] = 17 + 3  # 17 ok + 3 Versuche Asset 18
        recent = [
            {
                "timestamp": "2026-07-25T07:00:00Z",
                "endpoint": "Content/Info",
                "content_id": "1017",
                "license_type": "Video_4K",
                "attempt": 3,
                "batch_id": "r1smoke",
                "asset_index": 18,
                "http_status": 429,
                "request_id": "req-info-429",
                "retry_after": "2",
                "url_class": "missing",
                "has_download_url": False,
            }
        ]
        for i in range(17):
            items.append(
                AdobeResearchImportItemResult(
                    chapter_title="Test",
                    folder_name="Test",
                    asset_id=str(1000 + i),
                    status="downloaded",
                    license="Video_4K",
                    message=f"Test_Asset_{i+1:02d}.mp4",
                )
            )
        items.append(
            AdobeResearchImportItemResult(
                chapter_title="Test",
                folder_name="Test",
                asset_id="1017",
                status="error",
                message=(
                    "[adobe_rate_limited] Content/Info HTTP 429 nach 3 Versuchen "
                    "(X-Request-Id=req-info-429)"
                ),
            )
        )
    elif SCENARIO == "cancelled":
        stop_reason = "adobe_license_transaction_cancelled"
        counters = {
            **counters,
            "content_license": 2,
            "licensed_ok": 0,
            "cancelled": 2,
            "http_429": 0,
            "retries": 0,
            "content_info": 2,
        }
        message = "Dual cancelled Video_4K+Video_HD."
        items = [
            AdobeResearchImportItemResult(
                chapter_title="Test",
                folder_name="Test",
                asset_id="2001",
                status="error",
                message=(
                    "[adobe_license_transaction_cancelled] Video_4K und Video_HD cancelled "
                    "(nicht Rate-Limit)."
                ),
            )
        ]
    elif SCENARIO == "watermarked":
        counters = {
            **counters,
            "licensed_ok": 0,
            "watermarked": 1,
            "http_429": 0,
            "content_license": 1,
            "content_info": 1,
            "license_history": 0,
        }
        message = "Watermarked blockiert."
        items = [
            AdobeResearchImportItemResult(
                chapter_title="Test",
                folder_name="Test",
                asset_id="3001",
                status="error",
                message=(
                    "[adobe_watermarked_preview_only] url_class=watermarked — "
                    "kein Vollversions-Download."
                ),
            )
        ]
    elif SCENARIO == "download429":
        stop_reason = "adobe_rate_limited"
        counters = {
            **counters,
            "content_license": 1,
            "licensed_ok": 1,
            "http_429": 3,
            "retries": 2,
            "content_info": 1,
            "license_history": 0,
        }
        message = "Download HTTP 429 nach 3 Versuchen (gleiche URL, keine Neu-Lizenzierung)."
        recent = [
            {
                "timestamp": "2026-07-25T07:10:00Z",
                "endpoint": "Content/License",
                "content_id": "4001",
                "license_type": "Video_4K",
                "attempt": 1,
                "batch_id": "r1smoke",
                "asset_index": 1,
                "http_status": 200,
                "request_id": "req-lic-ok",
                "purchase_state": "just_purchased",
                "url_class": "download",
                "has_download_url": True,
            }
        ]
        items = [
            AdobeResearchImportItemResult(
                chapter_title="Test",
                folder_name="Test",
                asset_id="4001",
                status="error",
                message=(
                    "[adobe_rate_limited] Adobe-Download rate-limited (HTTP 429) nach 3 Versuchen "
                    "(request_id=req-dl-429). Kein neuer Content/License."
                ),
            )
        ]
    else:
        # route: leere Idle-Ansicht der echten Seite
        items = []

    result = AdobeResearchImportResult(
        target_root=str(target),
        items=items,
        diagnostics={
            "batch_id": "r1smoke",
            "oauth_sub": "smoke-sub-001",
            "oauth_email_redacted": "jo…@example.com",
            "token_fingerprint": "fad424cfb7",
            "batch_stop_reason": stop_reason,
            "request_counters": counters,
            "recent_requests": recent,
        },
    )
    job = ResearchImportJobState(
        project_id=project_id,
        status=JobStatus.COMPLETED if items or stop_reason else JobStatus.IDLE,
        target_root=str(target),
        message=message,
        result=result if (items or stop_reason) else None,
        fraction=1.0 if items else 0.0,
        done=len([i for i in items if i.status == "downloaded"]),
        total=max(len(items), 1),
    )

    mgr = get_research_import_job_manager()
    mgr._jobs[project_id] = job  # noqa: SLF001 — Smoke-Seed


_seed_mocks()

st.caption(
    f"R1 Production Smoke · Scenario=`{SCENARIO}` · "
    "Renderer=`render_adobe_research_import_page` · Route=`adobe-stock-import`"
)

from otio_app.ui.adobe_research_import_page import render_adobe_research_import_page

render_adobe_research_import_page()
