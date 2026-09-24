"""Shared HTTP session with retries."""

from __future__ import annotations

import time

import requests

from ..config import USER_AGENT

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 30, retries: int = 4) -> requests.Response:
    """GET with exponential backoff on connection errors and 429/5xx."""
    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = session().get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = requests.HTTPError(f"{r.status_code} for {r.url}")
            else:
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc
