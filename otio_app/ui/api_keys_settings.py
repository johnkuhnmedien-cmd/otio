"""Streamlit-UI: API-Schlüssel verwalten."""

from __future__ import annotations

import streamlit as st

from otio_app.api_providers import API_PROVIDERS
from otio_app.services.api_keys import (
    get_api_key,
    is_api_key_set,
    mask_api_key,
    set_runtime_api_key,
    write_user_secrets,
)
from otio_app.services.gemini_client import is_gemini_configured


def _session_draft_key(env_key: str) -> str:
    return f"api_key_draft_{env_key}"


def _apply_runtime_from_session() -> None:
    for provider in API_PROVIDERS:
        draft = st.session_state.get(_session_draft_key(provider.env_key), "")
        if draft:
            set_runtime_api_key(provider.env_key, draft)


def render_api_keys_settings() -> None:
    """Formular für API-Schlüssel (Systemstatus)."""
    st.subheader("🔑 API-Schlüssel")
    st.caption(
        "Schlüssel können hier eingegeben werden — ohne Terminal. "
        "**Nur Sitzung** wirkt sofort bis zum Neustart; **Speichern** legt sie in "
        "`data/user_secrets.env` ab (nicht in Git)."
    )

    drafts: dict[str, str] = {}
    for provider in API_PROVIDERS:
        status = "✅ gesetzt" if is_api_key_set(provider.env_key) else "⚪ nicht gesetzt"
        badge = " · **aktiv in App**" if provider.implemented else " · *Demnächst*"
        st.markdown(f"**{provider.label}** — {status}{badge}")
        st.caption(provider.description)
        if provider.docs_url:
            st.markdown(f"[Schlüssel anlegen]({provider.docs_url})")

        current = get_api_key(provider.env_key)
        if current:
            st.caption(f"Aktuell: `{mask_api_key(current)}`")

        draft_key = _session_draft_key(provider.env_key)
        if draft_key not in st.session_state:
            st.session_state[draft_key] = ""

        value = st.text_input(
            f"{provider.label} API-Key",
            type="password",
            placeholder=provider.placeholder or "Key einfügen …",
            key=draft_key,
            label_visibility="collapsed",
        )
        drafts[provider.env_key] = value.strip()
        st.divider()

    col_session, col_save, col_clear = st.columns(3)
    with col_session:
        if st.button("▶️ Nur diese Sitzung übernehmen", key="api_keys_session"):
            for env_key, value in drafts.items():
                if value:
                    set_runtime_api_key(env_key, value)
            st.success("Schlüssel für diese Sitzung aktiv.")
            st.rerun()
    with col_save:
        if st.button("💾 Dauerhaft speichern", key="api_keys_save", type="primary"):
            to_save = {
                env_key: value
                for env_key, value in drafts.items()
                if value
            }
            if to_save:
                write_user_secrets(to_save)
                for env_key, value in to_save.items():
                    set_runtime_api_key(env_key, value)
                st.success(f"{len(to_save)} Schlüssel gespeichert.")
            else:
                st.warning("Bitte mindestens einen neuen Key eingeben.")
            st.rerun()
    with col_clear:
        if st.button("🗑️ Eingegebene Felder leeren", key="api_keys_clear"):
            for provider in API_PROVIDERS:
                st.session_state[_session_draft_key(provider.env_key)] = ""
            st.rerun()

    if is_gemini_configured():
        st.success("Gemini ist konfiguriert — Asset-Analysen und Gemini-Schnittpläne möglich.")
    else:
        st.caption(
            "Gemini-Key fehlt — Asset-Analysen und Gemini-Modelle im Schnittplan nicht verfügbar. "
            "OpenAI/Claude-Keys ermöglichen alternative Planungsmodelle im Tab **Vorschlag**."
        )
