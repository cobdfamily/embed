"""Provider lookup + fetch tests.

Provider lookup is pure regex over the registry --
no I/O, no httpx needed. Fetch uses respx to mock
the provider endpoints in-process, so the suite is
hermetic and survives `uv run pytest` with no
network.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from embed.oembed import fetch_oembed, lookup_provider


# ---------------------------------------------------
# lookup_provider -- pure
# ---------------------------------------------------


@pytest.mark.parametrize(
    "url, expected_provider",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("http://m.youtube.com/watch?v=abc", "youtube"),
        ("https://vimeo.com/123456789", "vimeo"),
        ("https://soundcloud.com/artist/track", "soundcloud"),
        (
            "https://bsky.app/profile/cobd.bsky.social/post/abc",
            "bluesky",
        ),
        (
            "https://mastodon.social/@user/123456789012345678",
            "mastodon",
        ),
        (
            "https://reddit.com/r/python/comments/abc/title/",
            "reddit",
        ),
        (
            "https://www.reddit.com/r/python/comments/abc/title/",
            "reddit",
        ),
        ("https://open.spotify.com/track/abc", "spotify"),
    ],
)
def test_lookup_known_providers(
    url: str, expected_provider: str,
) -> None:
    provider = lookup_provider(url)
    assert provider is not None
    assert provider.name == expected_provider


def test_lookup_unknown_provider_returns_none() -> None:
    assert lookup_provider("https://example.com/page") is None


def test_lookup_rejects_non_http() -> None:
    # The registry patterns all start `^https?://`;
    # other schemes shouldn't match.
    assert lookup_provider("file:///etc/passwd") is None
    assert lookup_provider("javascript:alert(1)") is None


def test_mastodon_endpoint_is_per_instance() -> None:
    """Mastodon is federated -- the endpoint must
    match the URL's own host, not a hardcoded
    instance."""
    p1 = lookup_provider(
        "https://mastodon.social/@u/1",
    )
    p2 = lookup_provider(
        "https://hachyderm.io/@u/1",
    )
    assert p1 is not None and p2 is not None
    assert p1.endpoint == "https://mastodon.social/api/oembed"
    assert p2.endpoint == "https://hachyderm.io/api/oembed"


# ---------------------------------------------------
# fetch_oembed -- mocked
# ---------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_oembed_returns_html_on_200() -> None:
    provider = lookup_provider(
        "https://www.youtube.com/watch?v=abc",
    )
    assert provider is not None

    respx.get("https://www.youtube.com/oembed").respond(
        200,
        json={
            "html": "<iframe src='https://yt.example/x'></iframe>",
            "title": "Example Video",
            "width": 480,
            "height": 270,
        },
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_oembed(
            provider,
            "https://www.youtube.com/watch?v=abc",
            client=client,
        )
    assert result is not None
    assert "<iframe" in result.html
    assert result.title == "Example Video"
    assert result.width == 480
    assert result.height == 270
    assert result.provider_name == "youtube"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_oembed_returns_none_on_404() -> None:
    provider = lookup_provider(
        "https://www.youtube.com/watch?v=abc",
    )
    assert provider is not None
    respx.get("https://www.youtube.com/oembed").respond(404)
    async with httpx.AsyncClient() as client:
        result = await fetch_oembed(
            provider,
            "https://www.youtube.com/watch?v=abc",
            client=client,
        )
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_oembed_returns_none_on_no_html() -> None:
    """A 200 with no `html` field is treated as a
    miss -- some providers return error envelopes
    inside a 200."""
    provider = lookup_provider("https://vimeo.com/1")
    assert provider is not None
    respx.get("https://vimeo.com/api/oembed.json").respond(
        200, json={"title": "missing html"},
    )
    async with httpx.AsyncClient() as client:
        result = await fetch_oembed(
            provider,
            "https://vimeo.com/1",
            client=client,
        )
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_oembed_returns_none_on_network_error() -> None:
    provider = lookup_provider("https://vimeo.com/1")
    assert provider is not None
    respx.get("https://vimeo.com/api/oembed.json").mock(
        side_effect=httpx.ConnectError("boom"),
    )
    async with httpx.AsyncClient() as client:
        result = await fetch_oembed(
            provider,
            "https://vimeo.com/1",
            client=client,
        )
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_oembed_tolerates_string_dimensions() -> None:
    """Some providers return width/height as strings
    rather than ints. Both must parse."""
    provider = lookup_provider("https://vimeo.com/1")
    assert provider is not None
    respx.get("https://vimeo.com/api/oembed.json").respond(
        200,
        json={
            "html": "<iframe></iframe>",
            "width": "640",
            "height": "360",
        },
    )
    async with httpx.AsyncClient() as client:
        result = await fetch_oembed(
            provider,
            "https://vimeo.com/1",
            client=client,
        )
    assert result is not None
    assert result.width == 640
    assert result.height == 360
