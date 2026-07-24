"""Adobe IMS OAuth: Authorize-URL, Callback-Parsing, Token-Store, Refresh."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from otio_app.services import adobe_stock_oauth as oauth


@pytest.fixture()
def oauth_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(oauth, "ensure_data_dir", lambda: tmp_path)
    monkeypatch.setattr(oauth, "get_api_key", lambda key: {
        "ADOBE_STOCK_API_KEY": "client-id-123",
        "ADOBE_STOCK_CLIENT_SECRET": "client-secret-xyz",
        "ADOBE_STOCK_REDIRECT_URI": "http://127.0.0.1:8501/adobe-stock-import",
        "ADOBE_STOCK_ACCESS_TOKEN": None,
    }.get(key))
    return tmp_path


def test_build_authorize_url_contains_required_params(oauth_data_dir: Path) -> None:
    url = oauth.build_authorize_url()  # erzeugt CSRF-State
    parsed = urlparse(url)
    assert parsed.netloc == "ims-na1.adobelogin.com"
    assert parsed.path.endswith("/ims/authorize/v2")
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["client-id-123"]
    assert qs["response_type"] == ["code"]
    assert qs["state"][0]
    assert "openid" in qs["scope"][0]
    assert "offline_access" in qs["scope"][0]
    assert qs["redirect_uri"] == ["http://127.0.0.1:8501/adobe-stock-import"]
    assert (oauth_data_dir / oauth.ADOBE_STOCK_OAUTH_STATE_FILENAME).is_file()


def test_extract_code_from_callback_url() -> None:
    code, state = oauth.extract_code_from_callback(
        "http://127.0.0.1:8501/adobe-stock-import?code=AUTHCODE&state=s1"
    )
    assert code == "AUTHCODE"
    assert state == "s1"


def test_extract_code_from_callback_error() -> None:
    with pytest.raises(oauth.AdobeOAuthError, match="access_denied"):
        oauth.extract_code_from_callback(
            "http://127.0.0.1:8501/?error=access_denied&error_description=nope"
        )


def test_exchange_authorization_code_stores_tokens(
    oauth_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = oauth.create_oauth_state()

    def fake_post(form: dict[str, str]) -> dict:
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "THECODE"
        return {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "token_type": "bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth, "_post_token_form", fake_post)
    stored = oauth.exchange_authorization_code("THECODE", state=state)
    assert stored["access_token"] == "access-1"
    assert stored["refresh_token"] == "refresh-1"
    assert oauth.get_adobe_access_token() == "access-1"
    assert oauth.adobe_oauth_status().source == "oauth"


def test_refresh_access_token(
    oauth_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oauth.save_token_store(
        {
            "access_token": "old",
            "refresh_token": "refresh-1",
            "expires_at": 0,
        }
    )

    def fake_post(form: dict[str, str]) -> dict:
        assert form["grant_type"] == "refresh_token"
        assert form["refresh_token"] == "refresh-1"
        return {
            "access_token": "new-access",
            "token_type": "bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth, "_post_token_form", fake_post)
    token = oauth.get_adobe_access_token()
    assert token == "new-access"


def test_env_fallback_when_no_oauth_store(
    oauth_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        oauth,
        "get_api_key",
        lambda key: {
            "ADOBE_STOCK_API_KEY": "client-id-123",
            "ADOBE_STOCK_CLIENT_SECRET": "client-secret-xyz",
            "ADOBE_STOCK_ACCESS_TOKEN": "manual-token",
        }.get(key),
    )
    assert oauth.get_adobe_access_token() == "manual-token"
    status = oauth.adobe_oauth_status()
    assert status.source == "env"


def test_basic_auth_header_encoding() -> None:
    header = oauth._basic_auth_header("id", "secret")
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    assert decoded == "id:secret"


def test_clear_token_store(oauth_data_dir: Path) -> None:
    oauth.save_token_store({"access_token": "x", "refresh_token": "y"})
    oauth.create_oauth_state()
    oauth.clear_token_store()
    assert oauth.load_token_store() is None
    assert not (oauth_data_dir / oauth.ADOBE_STOCK_OAUTH_STATE_FILENAME).is_file()
