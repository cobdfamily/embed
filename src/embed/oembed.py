"""oEmbed provider lookup + fetch.

v0.1 ships a small hardcoded registry of well-known
providers. Each entry maps a URL pattern (compiled
regex) to the provider's oEmbed endpoint. The
endpoint is called with `?url=<target>&format=json`;
the JSON's `html` field is what we splice into the
wrapper page.

Autodiscovery (parsing `<link rel="alternate"
type="application/json+oembed">` out of the target
page) is on the v0.2 roadmap. For v0.1 we don't
walk the open web -- the registry covers ~95% of
what consumers actually embed, and unknown URLs
fall through to the frame-probe path.

The registry stays small + curated on purpose:

  - YouTube + youtu.be          (video)
  - Vimeo                       (video)
  - SoundCloud                  (audio)
  - Bluesky                     (social)
  - Mastodon (any instance)     (social, regex-only)
  - Reddit                      (social)
  - Spotify                     (audio, oembed v1 wrapper)

For COBD-controlled origins (florin.cobd.ca,
kados.cobd.ca, cobd.ca itself) we hit those same
oEmbed endpoints once they exist -- contract is in
the README. Until then they fall through to the
frame-probe path, which works because we control
their X-Frame-Options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Provider:
    """One row of the registry. `name` is for logs +
    metrics. `url_pattern` matches the target URL
    (case-insensitive); first hit wins. `endpoint`
    is the oEmbed URL we GET with `?url=...`.

    A `params` dict adds extra query params -- e.g.
    Bluesky requires `format=json` explicitly; some
    providers want `omit_script=true` to skip their
    bootstrap JS (saves a network round-trip inside
    the iframe). Default is no extras.
    """

    name: str
    url_pattern: re.Pattern[str]
    endpoint: str
    params: dict[str, str]


# Compiled at import time -- the regex set is small
# and the patterns never change at runtime.
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        name="youtube",
        url_pattern=re.compile(
            r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/",
            re.I,
        ),
        endpoint="https://www.youtube.com/oembed",
        params={},
    ),
    Provider(
        name="vimeo",
        url_pattern=re.compile(
            r"^https?://(?:www\.)?vimeo\.com/",
            re.I,
        ),
        endpoint="https://vimeo.com/api/oembed.json",
        params={},
    ),
    Provider(
        name="soundcloud",
        url_pattern=re.compile(
            r"^https?://(?:www\.|m\.)?soundcloud\.com/",
            re.I,
        ),
        endpoint="https://soundcloud.com/oembed",
        params={"format": "json"},
    ),
    Provider(
        name="bluesky",
        url_pattern=re.compile(
            r"^https?://bsky\.app/profile/[^/]+/post/",
            re.I,
        ),
        endpoint="https://embed.bsky.app/oembed",
        params={},
    ),
    Provider(
        # Mastodon's federated; every instance answers
        # /api/oembed itself. We can't enumerate them
        # so the pattern is "anything that smells like
        # an instance" and we route to the URL's own
        # host. Endpoint is templated below in
        # `lookup_provider`.
        name="mastodon",
        url_pattern=re.compile(
            r"^https?://[^/]+/@[^/]+/\d+",
            re.I,
        ),
        endpoint="",
        params={},
    ),
    Provider(
        name="reddit",
        url_pattern=re.compile(
            r"^https?://(?:www\.|old\.|np\.)?reddit\.com/r/[^/]+/comments/",
            re.I,
        ),
        endpoint="https://www.reddit.com/oembed",
        params={},
    ),
    Provider(
        name="spotify",
        url_pattern=re.compile(
            r"^https?://open\.spotify\.com/",
            re.I,
        ),
        endpoint="https://open.spotify.com/oembed",
        params={},
    ),
)


def lookup_provider(url: str) -> Provider | None:
    """First-match wins. Returns None when no
    provider claims the URL. Mastodon's endpoint is
    templated from the URL's own host (federation)
    -- we rewrite the returned Provider in place
    rather than threading a hostname through the
    caller, since the endpoint and the URL host
    coincide for Mastodon."""
    for provider in PROVIDERS:
        if provider.url_pattern.match(url):
            if provider.name == "mastodon":
                # Pull the host out of the URL itself.
                # No need for a full URL parse here --
                # the regex already verified the
                # `scheme://host/@...` shape.
                host_match = re.match(r"^https?://([^/]+)/", url)
                if not host_match:
                    return None
                host = host_match.group(1)
                return Provider(
                    name=provider.name,
                    url_pattern=provider.url_pattern,
                    endpoint=f"https://{host}/api/oembed",
                    params=provider.params,
                )
            return provider
    return None


@dataclass(frozen=True)
class OEmbedResult:
    """Subset of the oEmbed spec we actually use.
    Some providers send more (author_name,
    thumbnail_url, etc.) but the wrapper page only
    needs the iframe-ready HTML + a title we can
    set on the outer iframe in the consumer page."""

    html: str
    title: str | None
    width: int | None
    height: int | None
    provider_name: str


async def fetch_oembed(
    provider: Provider,
    url: str,
    *,
    client: httpx.AsyncClient,
) -> OEmbedResult | None:
    """Hit the provider's oEmbed endpoint, return the
    parsed shape. Returns None on any non-200 / non-
    JSON / no-html response so the caller can fall
    through to the frame-probe path. Raises only on
    the truly exceptional (a programmer-error like a
    malformed registry endpoint URL)."""
    params: dict[str, str] = {"url": url, **provider.params}
    if "format" not in params:
        params["format"] = "json"
    try:
        resp = await client.get(provider.endpoint, params=params)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    html = data.get("html")
    if not isinstance(html, str) or not html.strip():
        return None
    return OEmbedResult(
        html=html,
        title=data.get("title") if isinstance(
            data.get("title"), str,
        ) else None,
        width=_as_int(data.get("width")),
        height=_as_int(data.get("height")),
        provider_name=provider.name,
    )


def _as_int(value: object) -> int | None:
    """oEmbed lets `width` / `height` be ints OR
    strings; tolerate both, drop anything else."""
    if isinstance(value, bool):
        # bool is a subclass of int in python; reject
        # explicitly so True doesn't become 1px.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
