# Changelog

All notable changes to `embed` are documented here.

The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow
[Semantic Versioning](https://semver.org/). The
service is pre-1.0, so the patch part of the
version moves freely for any change; major / minor
bumps are reserved for after 1.0.

## [0.1.1] -- 2026-05-26

### Fixed

- **Route is `/v1/embed`, not `/v/embed`.** The
  0.1.0 release used `/v/embed`; this was a
  misread of the openapis.ca naming convention
  (sibling services like `location.openapis.ca/v1/
  lookup` use `/v1/`). Renamed across the
  service, the test suite, the docs, and the
  paired `<cobd-embed>` element default. clf-core
  7.0.1 ships the matching element-default fix.

  No backwards-compat shim: 0.1.0 was registry-
  only, never deployed at `embed.openapis.ca`.

## [0.1.0] -- 2026-05-26

### Added

- **Initial release.** FastAPI service at
  `embed.openapis.ca/v1/embed?url=<target>`.

- **Three-stage resolver.** Tries the oEmbed
  provider registry first (YouTube, Vimeo,
  SoundCloud, Bluesky, Mastodon, Reddit,
  Spotify); falls back to a HEAD-probe that
  parses `X-Frame-Options` and CSP
  `frame-ancestors`; falls back further to a
  preview card with title scrape + click-
  through. Always returns 200 + HTML except for
  malformed `url=` input (400).

- **Per-process LRU cache.** Bounded by
  `EMBED_CACHE_SLOTS` (default 1024). Cache
  state is per-worker; horizontal-scale deploys
  accept N x duplicate cache contents until v0.2
  introduces a shared backend.

- **Response headers.** `Cache-Control: public,
  max-age=3600`, `Referrer-Policy: no-referrer`,
  `X-Embed-Source: <source>`, plus the standard
  `X-Request-ID` echo / mint via middleware
  (same shape as townsfolk).

- **CSP isolation.** Whatever scripts a
  provider's oEmbed `html` blob carries (YouTube
  IFrame API, Twitter widgets.js, etc.) run
  inside `embed.openapis.ca`'s origin -- the
  consumer page can ship a strict CSP that
  doesn't whitelist provider origins.

- **Docker + Traefik.** Two-stage uv build,
  `python:3.12-slim` runtime, Traefik labels for
  `embed.openapis.ca`.

### Pairs with

- `@cobdfamily/clf-core@6.2.0` -- the new
  `<cobd-embed>` custom element. Authoring shape
  is just `<cobd-embed><a href="...">title</a>
  </cobd-embed>`; the element upgrades to a
  sandboxed iframe pointing at this service.
