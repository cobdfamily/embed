"""Jinja2 templates for the wrapper HTML.

Three output shapes:

  - embed: oEmbed-driven (provider's HTML inlined)
  - iframe: plain `<iframe src="<target>">` when the
    target is frameable and oEmbed wasn't available
  - preview_card: title + favicon + click-through,
    used when the target refuses framing (XFO/CSP)
    or when oEmbed + probe both fail

Templates are loaded from the package's
`templates/` subdir and rendered with autoescape on
-- every variable that could carry untrusted text
(title, url, host) is escaped by Jinja by default.
The `html` field from an oEmbed response is the one
exception: providers return raw HTML on purpose, so
we splice it in with the `|safe` filter inside the
template. Acceptable because the rendered page
lives in a sandboxed origin -- any malicious script
hits embed.openapis.ca, not the consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


_TEMPLATES_DIR = Path(__file__).parent / "templates"


# Singleton environment. Templates are filesystem-
# loaded, autoescape on for .html, cached at the
# environment level so we're not re-reading files
# per request.
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "html.j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class EmbedPage:
    """Result of `render_oembed` / `render_iframe` /
    `render_preview_card`. The string content is a
    full HTML document; the caller sets the response
    Content-Type to text/html."""

    html: str
    # The provider source name (e.g. "youtube",
    # "iframe", "preview") for the Vary-friendly
    # X-Embed-Source header. Operators can grep
    # access logs by source without parsing the
    # body.
    source: str


def render_oembed(
    *,
    target_url: str,
    title: str,
    provider_html: str,
    provider_name: str,
) -> EmbedPage:
    """Wrap the provider's oEmbed `html` blob in a
    minimal page. The blob commonly contains its own
    `<iframe>` or `<script>` tag -- those run inside
    embed.openapis.ca's origin (sandboxed by the
    parent CSP), not the consumer's."""
    template = _env.get_template("embed.html.j2")
    rendered = template.render(
        target_url=target_url,
        title=title,
        provider_html=provider_html,
        provider_name=provider_name,
    )
    return EmbedPage(html=rendered, source=provider_name)


def render_iframe(
    *,
    target_url: str,
    title: str,
) -> EmbedPage:
    """Plain `<iframe src=target>` shim. Used when
    the target is frameable but doesn't have an
    oEmbed endpoint we know about. The outer iframe
    in the consumer page is what's sandboxed; this
    inner iframe inherits no additional sandbox
    restrictions but is loaded inside our origin
    so the consumer CSP is unaffected either way."""
    template = _env.get_template("embed.html.j2")
    rendered = template.render(
        target_url=target_url,
        title=title,
        provider_html=None,
        provider_name="iframe",
        plain_iframe_src=target_url,
    )
    return EmbedPage(html=rendered, source="iframe")


def render_preview_card(
    *,
    target_url: str,
    title: str,
    host: str,
) -> EmbedPage:
    """Preview-card fallback. Renders when:
      - oEmbed didn't match a provider, AND
      - the HEAD probe came back REFUSED or UNKNOWN.

    Shape: a click-through link with the title and
    the host. No iframe involved. Search-engine and
    screen-reader friendly because it's just an
    anchor."""
    template = _env.get_template("preview_card.html.j2")
    rendered = template.render(
        target_url=target_url,
        title=title,
        host=host,
    )
    return EmbedPage(html=rendered, source="preview")


def render_error(*, message: str) -> EmbedPage:
    """Rendered when the URL parameter is missing or
    malformed enough that we can't run the pipeline
    at all. Distinguished from preview-card so
    operators can spot bad caller behaviour in
    logs."""
    template = _env.get_template("error.html.j2")
    rendered = template.render(message=message)
    return EmbedPage(html=rendered, source="error")
