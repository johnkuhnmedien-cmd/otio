"""Fail-closed HTTPS-Fetch für Enhanced Stock-Previews und Vollmedien."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import requests

from otio_app.services.without_voiceover_enhanced.stock.http_utils import STOCK_USER_AGENT

PREVIEW_MAX_BYTES = 5 * 1024 * 1024
FULL_MEDIA_MAX_BYTES = 200 * 1024 * 1024
MAX_REDIRECTS = 3
PREVIEW_TIMEOUT_SEC = 10
FULL_TIMEOUT_SEC = 120

ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)

ALLOWED_MEDIA_CONTENT_TYPES = ALLOWED_IMAGE_CONTENT_TYPES | frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "application/octet-stream",
    }
)

# Host-Suffixe je Provider (fail-closed: unbekannter Provider → leer).
PROVIDER_HOST_SUFFIXES: dict[str, tuple[str, ...]] = {
    "pexels": (
        "pexels.com",
        "images.pexels.com",
        "videos.pexels.com",
        "www.pexels.com",
    ),
    "pixabay": (
        "pixabay.com",
        "cdn.pixabay.com",
        "i.pixabay.com",
        "www.pixabay.com",
    ),
    "wikimedia": (
        "wikimedia.org",
        "upload.wikimedia.org",
        "commons.wikimedia.org",
    ),
    "openverse": (
        "openverse.org",
        "api.openverse.org",
        "wordpress.com",
        "wp.com",
        "staticflickr.com",
        "flickr.com",
        "creativecommons.org",
    ),
    "archive_org": (
        "archive.org",
        "www.archive.org",
        "ia601504.us.archive.org",
    ),
}


class SafeFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SafeFetchResult:
    url: str
    content: bytes
    content_type: str
    final_url: str


def _host_allowed(hostname: str, allowed_suffixes: Iterable[str]) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    for suffix in allowed_suffixes:
        s = suffix.lower().lstrip(".")
        if host == s or host.endswith("." + s):
            return True
    return False


def _is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


# Spezieller Provider für manuelle Gap-URL-Zuordnung: jeder öffentliche HTTPS-Host,
# aber weiter fail-closed gegen Localhost/private IPs / Credentials.
MANUAL_URL_PROVIDER = "manual_url"


def resolve_and_validate_host(
    hostname: str,
    *,
    allowed_suffixes: Iterable[str],
    allow_any_public_host: bool = False,
) -> list[str]:
    """DNS auflösen und gegen Allowlist + private IPs prüfen."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        raise SafeFetchError("Leerer Hostname.")
    if host in {"localhost", "localhost.localdomain"}:
        raise SafeFetchError("Localhost ist nicht erlaubt.")
    if not allow_any_public_host and not _host_allowed(host, allowed_suffixes):
        raise SafeFetchError(f"Host nicht in Allowlist: {host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SafeFetchError(f"DNS-Auflösung fehlgeschlagen: {host} ({exc})") from exc
    addresses: list[str] = []
    for info in infos:
        addr = info[4][0]
        if _is_blocked_ip(addr):
            raise SafeFetchError(f"Private/reservierte IP blockiert: {addr} ({host})")
        addresses.append(addr)
    if not addresses:
        raise SafeFetchError(f"Keine öffentlichen IPs für Host: {host}")
    return addresses


def validate_fetch_url(
    url: str,
    *,
    provider: str,
    require_https: bool = True,
) -> tuple[str, str]:
    """Prüft Schema/Host/Credentials; liefert (normalized_url, hostname)."""
    raw = (url or "").strip()
    if not raw:
        raise SafeFetchError("Leere URL.")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if require_https and scheme != "https":
        raise SafeFetchError(f"Nur HTTPS erlaubt, erhalten: {scheme or 'kein Schema'}")
    if scheme not in {"http", "https"}:
        raise SafeFetchError(f"Ungültiges URL-Schema: {scheme}")
    if parsed.username or parsed.password:
        raise SafeFetchError("Zugangsdaten in URL sind nicht erlaubt.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise SafeFetchError("URL ohne Hostname.")
    provider_key = (provider or "").strip().lower()
    allow_any = provider_key == MANUAL_URL_PROVIDER
    suffixes = PROVIDER_HOST_SUFFIXES.get(provider_key, ())
    if not allow_any and not suffixes:
        raise SafeFetchError(f"Unbekannter/unerlaubter Provider: {provider}")
    resolve_and_validate_host(
        host,
        allowed_suffixes=suffixes,
        allow_any_public_host=allow_any,
    )
    return raw, host


def _content_type_base(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def safe_http_get(
    url: str,
    *,
    provider: str,
    max_bytes: int,
    timeout_sec: float,
    allowed_content_types: frozenset[str],
    headers: dict[str, str] | None = None,
) -> SafeFetchResult:
    """HTTPS-GET mit Redirect-Revalidierung, Size-/Type-Limits."""
    current, _host = validate_fetch_url(url, provider=provider)
    request_headers = {"User-Agent": STOCK_USER_AGENT}
    if headers:
        request_headers.update(headers)

    for redirect_count in range(MAX_REDIRECTS + 1):
        validate_fetch_url(current, provider=provider)
        response = requests.get(
            current,
            headers=request_headers,
            timeout=timeout_sec,
            allow_redirects=False,
            stream=True,
        )
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location") or ""
            response.close()
            if redirect_count >= MAX_REDIRECTS:
                raise SafeFetchError("Zu viele Redirects.")
            if not location:
                raise SafeFetchError("Redirect ohne Location.")
            # Relative Redirects auflösen.
            current = requests.compat.urljoin(current, location)
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            response.close()
            raise SafeFetchError(f"HTTP-Fehler: {exc}") from exc

        content_type = _content_type_base(response.headers.get("Content-Type"))
        if content_type in {"text/html", "application/json", "text/plain"}:
            response.close()
            raise SafeFetchError(f"Content-Type nicht als Medium erlaubt: {content_type}")
        if allowed_content_types and content_type and content_type not in allowed_content_types:
            # Manche CDNs liefern leeren/fehlenden Type — Bytes später prüfen.
            if content_type:
                response.close()
                raise SafeFetchError(f"Content-Type nicht erlaubt: {content_type}")

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise SafeFetchError(
                        f"Antwort größer als Limit ({max_bytes} Bytes)."
                    )
                chunks.append(chunk)
        finally:
            response.close()

        data = b"".join(chunks)
        if not data:
            raise SafeFetchError("Leere Antwort.")
        return SafeFetchResult(
            url=url,
            content=data,
            content_type=content_type,
            final_url=current,
        )

    raise SafeFetchError("Redirect-Limit überschritten.")


def decode_preview_image(content: bytes) -> tuple[int, int]:
    """Pillow-Decodierung; liefert (width, height)."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise SafeFetchError("Pillow fehlt für Preview-Decodierung.") from exc
    from io import BytesIO

    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            width, height = image.size
            if width <= 0 or height <= 0:
                raise SafeFetchError("Preview ohne positive Abmessungen.")
            return width, height
    except UnidentifiedImageError as exc:
        raise SafeFetchError("Preview ist kein decodierbares Bild.") from exc
    except SafeFetchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SafeFetchError(f"Preview-Decodierung fehlgeschlagen: {exc}") from exc


def fetch_preview_image_bytes(
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
) -> SafeFetchResult:
    result = safe_http_get(
        url,
        provider=provider,
        max_bytes=PREVIEW_MAX_BYTES,
        timeout_sec=PREVIEW_TIMEOUT_SEC,
        allowed_content_types=ALLOWED_IMAGE_CONTENT_TYPES,
        headers=headers,
    )
    decode_preview_image(result.content)
    return result


def fetch_full_media_bytes(
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
) -> SafeFetchResult:
    return safe_http_get(
        url,
        provider=provider,
        max_bytes=FULL_MEDIA_MAX_BYTES,
        timeout_sec=FULL_TIMEOUT_SEC,
        allowed_content_types=ALLOWED_MEDIA_CONTENT_TYPES,
        headers=headers,
    )
