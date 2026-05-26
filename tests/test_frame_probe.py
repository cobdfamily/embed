"""HEAD-probe + header-parser tests.

The parser tests are pure (no I/O). The `probe()`
tests use respx to stub the target's HEAD response.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from embed.frame_probe import (
    FrameVerdict,
    parse_csp_frame_ancestors,
    parse_xfo,
    probe,
)


# ---------------------------------------------------
# parse_xfo -- pure
# ---------------------------------------------------


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, None),
        ("", None),
        ("DENY", FrameVerdict.REFUSED),
        ("deny", FrameVerdict.REFUSED),
        ("SAMEORIGIN", FrameVerdict.REFUSED),
        ("ALLOW-FROM https://other.example", FrameVerdict.REFUSED),
        ("ALLOWALL", FrameVerdict.FRAMEABLE),
    ],
)
def test_parse_xfo(
    header: str | None, expected: FrameVerdict | None,
) -> None:
    assert parse_xfo(header) == expected


# ---------------------------------------------------
# parse_csp_frame_ancestors -- pure
# ---------------------------------------------------


def test_csp_no_frame_ancestors_returns_none() -> None:
    headers = ["default-src 'self'; script-src 'self'"]
    assert parse_csp_frame_ancestors(headers) is None


def test_csp_frame_ancestors_none_refuses() -> None:
    headers = ["frame-ancestors 'none'"]
    assert parse_csp_frame_ancestors(headers) == FrameVerdict.REFUSED


def test_csp_frame_ancestors_self_refuses() -> None:
    """`'self'` lets the target frame itself, not us."""
    headers = ["frame-ancestors 'self'"]
    assert parse_csp_frame_ancestors(headers) == FrameVerdict.REFUSED


def test_csp_frame_ancestors_star_allows() -> None:
    headers = ["frame-ancestors *"]
    assert parse_csp_frame_ancestors(headers) == FrameVerdict.FRAMEABLE


def test_csp_explicit_embed_origin_allows() -> None:
    """A target that whitelists embed.openapis.ca
    (theoretical -- consumers don't usually do this,
    but COBD-controlled origins might once we wire
    them up) should be FRAMEABLE."""
    headers = [
        "frame-ancestors https://embed.openapis.ca",
    ]
    verdict = parse_csp_frame_ancestors(headers)
    assert verdict == FrameVerdict.FRAMEABLE


def test_csp_multiple_headers_intersect_to_refused() -> None:
    """When two CSP headers are present (proxy
    concatenation), a single REFUSED wins."""
    headers = [
        "frame-ancestors *",
        "frame-ancestors 'none'",
    ]
    assert parse_csp_frame_ancestors(headers) == FrameVerdict.REFUSED


def test_csp_other_directives_ignored() -> None:
    """Only frame-ancestors affects our verdict."""
    headers = [
        "default-src 'self'; script-src 'none'; "
        "frame-ancestors *",
    ]
    assert parse_csp_frame_ancestors(headers) == FrameVerdict.FRAMEABLE


# ---------------------------------------------------
# probe() -- I/O
# ---------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_probe_frameable_no_refusal_headers() -> None:
    respx.head("https://safe.example/page").respond(200, headers={})
    async with httpx.AsyncClient() as client:
        verdict = await probe(
            "https://safe.example/page", client=client,
        )
    assert verdict == FrameVerdict.FRAMEABLE


@pytest.mark.asyncio
@respx.mock
async def test_probe_refused_by_xfo() -> None:
    respx.head("https://locked.example/").respond(
        200, headers={"X-Frame-Options": "DENY"},
    )
    async with httpx.AsyncClient() as client:
        verdict = await probe(
            "https://locked.example/", client=client,
        )
    assert verdict == FrameVerdict.REFUSED


@pytest.mark.asyncio
@respx.mock
async def test_probe_refused_by_csp() -> None:
    respx.head("https://csp.example/").respond(
        200,
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; frame-ancestors 'none'"
            ),
        },
    )
    async with httpx.AsyncClient() as client:
        verdict = await probe(
            "https://csp.example/", client=client,
        )
    assert verdict == FrameVerdict.REFUSED


@pytest.mark.asyncio
@respx.mock
async def test_probe_unknown_on_network_error() -> None:
    respx.head("https://gone.example/").mock(
        side_effect=httpx.ConnectError("boom"),
    )
    async with httpx.AsyncClient() as client:
        verdict = await probe(
            "https://gone.example/", client=client,
        )
    assert verdict == FrameVerdict.UNKNOWN
