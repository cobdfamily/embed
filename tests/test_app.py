"""FastAPI route tests via TestClient.

Uses respx to mock the outbound httpx calls so the
suite never touches the network. Covers:

  - liveness
  - 400 on bad url= input
  - oEmbed-driven response (YouTube)
  - frameable fallback (no provider, no refusal)
  - preview-card fallback (refusal)
  - cache reuse on a repeat call
  - response headers (Cache-Control, X-Embed-Source)
"""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient

from embed.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_liveness(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "embed"
    assert body["ok"] is True
    assert body["version"]


def test_healthz(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_embed_rejects_missing_url(client) -> None:
    r = client.get("/v/embed")
    # FastAPI's Query(...) declares the param
    # required; missing -> 422 from pydantic before
    # our handler runs.
    assert r.status_code == 422


def test_embed_rejects_bad_scheme(client) -> None:
    r = client.get(
        "/v/embed",
        params={"url": "javascript:alert(1)"},
    )
    assert r.status_code == 400
    assert "absolute http" in r.text.lower()


def test_embed_rejects_relative(client) -> None:
    r = client.get("/v/embed", params={"url": "/foo"})
    assert r.status_code == 400


@respx.mock
def test_embed_oembed_path_youtube(client) -> None:
    """YouTube URL -> oEmbed path. We splice the
    provider's html and ship a 200 with
    X-Embed-Source: youtube."""
    respx.get("https://www.youtube.com/oembed").respond(
        200,
        json={
            "html": (
                '<iframe '
                'src="https://www.youtube.com/embed/abc" '
                'allowfullscreen></iframe>'
            ),
            "title": "Sample video",
            "width": 480,
            "height": 270,
        },
    )
    r = client.get(
        "/v/embed",
        params={
            "url": "https://www.youtube.com/watch?v=abc",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Sample video" in r.text
    assert "iframe" in r.text
    assert r.headers.get("x-embed-source") == "youtube"
    assert "max-age=" in r.headers.get("cache-control", "")
    assert r.headers.get("referrer-policy") == "no-referrer"


@respx.mock
def test_embed_iframe_fallback_when_frameable(client) -> None:
    """Unknown provider + no refusal headers -> plain
    iframe shim."""
    respx.head("https://example.com/article").respond(
        200, headers={},
    )
    respx.get("https://example.com/article").respond(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Hello</title></head></html>",
    )
    r = client.get(
        "/v/embed",
        params={"url": "https://example.com/article"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-embed-source") == "iframe"
    assert 'src="https://example.com/article"' in r.text
    assert "Hello" in r.text


@respx.mock
def test_embed_preview_card_when_refused(client) -> None:
    """XFO=DENY -> preview-card fallback. Body has the
    title (scraped) + the host."""
    respx.head("https://locked.example/x").respond(
        200, headers={"X-Frame-Options": "DENY"},
    )
    respx.get("https://locked.example/x").respond(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Locked Page</title></head></html>",
    )
    r = client.get(
        "/v/embed",
        params={"url": "https://locked.example/x"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-embed-source") == "preview"
    assert "Locked Page" in r.text
    assert "locked.example" in r.text
    # Click-through anchor escapes to _top.
    assert 'target="_top"' in r.text


@respx.mock
def test_embed_cache_returns_same_body_on_repeat(client) -> None:
    """Second call to the same URL is served from the
    LRU -- mock asserts respx was hit exactly once."""
    route = respx.get(
        "https://www.youtube.com/oembed",
    ).respond(
        200,
        json={
            "html": "<iframe></iframe>",
            "title": "T",
        },
    )
    url = "https://www.youtube.com/watch?v=cache-test"
    r1 = client.get("/v/embed", params={"url": url})
    r2 = client.get("/v/embed", params={"url": url})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.text == r2.text
    assert route.call_count == 1


@respx.mock
def test_embed_request_id_echoed(client) -> None:
    """X-Request-ID middleware echoes the caller's
    id on the response when supplied."""
    # No provider match -> probe + scrape, so mock
    # both. The id is echoed regardless of path.
    respx.head("https://example.com/").respond(200)
    respx.get("https://example.com/").respond(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Ex</title></head></html>",
    )
    r = client.get(
        "/v/embed",
        params={"url": "https://example.com/"},
        headers={"X-Request-ID": "test-id-123"},
    )
    assert r.headers.get("x-request-id") == "test-id-123"


@respx.mock
def test_embed_request_id_minted_when_absent(client) -> None:
    respx.head("https://example.org/").respond(200)
    respx.get("https://example.org/").respond(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Ex</title></head></html>",
    )
    r = client.get(
        "/v/embed",
        params={"url": "https://example.org/"},
    )
    assert r.headers.get("x-request-id")
    # 32 hex chars (uuid4 hex form).
    assert len(r.headers["x-request-id"]) == 32
