"""Helpers for classifying Facebook post URLs.

Shared between the scanner (drop unsupported posts before they ever hit
``trending_posts``) and the ``POST /trending/{id}/comment`` router (final
safety net + API response flag so the UI can render a warning badge).
"""
from __future__ import annotations


# URL shapes that CANNOT host a web comment composer — Stories/Reels/Watch
# render in dedicated viewers without the regular composer DOM, and the
# Playwright sender will just time out hunting for a textbox that never
# exists. Reject these early everywhere:
#   1. scanner drops them before insert,
#   2. /trending/{id}/comment returns 415 instead of burning ~60s,
#   3. list payload exposes ``unsupported_kind`` so UI flags + disables.
_UNSUPPORTED_PATH_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("/stories/", "Stories"),
    ("/reel/", "Reel"),
    ("/reels/", "Reel"),
    ("/watch/", "Watch"),
    ("/share/r/", "Reel share"),
    ("/share/v/", "Video share"),
)


def classify_unsupported_post_url(post_url: str | None) -> str | None:
    """Return a human label if ``post_url`` points to a non-commentable view.

    Matching is path-based and case-insensitive so it tolerates both
    ``www.facebook.com`` and ``m.facebook.com`` hosts as well as trailing
    query strings. Returns ``None`` when the URL looks commentable (or is
    missing entirely — upstream callers already handle empty ``post_url``).
    """
    if not post_url:
        return None
    lowered = post_url.lower()
    for fragment, label in _UNSUPPORTED_PATH_FRAGMENTS:
        if fragment in lowered:
            return label
    return None


def is_unsupported_post_url(post_url: str | None) -> bool:
    """Boolean shorthand for :func:`classify_unsupported_post_url`."""
    return classify_unsupported_post_url(post_url) is not None
