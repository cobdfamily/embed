# embed

[![license](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

oEmbed / iframe-shim service for `<cobd-embed>`.
Lives at `https://embed.openapis.ca/v/embed?url=...`.
One endpoint, one envelope: returns a self-contained
HTML document the consumer page loads in a sandboxed
iframe.

## Why this exists

`<cobd-embed>` is a custom element in
`@cobdfamily/clf-core` that progressively enhances
an anchor:

```html
<cobd-embed>
  <a href="https://www.youtube.com/watch?v=abc">
    Demo of the new tactile interface
  </a>
</cobd-embed>
```

upgrades to:

```html
<iframe src="https://embed.openapis.ca/v/embed?url=..."
        sandbox="allow-scripts allow-popups
                 allow-popups-to-escape-sandbox"
        loading="lazy"></iframe>
```

The whole reason for the proxy is **CSP isolation**.
Provider embeds (YouTube IFrame API, Twitter
widgets.js, etc.) ship inline scripts and load
third-party resources. If we inline them on
blindhub.ca directly, consumer CSP needs holes for
every provider. Instead, we wrap the embed in a
document at `embed.openapis.ca`, the consumer
iframes THAT, and the provider scripts hit our CSP
(which can be permissive within its own origin). The
consumer page stays CSP-strict.

Double-iframe sometimes (when oEmbed itself returns
an iframe), but the cost is one extra HTTP round-
trip + parse of a tiny doc, paid lazily at scroll
time. The CSP win is worth it.

## What it does

```
GET  /                  liveness JSON
GET  /healthz           liveness text
GET  /v/embed?url=...   HTML wrapper response
```

The `/v/embed` resolver runs in three stages:

1. **oEmbed.** If the URL matches a known provider
   (YouTube, Vimeo, SoundCloud, Bluesky, Mastodon,
   Reddit, Spotify), call the provider's oEmbed
   endpoint and splice its `html` field into a
   wrapper page.
2. **Iframe shim.** If no oEmbed match, HEAD-probe
   the target for `X-Frame-Options` / CSP
   `frame-ancestors`. If the target lets us iframe
   it, emit `<iframe src=target>` inside the
   wrapper.
3. **Preview card.** If the target refuses framing
   or the probe fails, render a click-through card
   with the page's `<title>` + host. Real anchor,
   no script -- keyboard + screen-reader friendly
   by default.

Always returns 200 + HTML except for malformed
input (400) or missing `url=` (422 from FastAPI's
required-Query validator).

## Response headers

```
Content-Type:        text/html; charset=utf-8
Cache-Control:       public, max-age=3600
Referrer-Policy:     no-referrer
X-Embed-Source:      youtube | vimeo | soundcloud |
                     bluesky | mastodon | reddit |
                     spotify | iframe | preview |
                     error
X-Request-ID:        echoed if supplied, else minted
```

Operators can grep access logs by `X-Embed-Source`
to see how the resolver is making decisions in
production.

## Run locally

```sh
uv sync
uv run pytest                       # unit + app tests
uv run uvicorn embed.main:app --reload
curl 'http://localhost:8000/v/embed?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ' | head
```

Docker:

```sh
docker compose up -d
curl http://localhost:8004/healthz
```

## Configuration

Every knob is an `EMBED_*` env var. Defaults work
for most deployments; see `src/embed/config.py` for
the type-checked shape.

| Var                            | Default               |
|--------------------------------|-----------------------|
| `EMBED_FETCH_BYTE_CAP`         | `262144` (256 KiB)    |
| `EMBED_FETCH_TIMEOUT_SECONDS`  | `5`                   |
| `EMBED_FETCH_USER_AGENT`       | browser-shaped string |
| `EMBED_CACHE_MAX_AGE_SECONDS`  | `3600` (1h)           |
| `EMBED_CACHE_SLOTS`            | `1024`                |

## Provider contract for COBD origins

COBD-controlled origins (florin.cobd.ca,
kados.cobd.ca, cobd.ca itself) can expose an oEmbed
endpoint to short-circuit the iframe path. The
contract is the standard
[oEmbed 1.0](https://oembed.com/) JSON shape:

```
GET https://<host>/oembed?url=<absolute>&format=json
->
{
  "type":     "rich",
  "version":  "1.0",
  "title":    "Page title",
  "html":     "<iframe ...> or self-contained HTML",
  "width":    640,
  "height":   360
}
```

Once a COBD origin starts emitting that, add a
`Provider` entry to `src/embed/oembed.py` and bump
the patch. Until then, those origins fall through
to the iframe-shim path, which works because we
control their `X-Frame-Options`.

## Architecture

```
       browser
          |
          v
   consumer page (CSP-strict)
          |
          v  <iframe src=embed.openapis.ca/v/embed?url=...>
          |
   Traefik -> embed (FastAPI, this service)
                  |
                  v  HTTPS
              oEmbed providers / target URLs
```

Stateless. One in-process LRU. No database, no
Redis. Horizontal-scale by adding replicas; cache
state is per-worker.

## Status

v0.1.0. The resolver paths and registry have
unit + app coverage. Provider autodiscovery
(parsing `<link rel="alternate"
type="application/json+oembed">` out of arbitrary
pages) is v0.2 work. CSP `frame-ancestors`
intersection is implemented; multi-CSP-header
edge cases tested.

## Licence

AGPL-3.0. See [LICENSE](./LICENSE).
