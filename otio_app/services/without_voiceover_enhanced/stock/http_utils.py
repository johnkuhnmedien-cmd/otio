"""Gemeinsame HTTP-Helfer für Stock-Provider (UA, Retry bei 429)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Wikimedia verlangt einen beschreibenden User-Agent mit Kontaktpfad.
STOCK_USER_AGENT = (
    "OTIO-Schnittplaner/0.1 "
    "(https://github.com/johnkuhnmedien-cmd/otio; stock-search)"
)

DEFAULT_TIMEOUT_SEC = 30
MAX_RETRIES_ON_429 = 3
DEFAULT_BACKOFF_SEC = 1.5


def stock_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries_on_429: int = MAX_RETRIES_ON_429,
) -> requests.Response:
    """GET mit Standard-UA und exponentiellem Backoff bei HTTP 429."""
    merged = {"User-Agent": STOCK_USER_AGENT}
    if headers:
        merged.update(headers)
    last_response: requests.Response | None = None
    for attempt in range(max_retries_on_429 + 1):
        response = requests.get(
            url, params=params, headers=merged, timeout=timeout
        )
        last_response = response
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if attempt >= max_retries_on_429:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            sleep_sec = float(retry_after) if retry_after else DEFAULT_BACKOFF_SEC * (
                2**attempt
            )
        except ValueError:
            sleep_sec = DEFAULT_BACKOFF_SEC * (2**attempt)
        sleep_sec = min(max(sleep_sec, 0.5), 30.0)
        logger.warning(
            "Stock HTTP 429 for %s — retry %s/%s in %.1fs",
            url,
            attempt + 1,
            max_retries_on_429,
            sleep_sec,
        )
        time.sleep(sleep_sec)
    assert last_response is not None
    last_response.raise_for_status()
    return last_response
