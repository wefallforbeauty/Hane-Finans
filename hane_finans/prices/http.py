"""Shared HTTP client settings."""

from __future__ import annotations

import httpx

from hane_finans import __version__

USER_AGENT = f"Hane-Finans/{__version__} (+https://github.com/wefallforbeauty/Hane-Finans)"


def make_client(transport: httpx.BaseTransport | None = None, timeout: float = 30.0) -> httpx.Client:
    """HTTP client with a descriptive User-Agent, redirects and connection retries.

    Tests pass an ``httpx.MockTransport``.
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        transport=transport or httpx.HTTPTransport(retries=2),
    )
