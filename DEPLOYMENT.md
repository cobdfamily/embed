# Deployment

Operational notes for running `embed` at
`embed.openapis.ca`.

## Topology

```
            Internet
                |
                v
            Traefik (TLS + LetsEncrypt)
                |
                v
            embed (FastAPI, container)
                |
                v  HTTPS, outbound only
            oEmbed providers / target URLs
```

Single container. No database, no Redis in v0.1.
Horizontal-scale by adding replicas; cache state
is per-worker so the cache hit ratio at a given
size scales linearly with replica count rather
than aggregating.

## DNS

```
embed.openapis.ca   A    <traefik-public-ip>
embed.openapis.ca   AAAA <traefik-public-ipv6>
```

Same Traefik instance that fronts
`location.openapis.ca` (townsfolk).

## Build + tag

```sh
docker build \
  -t kibble.apps.blindhub.ca/cobdfamily/embed:0.1.0 \
  -t kibble.apps.blindhub.ca/cobdfamily/embed:latest \
  .
docker push kibble.apps.blindhub.ca/cobdfamily/embed:0.1.0
docker push kibble.apps.blindhub.ca/cobdfamily/embed:latest
```

The internal registry is `kibble.apps.blindhub.ca`,
same as every other cobdfamily service.

## Deploy

```sh
# on the embed host:
cd /srv/embed
git pull
EMBED_TAG=0.1.0 docker compose pull
EMBED_TAG=0.1.0 docker compose up -d
```

Compose mounts no volumes -- the service is
stateless. Restart is `unless-stopped`.

## Health checks

- `GET /healthz` (text/plain "ok") -- used by the
  Compose healthcheck and Traefik's loadbalancer.
- `GET /` (JSON liveness) -- includes the running
  version, useful for verifying a rolled-out
  upgrade.

The container's healthcheck hits `/healthz` every
5s with a 3s timeout. 12 retries with a 5s start
period covers slow boots without flapping the
container.

## Observability

- `X-Request-ID` echoed on every response. Caller-
  supplied ids are accepted (capped at 128
  chars); absent ids are minted as uuid4 hex.
  Pair with Traefik's access log to correlate.

- `X-Embed-Source` indicates which resolver path
  won. Operators can grep access logs for
  `X-Embed-Source: preview` to find pages where
  embeds are degrading to click-through cards --
  often a sign that a target site changed its
  XFO/CSP and needs an oEmbed registry entry.

## Outbound network

The service makes outbound HTTPS to:

- the oEmbed registry endpoints (youtube.com,
  vimeo.com, soundcloud.com, bsky.app, federated
  Mastodon instances, reddit.com, spotify.com)
- every target URL passed in `url=` (for the
  HEAD probe + title scrape paths)

If running behind a firewall, allowlist outbound
443/tcp to anywhere. We don't currently support
a SOCKS / HTTP forward-proxy -- if needed, set
`HTTPS_PROXY` in the container env and the
underlying httpx client picks it up.

## CSP for the service itself

The service emits HTML documents that are
deliberately permissive within their own origin
(provider scripts run inside them). A reasonable
CSP for `embed.openapis.ca` itself:

```
Content-Security-Policy:
  default-src 'self' https:;
  script-src  'self' https: 'unsafe-inline'
              'unsafe-eval';
  style-src   'self' https: 'unsafe-inline';
  img-src     'self' https: data:;
  font-src    'self' https: data:;
  frame-src   https:;
  connect-src 'self' https:;
  worker-src  'self' blob:;
```

Yes, `'unsafe-inline'` + `'unsafe-eval'` are
intentional here. Provider scripts (YouTube
widget API, Twitter embed.js) need them. The
isolation is at the origin boundary, not the CSP
-- the consumer page's CSP stays strict because
the iframe origin is different.

## Scaling

The bottleneck for any single replica is outbound
HTTP latency, not CPU. uvicorn workers ~= 4 x
vCPU. A 2-vCPU container easily handles
hundreds of req/sec because the LRU saturates
quickly on hot URLs.

Cold replicas (no cache) are slower for the
first N requests. Pre-warm by hitting `/v/embed`
with the top-N URLs as part of the deploy
script if cold-start latency matters.

## Rollback

`docker compose up -d` is idempotent. To roll
back, set `EMBED_TAG` to the prior version and
re-run. The image at `:latest` is moving; pin
explicitly in production.

## License

AGPL-3.0. The full text is in `LICENSE`.
