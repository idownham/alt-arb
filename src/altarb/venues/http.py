"""Shared HTTP helper for venue clients: GET JSON, backing off on rate limits."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_RETRY_AFTER = 60.0  # never wait longer than this on a server's Retry-After


def get_json(
    http: httpx.Client,
    path: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GET `path` and return the JSON body.

    On 429 Too Many Requests, waits as long as the server's Retry-After header
    says (capped at MAX_RETRY_AFTER), or 1s, 2s, 4s, ... if there isn't one, and
    retries up to `max_retries` times. Any other error status raises immediately.
    """
    for attempt in range(max_retries + 1):
        response = http.get(path, params=params)
        if response.status_code != 429 or attempt == max_retries:
            break
        wait = _retry_after(response) or float(2**attempt)
        log.warning("429 rate limited on %s; retrying in %.0fs", path, wait)
        sleep(wait)
    response.raise_for_status()
    return response.json()


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return min(float(response.headers["retry-after"]), MAX_RETRY_AFTER)
    except (KeyError, ValueError):
        return None  # missing, or an HTTP-date we don't bother parsing
