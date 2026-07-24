"""DIAG-002 Streamlit smoke UI — redacted identity + request counters + error classes.

Self-contained (no otio_app import) so Streamlit can start without PYTHONPATH.
No real OAuth tokens and no signed download URLs are shown.
"""

from __future__ import annotations

import hashlib

import streamlit as st

st.set_page_config(page_title="Adobe DIAG-002 Smoke", layout="wide")
st.header("Adobe Stock Import (Research-Excel)")
st.caption("DIAG-002 Smoke — redigierte Diagnose, keine Secrets.")

st.subheader("Adobe-Anmeldung (OAuth)")
st.success("OAuth aktiv (Smoke-Mock — kein echtes Token).")
email = "jo…@example.com"
sub = "smoke-sub-001"
fp = hashlib.sha256(b"smoke-access-token-not-real").hexdigest()[:10]
st.caption(f"OAuth-Konto: `{email}` · sub=`{sub}` · token_fp=`{fp}`")

with st.expander("API-Konto / Entitlement prüfen", expanded=True):
    st.write("**Member/Profile Video_HD (Mock)**")
    st.json(
        {
            "available_entitlement": {"quota": 999, "is_cce": True},
            "purchase_options": {"state": "possible"},
            "member": {"stock_id": 1272100},
        }
    )
    st.write("**Content/Info Video_HD (URL redigiert)**")
    st.json(
        {
            "state": "purchased",
            "license": "Video_HD",
            "size": "Comp",
            "url_class": "download",
            "has_download_url": True,
        }
    )
    st.write("**Request-Zähler (diese Diagnose)**")
    st.json(
        {
            "content_info": 2,
            "content_license": 1,
            "member_profile": 2,
            "license_history": 1,
            "license_history_pages": 1,
            "licensed_ok": 1,
            "already_licensed": 1,
            "http_429": 0,
            "retries": 0,
            "cancelled": 0,
            "watermarked": 0,
            "local_storage_errors": 0,
            "invalid_media": 0,
        }
    )

st.subheader("Request-Diagnose (redigiert)")
batch_id = "smokebatch01"
counters = {
    "content_info": 36,
    "content_license": 20,
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
recent = [
    {
        "timestamp": "2026-07-24T23:00:00Z",
        "endpoint": "Content/License",
        "content_id": "100017",
        "license_type": "Video_4K",
        "attempt": 3,
        "batch_id": batch_id,
        "asset_index": 18,
        "http_status": 429,
        "request_id": "req-smoke-429",
        "retry_after": "2",
        "purchase_state": "",
        "url_class": "missing",
        "has_download_url": False,
        "duration_ms": 120,
    },
    {
        "timestamp": "2026-07-24T22:59:50Z",
        "endpoint": "Content/License",
        "content_id": "100016",
        "license_type": "Video_4K",
        "attempt": 1,
        "batch_id": batch_id,
        "asset_index": 17,
        "http_status": 200,
        "request_id": "req-smoke-ok",
        "retry_after": "",
        "purchase_state": "just_purchased",
        "url_class": "download",
        "has_download_url": True,
        "duration_ms": 340,
    },
]
st.caption(
    f"Batch `{batch_id}` · OAuth sub=`{sub}` · E-Mail=`{email}` · "
    f"Token-FP=`{fp}` · Stop=`adobe_rate_limited`"
)
st.json(counters)
st.write("Letzte Adobe-Requests (ohne Tokens/URLs)")
st.dataframe(recent, use_container_width=True, hide_index=True)

st.subheader("Fehler / Nicht verfügbar")
st.dataframe(
    [
        {
            "Kapitel": "Test",
            "Asset ID": "100017",
            "Status": "Fehler",
            "Fehler": "[adobe_rate_limited] Adobe rate-limited (HTTP 429) nach 3 Versuchen "
            "(Content/License, request_id=req-smoke-429). (X-Request-Id=req-smoke-429)",
        },
        {
            "Kapitel": "Test",
            "Asset ID": "900001",
            "Status": "Fehler",
            "Fehler": "[adobe_license_transaction_cancelled] state=cancelled "
            "(license=Video_HD, url_class=watermarked, request_id=req-cxl).",
        },
        {
            "Kapitel": "Test",
            "Asset ID": "900002",
            "Status": "Downloaded",
            "Fehler": "Video_HD · bereits lizenziert (state=purchased, url_class=download)",
        },
    ],
    use_container_width=True,
    hide_index=True,
)
st.success(
    "Smoke: Watermarked nie als Erfolg; size=Comp allein kein Fehler; "
    "keine Tokens/signierten URLs in UI."
)
