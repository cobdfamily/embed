"""Render-template tests.

Pure: builds an EmbedPage and asserts the rendered
HTML contains the expected shape. Doesn't exercise
the whole FastAPI surface -- that's in test_app.
"""

from __future__ import annotations

from embed.render import (
    render_error,
    render_iframe,
    render_oembed,
    render_preview_card,
)


def test_render_oembed_inlines_provider_html() -> None:
    page = render_oembed(
        target_url="https://www.youtube.com/watch?v=abc",
        title="My video",
        provider_html='<iframe src="https://yt/x"></iframe>',
        provider_name="youtube",
    )
    assert "<!doctype html>" in page.html
    # provider HTML must pass through |safe, not be
    # escaped into &lt;iframe&gt;.
    assert "<iframe src=" in page.html
    assert "My video" in page.html
    assert page.source == "youtube"


def test_render_iframe_emits_plain_iframe() -> None:
    page = render_iframe(
        target_url="https://example.com/article",
        title="An article",
    )
    assert 'src="https://example.com/article"' in page.html
    assert "An article" in page.html
    assert page.source == "iframe"


def test_render_preview_card_emits_anchor_clickthrough() -> None:
    page = render_preview_card(
        target_url="https://example.com/post",
        title="A post",
        host="example.com",
    )
    assert 'href="https://example.com/post"' in page.html
    assert "A post" in page.html
    assert "example.com" in page.html
    # Preview card opens in the top window so the
    # user actually leaves the iframe.
    assert 'target="_top"' in page.html
    assert page.source == "preview"


def test_render_preview_card_escapes_user_content() -> None:
    """Title + host pass through Jinja autoescape;
    XSS in those fields shouldn't reach the browser
    as live HTML."""
    page = render_preview_card(
        target_url="https://example.com/",
        title="<script>alert('xss')</script>",
        host="example.com",
    )
    assert "<script>alert" not in page.html
    assert "&lt;script&gt;" in page.html


def test_render_error_marks_role_alert() -> None:
    page = render_error(message="bad input")
    assert 'role="alert"' in page.html
    assert "bad input" in page.html
    assert page.source == "error"
