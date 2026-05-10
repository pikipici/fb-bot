"""Keyword filter — match post text against include/exclude lists.

Usage::

    if matches_keyword_filter(post_text, include=src.include, exclude=src.exclude):
        keep_post()

Rules:
* Both lists are case-insensitive.
* Empty ``include`` means "pass everything" (the source didn't ask for
  keyword filtering).
* Non-empty ``include`` is OR semantics — at least one keyword must
  appear in the text.
* ``exclude`` always takes precedence — any match drops the post even
  if ``include`` matched.
* Keywords are matched as fuzzy word-fragments with bounded "extra"
  characters on either side (≤3). This catches natural Indonesian
  morphology — ``murah`` matches ``termurah``, ``laptop`` matches
  ``laptops`` — without matching unrelated compound words like
  ``laptopgaming`` that just happen to share a prefix.

The matcher tokenizes with ``\\w+`` so punctuation, emoji, and
whitespace are natural separators.
"""
from __future__ import annotations

import re
from typing import Iterable

_MAX_EXTRA_CHARS = 3
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalize_keywords(keywords: Iterable[str] | None) -> list[str]:
    """Trim + lowercase + drop empties. Preserves order."""
    if not keywords:
        return []
    out: list[str] = []
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        clean = kw.strip().lower()
        if clean:
            out.append(clean)
    return out


def _word_contains_keyword(word: str, keyword: str) -> bool:
    """Return True iff ``word`` contains ``keyword`` with ≤3 extra chars.

    This intentionally allows short suffixes/prefixes (Indonesian:
    ``ter-``, ``-nya``, English plural ``-s``/``-es``) while rejecting
    longer compound words.
    """
    if len(word) < len(keyword):
        return False
    if keyword not in word:
        return False
    return (len(word) - len(keyword)) <= _MAX_EXTRA_CHARS


def _any_keyword_matches(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    for word in words:
        for kw in keywords:
            if _word_contains_keyword(word, kw):
                return True
    return False


def matches_keyword_filter(
    text: str | None,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> bool:
    """Return True if ``text`` passes the include/exclude filter.

    Args:
        text: raw post text. ``None`` is treated as empty.
        include: at least one of these must appear when non-empty.
        exclude: none of these may appear.

    A non-empty ``include`` iterable that normalizes to zero valid
    keywords (e.g. ``["", "  "]``) is treated as an intentional filter
    request with no valid matches and rejects everything — rather than
    silently ignoring the filter and passing posts through.
    """
    include_raw_list = list(include) if include is not None else []
    exclude_raw_list = list(exclude) if exclude is not None else []
    include_list = _normalize_keywords(include_raw_list)
    exclude_list = _normalize_keywords(exclude_raw_list)
    body = text or ""

    # Exclude wins: short-circuit before checking include.
    if exclude_list and _any_keyword_matches(body, exclude_list):
        return False

    if include_raw_list and not include_list:
        # Caller explicitly provided include terms, but none were valid
        # strings — treat as "filter active, nothing qualifies".
        return False

    if not include_list:
        return True

    return _any_keyword_matches(body, include_list)
