"""Streamlit-UI: Adobe IMS OAuth Login / Callback."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from otio_app.services.adobe_stock_oauth import (
    AdobeOAuthError,
    adobe_oauth_status,
    build_authorize_url,
    clear_token_store,
    decode_access_token_claims,
    exchange_authorization_code,
    extract_code_from_callback,
    get_adobe_access_token,
    get_adobe_redirect_uri,
    has_oauth_client_credentials,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter


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
        claims = decode_access_token_claims()
        if claims:
            from otio_app.services.supplement_sources.adobe_stock import token_fingerprint

            raw_email = str(claims.get("email") or "")
            if "@" in raw_email:
                local, _, domain = raw_email.partition("@")
                email = f"{local[:2]}…@{domain}" if local else f"…@{domain}"
            else:
                email = raw_email or "—"
            sub = claims.get("sub") or "—"
            fp = token_fingerprint(get_adobe_access_token())
            st.caption(
                f"OAuth-Konto: `{email}` · sub=`{sub}` · token_fp=`{fp or '—'}` "
                "— muss mit stock.adobe.com übereinstimmen."
            )
    elif status.source == "env":
        st.info(status.message)
    else:
        st.warning(status.message)

    with st.expander("API-Konto / Entitlement prüfen (wenn Browser-Unlimited geht, API aber nicht)"):
        st.caption(
            "Browser-Lizenzierung und Stock-API können verschiedene Konten/Rechte sehen. "
            "Hier die Werte, die **dieses OAuth-Token** bei Adobe bekommt."
        )
        test_id = st.text_input(
            "Optionale Content-ID (z. B. gerade im Browser lizenziertes Video)",
            key=f"{key_prefix}_diag_content_id",
            placeholder="644202290",
        )
        if st.button("Member/Profile + License-Status abfragen", key=f"{key_prefix}_diag_run"):
            api_key = get_api_key("ADOBE_STOCK_API_KEY") or ""
            token = get_adobe_access_token() or ""
            if not api_key or not token:
                st.error("API-Key oder Access-Token fehlt.")
            else:
                from otio_app.defaults import (
                    ADOBE_STOCK_LICENSE_TYPE_STANDARD,
                    ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
                    ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
                )

                adapter = AdobeStockAdapter()
                cid = (test_id or "").strip() or None

                def _profile(license_type: str) -> dict:
                    params: dict = {"license": license_type, "locale": "en_US"}
                    if cid:
                        params["content_id"] = cid
                    payload = adapter._request_licensing_json_safe(
                        ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
                        params,
                        api_key,
                        token,
                    )
                    return adapter._summarize_member_profile(payload)

                st.write("**Member/Profile Standard**")
                st.json(_profile(ADOBE_STOCK_LICENSE_TYPE_STANDARD))
                st.write("**Member/Profile Video_HD**")
                st.json(_profile(ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD))
                if cid:
                    from otio_app.services.supplement_sources.adobe_stock import (
                        classify_adobe_url,
                    )

                    def _redact_purchase(details: dict) -> dict:
                        out = dict(details or {})
                        url = str(out.pop("url", "") or "")
                        if url:
                            out["url_class"] = classify_adobe_url(url)
                            out["has_download_url"] = bool(url)
                        return out

                    info4k = adapter.content_info_purchase(cid, "Video_4K", api_key, token)
                    info_hd = adapter.content_info_purchase(cid, "Video_HD", api_key, token)
                    history = adapter.find_license_history_download(cid, api_key, token)
                    st.write("**Content/Info Video_4K** (URL redigiert)")
                    st.json(_redact_purchase(info4k))
                    st.write("**Content/Info Video_HD** (URL redigiert)")
                    st.json(_redact_purchase(info_hd))
                    st.write("**LicenseHistory-Treffer** (nur Diagnose, nicht Import-Hot-Path)")
                    st.json(_redact_purchase(history) if history else {"found": False})
                    st.caption(
                        "Erwartung nach Browser-Lizenz: Content/Info `state=purchased` "
                        "und `url_class=download` (nicht `watermarked`). "
                        "LicenseHistory nur manuell hier — nicht pro Asset im Import."
                    )
                    st.write("**Request-Zähler (diese Diagnose)**")
                    st.json(adapter.request_counters.as_dict())

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
