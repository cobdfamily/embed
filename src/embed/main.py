"""embed FastAPI app.

  GET  /                       liveness
  GET  /v1/embed?url=<target>  HTML wrapper response

The wrapper resolves through three paths in order:

  1. oEmbed provider match (PROVIDERS in oembed.py)
     -> splice provider HTML into embed.html.j2
  2. else, HEAD-probe the target for XFO / CSP
     frame-ancestors. If frameable -> plain
     `<iframe src=target>` in embed.html.j2
  3. else -> preview card with title scrape +
     click-through

Why the wrapper at all: the consumer page loads
THIS response inside a sandboxed iframe. Whatever
scripts the provider's `html` blob includes
(YouTube IFrame API, Twitter widgets.js, etc.) run
inside embed.openapis.ca's origin and are subject
to OUR CSP, not the consumer's. The consumer can
ship a strict CSP and not whitelist
googlevideo.com, abs.twimg.com, etc.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import __version__
from .config import Config, load
from .frame_probe import FrameVerdict, probe
from .middleware import RequestIdMiddleware
from .oembed import (
    OEmbedResult,
    fetch_oembed,
    lookup_provider,
)
from .render import (
    render_error,
    render_iframe,
    render_oembed,
    render_preview_card,
)


logger = logging.getLogger("embed")


# In-process LRU keyed by target URL. Holds rendered
# HTML strings + their source label. Bounded by
# `cfg.cache_slots`; oldest entry evicts on
# overflow. Single process, no Redis dependency in
# v0.1; for horizontal-scale deploys an operator
# can either accept N x duplicate cache state or
# wire Redis in v0.2.
class _Lru:
    def __init__(self, max_slots: int) -> None:
        self._slots: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._max = max_slots

    def get(self, key: str) -> tuple[str, str] | None:
        entry = self._slots.get(key)
        if entry is None:
            return None
        # Touch -> move to MRU end.
        self._slots.move_to_end(key)
        return entry

    def put(self, key: str, value: tuple[str, str]) -> None:
        if key in self._slots:
            self._slots.move_to_end(key)
        self._slots[key] = value
        while len(self._slots) > self._max:
            self._slots.popitem(last=False)

    def __len__(self) -> int:
        return len(self._slots)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bring up the httpx client + LRU cache. The
    client is shared across requests; httpx keeps
    its own connection pool internally."""
    cfg: Config = load()
    app.state.config = cfg
    app.state.cache = _Lru(cfg.cache_slots)
    app.state.client = httpx.AsyncClient(
        timeout=cfg.fetch_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": cfg.fetch_user_agent},
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(
    title="embed",
    version=__version__,
    description=(
        "oEmbed / iframe-shim service for cobd-embed. "
        "Returns a CSP-isolated HTML wrapper at "
        "/v/embed so consumer pages can stay CSP-strict."
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)


@app.get("/", tags=["Liveness"])
async def liveness() -> dict:
    return {
        "ok": True,
        "service": "embed",
        "version": __version__,
    }


@app.get(
    "/v1/embed",
    tags=["Embed"],
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}},
        400: {"content": {"text/html": {}}},
    },
)
async def embed(
    request: Request,
    url: str = Query(
        ...,
        description=(
            "Target URL to embed. Must be http or https."
        ),
    ),
) -> HTMLResponse:
    """Return a self-contained HTML doc the consumer
    iframes. See module docstring for the three-path
    resolution."""
    cfg: Config = request.app.state.config

    if not _is_acceptable_url(url):
        page = render_error(
            message=(
                "url parameter must be an absolute "
                "http or https URL."
            ),
        )
        return HTMLResponse(content=page.html, status_code=400)

    # Cache key is the canonical URL. We don't strip
    # query strings -- oembed can return different
    # responses for the same path with different
    # query params (e.g. YouTube `?t=120s`).
    cache: _Lru = request.app.state.cache
    cached = cache.get(url)
    if cached is not None:
        body, source = cached
        return _ok_html(body, source, cfg)

    client: httpx.AsyncClient = request.app.state.client
    page_html, source = await _resolve(url, cfg=cfg, client=client)
    cache.put(url, (page_html, source))
    return _ok_html(page_html, source, cfg)


def _is_acceptable_url(value: str) -> bool:
    """Reject anything that isn't http/https with a
    hostname. Defence against `file://`,
    `javascript:`, and relative-path inputs."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _ok_html(
    body: str, source: str, cfg: Config,
) -> HTMLResponse:
    """Apply uniform response headers. Cache-Control
    public so a CDN in front can hold the response;
    Referrer-Policy no-referrer so we never leak a
    user's COBD session in our outbound provider
    fetches OR in the consumer's response chain."""
    return HTMLResponse(
        content=body,
        status_code=200,
        headers={
            "Cache-Control": (
                f"public, max-age={cfg.cache_max_age_seconds}"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Embed-Source": source,
        },
    )


async def _resolve(
    url: str,
    *,
    cfg: Config,
    client: httpx.AsyncClient,
) -> tuple[str, str]:
    """Three-stage resolver. Returns (html, source)."""

    # Stage 1: oEmbed.
    provider = lookup_provider(url)
    if provider is not None:
        result: OEmbedResult | None = await fetch_oembed(
            provider, url, client=client,
        )
        if result is not None:
            page = render_oembed(
                target_url=url,
                title=result.title or _host_of(url),
                provider_html=result.html,
                provider_name=result.provider_name,
            )
            return page.html, page.source

    # Stage 2: HEAD probe.
    verdict = await probe(url, client=client)
    if verdict == FrameVerdict.FRAMEABLE:
        # No oEmbed match, but the target lets us
        # iframe it directly. Title-scrape so the
        # outer-iframe label is honest.
        title = await _scrape_title(url, cfg=cfg, client=client)
        page = render_iframe(
            target_url=url,
            title=title or _host_of(url),
        )
        return page.html, page.source

    # Stage 3: preview card. Always reachable when
    # nothing else worked; never returns blank.
    title = await _scrape_title(url, cfg=cfg, client=client)
    page = render_preview_card(
        target_url=url,
        title=title or _host_of(url),
        host=_host_of(url),
    )
    return page.html, page.source


def _host_of(url: str) -> str:
    """Hostname for the preview-card subtitle and as
    the title fallback. urlparse never raises on
    str input."""
    return urlparse(url).hostname or url


class _TitleParser(HTMLParser):
    """Pulls `<title>` text out of an HTML stream.
    Stops at the first closing tag so we don't keep
    reading past `</title>`. Defensive: handles a
    missing close-tag by just keeping whatever it
    saw."""

    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._chunks: list[str] = []
        self.done = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.done = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._chunks.append(data)

    def title(self) -> str | None:
        if not self._chunks:
            return None
        joined = "".join(self._chunks).strip()
        return joined or None


async def _scrape_title(
    url: str,
    *,
    cfg: Config,
    client: httpx.AsyncClient,
) -> str | None:
    """Stream the target URL, parse out `<title>`,
    stop early. Capped at `fetch_byte_cap` bytes so
    a hostile target can't bloat us via a never-
    ending response."""
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                return None
            parser = _TitleParser()
            total = 0
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                total += len(chunk)
                # Decode as latin-1 -- best-effort,
                # cheap, never raises on bytes; title
                # text is ASCII in practice. Wrong
                # encoding here just means a few
                # mojibake characters in the preview
                # card, not a server error.
                parser.feed(chunk.decode("latin-1", errors="replace"))
                if parser.done or total >= cfg.fetch_byte_cap:
                    break
            return parser.title()
    except httpx.HTTPError:
        return None


@app.get(
    "/healthz",
    tags=["Liveness"],
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def healthz() -> str:
    """Kubernetes-style liveness path. Same body
    shape as Traefik's expected probe -- 200 + small
    text. Operators that prefer a deeper health
    contract can layer on /v/health later."""
    return "ok"


def run() -> None:
    """Entry point for the `embed` console script.
    Mirrors `uvicorn embed.main:app`."""
    import uvicorn

    uvicorn.run(
        "embed.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
