"""HEAD probe: can the target URL be loaded in an
iframe at all?

A non-trivial fraction of the web sends
`X-Frame-Options: DENY` or
`Content-Security-Policy: frame-ancestors 'none'`
to refuse framing entirely. If we silently emit
`<iframe src=...>` for those targets, the consumer
gets a blank embed with a console error and no
indication of what to click instead.

The probe runs a HEAD request, parses both headers,
and returns one of three verdicts:

  - FRAMEABLE: no refusal, safe to iframe directly
  - REFUSED:   XFO=DENY/SAMEORIGIN or CSP
               frame-ancestors disallows us; fall
               back to preview card
  - UNKNOWN:   probe failed (network, timeout) --
               err on the side of preview card so
               we never ship a blank embed

Notes on the CSP parse:

  We only inspect the `frame-ancestors` directive.
  Other directives don't affect whether WE can
  iframe the target. The directive value can be:

    'none'       -> REFUSED
    'self'       -> REFUSED (we're a different
                    origin from the target)
    list of URLs -> REFUSED if our origin isn't
                    listed (we never are; consumers
                    don't whitelist us per-site)
    *            -> FRAMEABLE
    (missing)    -> not present in CSP at all,
                    FRAMEABLE on this axis

  Multiple CSP headers exist in real-world traffic
  (proxy concatenation). Each is parsed and
  intersected -- a single REFUSED among them wins.
"""

from __future__ import annotations

import enum
import re

import httpx


class FrameVerdict(enum.Enum):
    FRAMEABLE = "frameable"
    REFUSED = "refused"
    UNKNOWN = "unknown"


# Splits a CSP header into individual directives.
# Each directive is its name + a space-separated
# token list, semicolon-terminated.
_CSP_DIRECTIVE_RE = re.compile(r"\s*;\s*")


def parse_xfo(header: str | None) -> FrameVerdict | None:
    """`X-Frame-Options` is one of DENY, SAMEORIGIN,
    or ALLOW-FROM <uri>. We treat the first two as
    refusal and ALLOW-FROM as refusal too -- the
    `uri` is checked against the embedding origin,
    and our origin (embed.openapis.ca) won't appear
    in practice.

    Returns None if the header is missing (so the
    CSP parse can still produce a verdict)."""
    if not header:
        return None
    value = header.strip().upper()
    if value in ("DENY", "SAMEORIGIN"):
        return FrameVerdict.REFUSED
    if value.startswith("ALLOW-FROM"):
        return FrameVerdict.REFUSED
    # Any other value (ALLOWALL is non-standard but
    # observed) we treat as FRAMEABLE.
    return FrameVerdict.FRAMEABLE


def parse_csp_frame_ancestors(
    headers: list[str],
) -> FrameVerdict | None:
    """Walks all CSP headers, returns the strictest
    verdict the frame-ancestors directive provides.
    Returns None when no header contains a
    frame-ancestors directive."""
    found_any = False
    refused = False
    frameable = False
    for header in headers:
        for directive in _CSP_DIRECTIVE_RE.split(header):
            directive = directive.strip()
            if not directive:
                continue
            parts = directive.split()
            if not parts:
                continue
            name = parts[0].lower()
            if name != "frame-ancestors":
                continue
            found_any = True
            sources = parts[1:]
            if not sources or "'none'" in sources:
                refused = True
                continue
            if "*" in sources:
                frameable = True
                continue
            # A list without * -- check if any of the
            # tokens names embed.openapis.ca. They
            # won't, but the check is cheap.
            if any(
                "embed.openapis.ca" in s for s in sources
            ):
                frameable = True
            else:
                refused = True
    if not found_any:
        return None
    # Refusal wins on intersection.
    if refused:
        return FrameVerdict.REFUSED
    return (
        FrameVerdict.FRAMEABLE if frameable else FrameVerdict.REFUSED
    )


async def probe(
    url: str,
    *,
    client: httpx.AsyncClient,
) -> FrameVerdict:
    """HEAD the target, combine XFO + CSP. Falls back
    to GET if the server 405s on HEAD (a real
    minority of sites). We never read the GET body
    -- closing the response after headers arrive is
    enough."""
    try:
        resp = await client.head(url, follow_redirects=True)
        if resp.status_code == 405:
            # Server refuses HEAD; fall back to GET.
            # Stream-mode so we don't pull the body.
            async with client.stream(
                "GET", url, follow_redirects=True,
            ) as stream_resp:
                xfo_hdr = stream_resp.headers.get("x-frame-options")
                csp_hdrs = stream_resp.headers.get_list(
                    "content-security-policy",
                )
        else:
            xfo_hdr = resp.headers.get("x-frame-options")
            csp_hdrs = resp.headers.get_list(
                "content-security-policy",
            )
    except httpx.HTTPError:
        return FrameVerdict.UNKNOWN

    xfo_verdict = parse_xfo(xfo_hdr)
    csp_verdict = parse_csp_frame_ancestors(csp_hdrs)

    # Intersection: REFUSED wins. If neither axis has
    # any verdict, the target ships no framing
    # headers -- the safe assumption is FRAMEABLE.
    if xfo_verdict == FrameVerdict.REFUSED:
        return FrameVerdict.REFUSED
    if csp_verdict == FrameVerdict.REFUSED:
        return FrameVerdict.REFUSED
    return FrameVerdict.FRAMEABLE
