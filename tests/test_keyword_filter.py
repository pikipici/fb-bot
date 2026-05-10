"""Tests for keyword_filter — include/exclude match against post text."""
from __future__ import annotations

from bot.modules.keyword_filter import matches_keyword_filter


class TestIncludeList:
    def test_empty_include_passes_everything(self):
        assert matches_keyword_filter("anything at all", include=[], exclude=[])

    def test_single_include_match_passes(self):
        assert matches_keyword_filter(
            "Jual laptop gaming murah", include=["laptop"], exclude=[]
        )

    def test_include_case_insensitive(self):
        assert matches_keyword_filter(
            "Jual LAPTOP gaming", include=["laptop"], exclude=[]
        )
        assert matches_keyword_filter(
            "jual laptop gaming", include=["LAPTOP"], exclude=[]
        )

    def test_include_unicode_match(self):
        assert matches_keyword_filter(
            "Jual laptop termurah gan 👍", include=["murah"], exclude=[]
        )

    def test_any_of_include_is_enough(self):
        """OR semantics — match at least one."""
        assert matches_keyword_filter(
            "cari hp bekas", include=["laptop", "hp"], exclude=[]
        )

    def test_include_no_match_rejected(self):
        assert not matches_keyword_filter(
            "jual motor matic", include=["laptop", "hp"], exclude=[]
        )

    def test_include_word_boundary_prevents_substring(self):
        """``laptop`` in include shouldn't match ``laptops`` continuation
        that's actually part of a different word — but 's' suffix should
        still match (common plural). Must NOT match ``laptopgaming`` as
        one token.
        """
        # plural form still counts (prefix match)
        assert matches_keyword_filter(
            "beli laptops baru", include=["laptop"], exclude=[]
        )
        # glued together — reject (not a real laptop mention)
        assert not matches_keyword_filter(
            "laptopgaming is not a word", include=["laptop"], exclude=[]
        )


class TestExcludeList:
    def test_empty_exclude_has_no_effect(self):
        assert matches_keyword_filter(
            "jual laptop", include=["laptop"], exclude=[]
        )

    def test_exclude_match_rejects(self):
        assert not matches_keyword_filter(
            "jual laptop rusak murah", include=["laptop"], exclude=["rusak"]
        )

    def test_exclude_case_insensitive(self):
        assert not matches_keyword_filter(
            "jual laptop RUSAK", include=["laptop"], exclude=["rusak"]
        )

    def test_exclude_any_match_rejects(self):
        assert not matches_keyword_filter(
            "jual laptop bekas tapi masih oke",
            include=["laptop"],
            exclude=["rusak", "bekas"],
        )

    def test_exclude_takes_precedence_over_include(self):
        """If a post matches include AND exclude, it's rejected."""
        assert not matches_keyword_filter(
            "jual laptop rusak total",
            include=["laptop"],
            exclude=["rusak"],
        )

    def test_exclude_with_empty_include_still_works(self):
        """Empty include = pass everything, unless excluded."""
        assert not matches_keyword_filter(
            "info lowongan kerja bandung", include=[], exclude=["lowongan"]
        )


class TestInputHandling:
    def test_empty_text_no_include_passes(self):
        assert matches_keyword_filter("", include=[], exclude=[])

    def test_empty_text_with_include_rejected(self):
        assert not matches_keyword_filter(
            "", include=["laptop"], exclude=[]
        )

    def test_none_text_handled_gracefully(self):
        assert not matches_keyword_filter(
            None, include=["laptop"], exclude=[]
        )
        assert matches_keyword_filter(None, include=[], exclude=[])

    def test_keyword_whitespace_trimmed(self):
        assert matches_keyword_filter(
            "jual laptop", include=["  laptop  "], exclude=[]
        )

    def test_empty_keyword_strings_ignored(self):
        assert not matches_keyword_filter(
            "jual motor", include=["", "  "], exclude=[]
        )


class TestMultilineText:
    def test_matches_across_newlines(self):
        text = "Halo gan\njual laptop murah\nCOD daerah Jakarta"
        assert matches_keyword_filter(
            text, include=["laptop"], exclude=[]
        )
        assert not matches_keyword_filter(
            text, include=["laptop"], exclude=["jakarta"]
        )
