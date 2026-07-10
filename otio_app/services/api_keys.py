"""API-Schlüssel: .env, user_secrets.env und Sitzungs-Overrides."""

from __future__ import annotations

import os
import re
from pathlib import Path

from otio_app.api_providers import API_PROVIDERS
from otio_app.config import APP_ROOT, DATA_DIR, ensure_data_dir, get_env

USER_SECRETS_FILENAME = "user_secrets.env"
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

_runtime_overrides: dict[str, str] = {}


def user_secrets_path() -> Path:
    return ensure_data_dir() / USER_SECRETS_FILENAME


def load_user_secrets_into_environ() -> None:
    """Lädt .env und user_secrets erneut (z. B. nach Override-Reset)."""
    from dotenv import load_dotenv

    load_dotenv(APP_ROOT / ".env")
    path = user_secrets_path()
    if path.is_file():
        load_dotenv(path, override=True)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(stripped)
        if match is None:
            continue
        key, raw_value = match.group(1), match.group(2)
        values[key] = _unquote(raw_value)
    return values


def _unquote(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
        return trimmed[1:-1]
    return trimmed


def _quote(value: str) -> str:
    if not value:
        return ""
    if any(char in value for char in ' #"\''):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_user_secrets(values: dict[str, str]) -> None:
    """Schreibt API-Schlüssel dauerhaft nach data/user_secrets.env."""
    path = user_secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = parse_env_file(path)
    for provider in API_PROVIDERS:
        if provider.env_key in values:
            merged[provider.env_key] = values[provider.env_key]

    lines = [
        "# API-Schlüssel — lokal gespeichert, nicht committen.",
        "# Wird von der App unter Systemstatus verwaltet.",
        "",
    ]
    for provider in API_PROVIDERS:
        lines.append(f"# {provider.label}")
        lines.append(f"{provider.env_key}={_quote(merged.get(provider.env_key, ''))}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    for provider in API_PROVIDERS:
        key = provider.env_key
        value = merged.get(key, "").strip()
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def _persisted_value(env_key: str) -> str | None:
    secrets = parse_env_file(user_secrets_path())
    if secrets.get(env_key, "").strip():
        return secrets[env_key].strip()
    base = parse_env_file(APP_ROOT / ".env")
    if base.get(env_key, "").strip():
        return base[env_key].strip()
    return None


def set_runtime_api_key(env_key: str, value: str | None) -> None:
    """Setzt einen Schlüssel nur für die laufende App (bis Neustart)."""
    if value and value.strip():
        cleaned = value.strip()
        _runtime_overrides[env_key] = cleaned
        os.environ[env_key] = cleaned
        return
    _runtime_overrides.pop(env_key, None)
    persisted = _persisted_value(env_key)
    if persisted:
        os.environ[env_key] = persisted
    else:
        os.environ.pop(env_key, None)


def clear_runtime_overrides() -> None:
    _runtime_overrides.clear()
    load_user_secrets_into_environ()
    for key, value in parse_env_file(APP_ROOT / ".env").items():
        if key not in parse_env_file(user_secrets_path()) and value.strip():
            os.environ[key] = value.strip()


def get_api_key(env_key: str) -> str | None:
    override = _runtime_overrides.get(env_key)
    if override and override.strip():
        return override.strip()
    value = get_env(env_key)
    if value and value.strip():
        return value.strip()
    return None


def is_api_key_set(env_key: str) -> bool:
    return bool(get_api_key(env_key))


def mask_api_key(value: str | None) -> str:
    if not value:
        return "—"
    cleaned = value.strip()
    if len(cleaned) <= 8:
        return "••••"
    return f"{cleaned[:4]}…{cleaned[-4:]}"
