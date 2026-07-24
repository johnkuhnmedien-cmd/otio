"""Streamlit-UI: Adobe IMS OAuth Login / Callback."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from otio_app.services.adobe_stock_oauth import (
    AdobeOAuthError,
    adobe_oauth_status,
    build_authorize_url,
    clear_token_store,
    exchange_authorization_code,
    extract_code_from_callback,
    get_adobe_redirect_uri,
    has_oauth_client_credentials,
)


def _clear_oauth_query_params() -> None:
    try:
        params = dict(st.query_params)
    except Exception:  # noqa: BLE001
        return
    changed = False
    for key in ("code", "state", "error", "error_description"):
        if key in params:
            del st.query_params[key]
            changed = True
    if changed:
        # noop — Streamlit aktualisiert die URL; Caller kann rerun
        pass


def _try_handle_oauth_callback(key_prefix: str) -> bool:
    """Verarbeitet ?code=… aus der Redirect-URI. True wenn etwas verarbeitet wurde."""
    handled_key = f"{key_prefix}_oauth_handled"
    try:
        code = st.query_params.get("code")
        state = st.query_params.get("state")
        error = st.query_params.get("error")
    except Exception:  # noqa: BLE001
        return False

    if error:
        desc = st.query_params.get("error_description") or ""
        hint = ""
        if str(error) == "invalid_scope":
            hint = (
                " — Scope wird von diesem Credential nicht akzeptiert. "
                "App-Version pullen/neu starten; Login erneut versuchen."
            )
        st.error(f"Adobe-Login abgebrochen: {error} {desc}".strip() + hint)
        _clear_oauth_query_params()
        return True

    if not code:
        return False
    if st.session_state.get(handled_key) == code:
        return False

    try:
        exchange_authorization_code(str(code), state=str(state) if state else None)
        st.session_state[handled_key] = code
        _clear_oauth_query_params()
        st.success("Adobe-Login erfolgreich — Access-Token gespeichert.")
        st.rerun()
    except AdobeOAuthError as exc:
        st.error(f"OAuth-Token-Austausch fehlgeschlagen: {exc}")
        _clear_oauth_query_params()
    return True


def render_adobe_oauth_panel(*, key_prefix: str = "adobe_oauth") -> None:
    """Kompaktes Login-Panel für Import-Seite und API-Schlüssel."""
    st.subheader("Adobe-Anmeldung (OAuth)")
    st.caption(
        "Empfohlener Weg für Lizenzierung/Download: einmal mit Adobe anmelden. "
        "Access-Token werden lokal in `data/adobe_stock_oauth.json` gespeichert "
        "und bei Bedarf über den Refresh-Token erneuert."
    )

    _try_handle_oauth_callback(key_prefix)

    status = adobe_oauth_status()
    if status.source == "oauth" and status.logged_in:
        st.success(status.message)
        if status.expires_at:
            expires = datetime.fromtimestamp(status.expires_at, tz=timezone.utc)
            st.caption(f"Access-Token gültig bis (UTC): `{expires.isoformat(timespec='seconds')}`")
    elif status.source == "env":
        st.info(status.message)
    else:
        st.warning(status.message)

    redirect = get_adobe_redirect_uri()
    st.caption(
        f"Redirect-URI (exakt wie in der Adobe Developer Console): `{redirect}`"
    )
    if redirect.lower().startswith("https://"):
        st.info(
            "Adobe verlangt oft HTTPS als Redirect, Streamlit läuft lokal aber auf HTTP. "
            "Nach dem Adobe-Login erscheint ggf. ein SSL-Fehler: in der Adresszeile "
            "`https://` → `http://` ändern und Enter — der `?code=…`-Teil bleibt wichtig. "
            "Alternativ die komplette URL unter „Callback-URL manuell einfügen“ pasten."
        )

    if not has_oauth_client_credentials():
        st.error(
            "Für OAuth bitte unter **API-Schlüssel** setzen: "
            "`ADOBE_STOCK_API_KEY` (Client ID) und `ADOBE_STOCK_CLIENT_SECRET`."
        )
        return

    col_login, col_logout = st.columns(2)
    auth_url_key = f"{key_prefix}_authorize_url"
    with col_login:
        if st.button(
            "Mit Adobe anmelden",
            type="primary",
            key=f"{key_prefix}_start_login",
            use_container_width=True,
        ):
            try:
                # State erst beim Klick erzeugen — sonst überschreibt jedes Rerun den CSRF-State.
                st.session_state[auth_url_key] = build_authorize_url()
            except AdobeOAuthError as exc:
                st.error(str(exc))
        auth_url = st.session_state.get(auth_url_key)
        if auth_url:
            st.link_button(
                "Adobe-Login öffnen",
                auth_url,
                use_container_width=True,
            )
            st.caption("Nach dem Adobe-Login landest du wieder in der App (Redirect-URI).")
    with col_logout:
        if st.button("Abmelden / Token löschen", key=f"{key_prefix}_logout", use_container_width=True):
            clear_token_store()
            st.session_state.pop(auth_url_key, None)
            st.success("OAuth-Tokens gelöscht.")
            st.rerun()

    with st.expander("Callback-URL manuell einfügen (falls Redirect nicht greift)"):
        pasted = st.text_input(
            "Vollständige Redirect-URL nach Adobe-Login",
            key=f"{key_prefix}_callback_paste",
            placeholder=f"{redirect}?code=…&state=…",
        )
        if st.button("Code austauschen", key=f"{key_prefix}_exchange_paste"):
            try:
                code, state = extract_code_from_callback(pasted)
                exchange_authorization_code(code, state=state, verify_state=bool(state))
                st.success("Token gespeichert.")
                st.rerun()
            except AdobeOAuthError as exc:
                st.error(str(exc))
