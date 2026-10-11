"""Shared HTTP helper for venue clients: GET JSON, backing off on rate limits."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx


def get_json(
    http: httpx.Client,
    path: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GET `path` and return the JSON body.

    On 429 Too Many Requests, waits 1s, 2s, 4s, ... and retries up to
    `max_retries` times. Any other error status raises immediately.
    """
    for attempt in range(max_retries + 1):
        response = http.get(path, params=params)
        if response.status_code != 429 or attempt == max_retries:
            break
        sleep(2**attempt)
    response.raise_for_status()
    return response.json()
