"""Adobe IMS OAuth (Authorization Code) für Adobe Stock Lizenzierung/Download.

Flow:
1. Nutzer speichert Client-ID (`ADOBE_STOCK_API_KEY`) + Client Secret
2. App öffnet Adobe Authorize-URL
3. Adobe redirected mit `?code=&state=` zurück (Redirect-URI)
4. App tauscht Code gegen Access-/Refresh-Token und speichert sie lokal
5. Abgelaufene Access-Tokens werden via Refresh-Token erneuert

Manuelles `ADOBE_STOCK_ACCESS_TOKEN` bleibt als Fallback erhalten.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from otio_app.config import ensure_data_dir
from otio_app.defaults import ADOBE_STOCK_OAUTH_DEFAULT_REDIRECT_URI
from otio_app.services.api_keys import get_api_key

ADOBE_IMS_AUTHORIZE_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
ADOBE_IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
# Minimale Scopes für Stock-User-Auth. Zu breite Listen (z. B. offline_access,
# creative_sdk) liefern bei manchen Stock-Web-App-Credentials `invalid_scope`.
ADOBE_STOCK_OAUTH_DEFAULT_SCOPES = "openid,AdobeID"
ADOBE_STOCK_OAUTH_TOKEN_FILENAME = "adobe_stock_oauth.json"
ADOBE_STOCK_OAUTH_STATE_FILENAME = "adobe_stock_oauth_state.json"
# Access-Token etwas vor Ablauf refreshen.
_EXPIRY_SKEW_SECONDS = 90


@dataclass(frozen=True)
class AdobeOAuthStatus:
    logged_in: bool
    has_refresh_token: bool
    expires_at: float | None
    source: str  # oauth | env | none
    message: str


class AdobeOAuthError(RuntimeError):
    """Fehler beim Adobe-IMS-OAuth-Flow."""


def oauth_token_path() -> Path:
    return ensure_data_dir() / ADOBE_STOCK_OAUTH_TOKEN_FILENAME


def oauth_state_path() -> Path:
    return ensure_data_dir() / ADOBE_STOCK_OAUTH_STATE_FILENAME


def get_adobe_client_id() -> str | None:
    return get_api_key("ADOBE_STOCK_API_KEY")


def get_adobe_client_secret() -> str | None:
    return get_api_key("ADOBE_STOCK_CLIENT_SECRET")


def get_adobe_redirect_uri() -> str:
    configured = get_api_key("ADOBE_STOCK_REDIRECT_URI")
    if configured:
        return configured.strip()
    return ADOBE_STOCK_OAUTH_DEFAULT_REDIRECT_URI


def has_oauth_client_credentials() -> bool:
    return bool(get_adobe_client_id() and get_adobe_client_secret())


def load_token_store() -> dict[str, Any] | None:
    path = oauth_token_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_token_store(payload: dict[str, Any]) -> Path:
    path = oauth_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def clear_token_store() -> None:
    path = oauth_token_path()
    if path.is_file():
        path.unlink()
    state_path = oauth_state_path()
    if state_path.is_file():
        state_path.unlink()


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def create_oauth_state() -> str:
    state = secrets.token_urlsafe(24)
    path = oauth_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"state": state, "created_at": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return state


def consume_oauth_state(expected: str | None) -> bool:
    """Prüft und verbraucht den gespeicherten CSRF-State."""
    if not expected:
        return False
    path = oauth_state_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    stored = str(data.get("state") or "")
    try:
        path.unlink()
    except OSError:
        pass
    return bool(stored) and secrets.compare_digest(stored, expected)


def build_authorize_url(*, state: str | None = None, scopes: str | None = None) -> str:
    client_id = get_adobe_client_id()
    if not client_id:
        raise AdobeOAuthError("ADOBE_STOCK_API_KEY (Client ID) fehlt.")
    if not get_adobe_client_secret():
        raise AdobeOAuthError("ADOBE_STOCK_CLIENT_SECRET fehlt.")
    oauth_state = state or create_oauth_state()
    params = {
        "client_id": client_id,
        "redirect_uri": get_adobe_redirect_uri(),
        "scope": scopes or ADOBE_STOCK_OAUTH_DEFAULT_SCOPES,
        "response_type": "code",
        "state": oauth_state,
    }
    return f"{ADOBE_IMS_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def extract_code_from_callback(url_or_query: str) -> tuple[str, str | None]:
    """Extrahiert `code` und optional `state` aus einer Callback-URL oder Query."""
    text = (url_or_query or "").strip()
    if not text:
        raise AdobeOAuthError("Callback-URL ist leer.")
    if "://" in text or text.startswith("?"):
        parsed = urlparse(text if "://" in text else f"http://local{text}")
        query = parse_qs(parsed.query)
    else:
        query = parse_qs(text)
    code = (query.get("code") or [None])[0]
    state = (query.get("state") or [None])[0]
    error = (query.get("error") or [None])[0]
    if error:
        desc = (query.get("error_description") or [""])[0]
        raise AdobeOAuthError(f"Adobe OAuth Fehler: {error} {desc}".strip())
    if not code:
        raise AdobeOAuthError("Kein `code` in der Callback-URL gefunden.")
    return str(code), (str(state) if state else None)


def _post_token_form(form: dict[str, str]) -> dict[str, Any]:
    client_id = get_adobe_client_id()
    client_secret = get_adobe_client_secret()
    if not client_id or not client_secret:
        raise AdobeOAuthError(
            "OAuth-Credentials fehlen (ADOBE_STOCK_API_KEY / ADOBE_STOCK_CLIENT_SECRET)."
        )
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        ADOBE_IMS_TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": _basic_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise AdobeOAuthError(
            f"Adobe Token-Endpoint HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AdobeOAuthError(f"Adobe Token-Endpoint nicht erreichbar: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdobeOAuthError("Adobe Token-Antwort ist kein JSON.") from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise AdobeOAuthError(f"Unerwartete Token-Antwort: {payload!r}"[:400])
    return payload


def _store_token_response(payload: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    expires_in = int(payload.get("expires_in") or 0)
    now = time.time()
    stored = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token")
        or (previous or {}).get("refresh_token"),
        "token_type": payload.get("token_type") or "bearer",
        "expires_in": expires_in,
        "expires_at": now + expires_in if expires_in > 0 else (previous or {}).get("expires_at"),
        "obtained_at": now,
        "scope": payload.get("scope") or (previous or {}).get("scope"),
        "sub": payload.get("sub") or (previous or {}).get("sub"),
    }
    save_token_store(stored)
    return stored


def exchange_authorization_code(code: str, *, state: str | None = None, verify_state: bool = True) -> dict[str, Any]:
    if verify_state and not consume_oauth_state(state):
        raise AdobeOAuthError(
            "OAuth-State ungültig oder abgelaufen — bitte Login erneut starten."
        )
    payload = _post_token_form(
        {
            "grant_type": "authorization_code",
            "code": code,
            # redirect_uri muss laut OAuth mit dem Authorize-Request übereinstimmen
            "redirect_uri": get_adobe_redirect_uri(),
        }
    )
    return _store_token_response(payload)


def refresh_access_token() -> str:
    store = load_token_store() or {}
    refresh = store.get("refresh_token")
    if not refresh:
        raise AdobeOAuthError("Kein Refresh-Token vorhanden — bitte erneut mit Adobe anmelden.")
    payload = _post_token_form(
        {
            "grant_type": "refresh_token",
            "refresh_token": str(refresh),
        }
    )
    stored = _store_token_response(payload, previous=store)
    return str(stored["access_token"])


def _token_is_fresh(store: dict[str, Any]) -> bool:
    token = store.get("access_token")
    if not token:
        return False
    expires_at = store.get("expires_at")
    if expires_at is None:
        return True
    try:
        return float(expires_at) > (time.time() + _EXPIRY_SKEW_SECONDS)
    except (TypeError, ValueError):
        return True


def get_adobe_access_token(*, force_refresh: bool = False) -> str | None:
    """Liefert einen gültigen Access-Token (OAuth bevorzugt, sonst Env-Fallback)."""
    store = load_token_store()
    if store:
        if force_refresh and store.get("refresh_token"):
            try:
                return refresh_access_token()
            except AdobeOAuthError:
                pass
        elif _token_is_fresh(store):
            return str(store["access_token"])
        elif store.get("refresh_token"):
            try:
                return refresh_access_token()
            except AdobeOAuthError:
                pass
        elif store.get("access_token"):
            # Abgelaufen ohne Refresh — trotzdem versuchen (Adobe mag noch akzeptieren).
            return str(store["access_token"])

    manual = get_api_key("ADOBE_STOCK_ACCESS_TOKEN")
    return manual.strip() if manual else None


def decode_access_token_claims(access_token: str | None = None) -> dict[str, Any]:
    """Liest unverifizierte JWT-Claims aus dem Access-Token (nur Anzeige)."""
    token = access_token or get_adobe_access_token()
    if not token or token.count(".") < 2:
        return {}
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    keep = ("sub", "email", "email_verified", "created_at", "expires_in", "scope", "client_id")
    return {key: data[key] for key in keep if key in data}


def adobe_oauth_status() -> AdobeOAuthStatus:
    store = load_token_store()
    if store and store.get("access_token"):
        expires_at = store.get("expires_at")
        try:
            expires_at_f = float(expires_at) if expires_at is not None else None
        except (TypeError, ValueError):
            expires_at_f = None
        fresh = _token_is_fresh(store)
        has_refresh = bool(store.get("refresh_token"))
        if fresh:
            msg = "Mit Adobe angemeldet (OAuth) — Lizenzierung/Download möglich."
        elif has_refresh:
            msg = "OAuth-Token abgelaufen — wird beim nächsten Download automatisch erneuert."
        else:
            msg = "OAuth-Token abgelaufen — bitte erneut mit Adobe anmelden."
        return AdobeOAuthStatus(
            logged_in=True,
            has_refresh_token=has_refresh,
            expires_at=expires_at_f,
            source="oauth",
            message=msg,
        )

    if get_api_key("ADOBE_STOCK_ACCESS_TOKEN"):
        return AdobeOAuthStatus(
            logged_in=False,
            has_refresh_token=False,
            expires_at=None,
            source="env",
            message=(
                "Manuelles ADOBE_STOCK_ACCESS_TOKEN gesetzt (Fallback). "
                "Empfohlen: OAuth-Login für automatische Verlängerung."
            ),
        )

    return AdobeOAuthStatus(
        logged_in=False,
        has_refresh_token=False,
        expires_at=None,
        source="none",
        message="Nicht bei Adobe angemeldet — OAuth-Login oder Access-Token nötig.",
    )
