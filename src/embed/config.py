"""Runtime configuration for embed.

Reads EMBED_* env vars into a typed Config singleton.
Service is small enough to keep all knobs in one file --
matches the townsfolk / salmon pattern.

Deployment shape: behind Traefik. The service makes
outbound HTTPS to oembed providers + the target URLs,
so the container needs egress (no Traefik whitelist
on outbound). The inbound surface is one GET.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Single source of truth for everything operator-
    controllable. Built once from env at boot."""

    # Cap on bytes pulled from a target URL when we
    # scrape the <title> for the preview-card
    # fallback. 256 KiB is enough to cover head + a
    # couple of og: tags without giving a hostile
    # target an easy resource-exhaustion vector.
    fetch_byte_cap: int

    # Per-request HTTP timeout for outbound calls
    # (oembed JSON, HEAD probe, title scrape). 5s
    # is a balance between waiting on slow providers
    # and not holding the consumer's iframe spinner
    # forever -- the iframe is lazy-loaded so user-
    # facing latency is at scroll time.
    fetch_timeout_seconds: float

    # User-Agent on outbound calls. Some oembed
    # providers (notably Twitter/X) refuse non-
    # browser-shaped UAs. Default is a real-looking
    # string with an embed.openapis.ca callback in
    # parens so honest operators can grep us out of
    # their logs.
    fetch_user_agent: str

    # Cache-Control max-age on successful responses.
    # Embeds are near-immutable on a per-URL basis;
    # 1 hour is the sweet spot between catching
    # provider updates (e.g. a YouTube thumbnail
    # change) and not re-fetching on every page
    # view. Failures (5xx from the provider) are
    # NOT cached at this layer -- httpx returns the
    # error and the response is a fresh probe each
    # time.
    cache_max_age_seconds: int

    # In-process LRU size. One slot per URL. 1024 is
    # ~1MB at typical doc sizes; bump for sites with
    # heavy long-tail embed traffic. Cache is per-
    # worker; with N uvicorn workers you get N x size.
    cache_slots: int


def load() -> Config:
    return Config(
        fetch_byte_cap=int(
            os.environ.get("EMBED_FETCH_BYTE_CAP", str(256 * 1024)),
        ),
        fetch_timeout_seconds=float(
            os.environ.get("EMBED_FETCH_TIMEOUT_SECONDS", "5"),
        ),
        fetch_user_agent=os.environ.get(
            "EMBED_FETCH_USER_AGENT",
            "Mozilla/5.0 (compatible; cobd-embed/0.1; "
            "+https://embed.openapis.ca/)",
        ),
        cache_max_age_seconds=int(
            os.environ.get("EMBED_CACHE_MAX_AGE_SECONDS", "3600"),
        ),
        cache_slots=int(
            os.environ.get("EMBED_CACHE_SLOTS", "1024"),
        ),
    )
