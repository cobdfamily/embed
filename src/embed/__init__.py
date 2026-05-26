"""embed -- HTML wrapper service for cobd-embed.

Sits at embed.openapis.ca/v1/embed?url=<target>. Returns
a self-contained HTML document the consumer page loads
in a sandboxed iframe, so any provider scripts (YouTube
widgets, Twitter embed.js, etc.) run inside this
origin and never touch the consumer's CSP.

The element it pairs with is `<cobd-embed>` in
`@cobdfamily/clf-core/components/cobd-embed`.
"""

__version__ = "0.1.1"
