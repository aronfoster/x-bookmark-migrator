"""Shared exact saved-post identity. No provider calls or credential handling."""

import re
from urllib.parse import urlsplit

from .core import PilotError, exact_id

X_HOSTS = frozenset(
    prefix + host for host in ("x.com", "twitter.com") for prefix in ("", "www.", "m.", "mobile.")
)
STATUS_PATH = re.compile(
    r"/(?:[A-Za-z0-9_]+/status|i/web/status)/([0-9]{1,19})(?:/(?:photo|video)/[1-9][0-9]*)?/?"
)


def post_id(value):
    """Preserve decimal strings exactly, including IDs beyond JavaScript precision."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        return exact_id(value)
    if isinstance(value, str) and not any(c.isspace() for c in value):
        try:
            url = urlsplit(value)
            match = STATUS_PATH.fullmatch(url.path)
            if url.scheme in ("http", "https") and url.netloc.lower() in X_HOSTS and match:
                return exact_id(match[1])
        except ValueError:
            pass
    raise PilotError("Expected an exact numeric post ID or supported X status URL.")


def source_url(value):
    return "https://x.com/i/status/" + post_id(value)


def raindrop_post_id(link):
    """Non-X URLs are irrelevant; unparseable X URLs block exhaustive dedupe."""
    if not isinstance(link, str) or not link:
        raise PilotError("Missing Raindrop primary link; dedupe is incomplete.")
    try:
        url = urlsplit(link)
        if url.hostname in X_HOSTS:
            return post_id(link)
        if url.scheme not in ("http", "https") or not url.netloc:
            raise PilotError("Malformed Raindrop primary link; dedupe is incomplete.")
    except ValueError:
        raise PilotError("Malformed Raindrop primary link; dedupe is incomplete.") from None
    return None
