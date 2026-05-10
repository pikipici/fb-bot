"""Tests for ``server.crypto`` — real Fernet round-trip.

The existing ``test_fb_account_service`` suite mocks ``encrypt``/``decrypt``
to avoid needing a key. That leaves the crypto layer completely uncovered.
These tests exercise the real code path using a supplied key, plus the
production fail-fast behavior.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet

from server import crypto


@pytest.fixture(autouse=True)
def reset_crypto_cache():
    """Reset the module-level key cache before and after each test."""
    crypto._reset_key_cache_for_tests()
    yield
    crypto._reset_key_cache_for_tests()


class TestRoundTrip:
    def test_encrypt_decrypt_round_trip(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("CREDENTIALS_KEY", key)
        plaintext = "hello@facebook.com:s3cr3t pa$$"
        ciphertext = crypto.encrypt(plaintext)
        assert ciphertext != plaintext
        assert crypto.decrypt(ciphertext) == plaintext

    def test_ciphertext_is_non_deterministic(self, monkeypatch):
        # Fernet uses a fresh IV each call, so ciphertexts must differ.
        monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
        a = crypto.encrypt("same input")
        b = crypto.encrypt("same input")
        assert a != b
        assert crypto.decrypt(a) == "same input"
        assert crypto.decrypt(b) == "same input"


class TestKeyValidation:
    def test_invalid_key_raises(self, monkeypatch):
        monkeypatch.setenv("CREDENTIALS_KEY", "obviously-not-a-fernet-key")
        with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
            crypto.encrypt("data")

    def test_key_of_wrong_length_raises(self, monkeypatch):
        # Valid base64 but not 32 decoded bytes.
        bad = base64.urlsafe_b64encode(b"too-short").decode()
        monkeypatch.setenv("CREDENTIALS_KEY", bad)
        with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
            crypto.encrypt("data")


class TestProductionFailFast:
    def test_missing_key_in_production_raises(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
            crypto.encrypt("data")

    def test_ephemeral_key_in_dev_ok(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
        # Should not raise; ephemeral key is generated transparently.
        plaintext = "temp"
        assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext


class TestCookieHelpers:
    """Cookie helpers wrap encrypt/decrypt + serialize/parse so callers
    can persist a cookie ``dict`` without boilerplate.
    """

    def test_cookie_dict_roundtrip(self, monkeypatch):
        monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
        cookies = {
            "c_user": "100001234567890",
            "xs": "38%3AAbC...deadbeef",
            "datr": "long-random-token",
            "fr": "another-value",
        }
        enc = crypto.encrypt_cookies(cookies)
        assert isinstance(enc, str)
        assert "c_user" not in enc  # must be encrypted, not visible
        out = crypto.decrypt_cookies(enc)
        assert out == cookies

    def test_empty_dict_roundtrip(self, monkeypatch):
        monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
        enc = crypto.encrypt_cookies({})
        assert crypto.decrypt_cookies(enc) == {}

    def test_preserves_values_with_equals_and_semicolons(self, monkeypatch):
        monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
        # Real FB cookie values contain '=' (base64 padding) regularly.
        cookies = {"xs": "abc=def=ghi", "c_user": "123"}
        enc = crypto.encrypt_cookies(cookies)
        assert crypto.decrypt_cookies(enc) == cookies

    def test_non_deterministic_ciphertext(self, monkeypatch):
        monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
        cookies = {"c_user": "1", "xs": "2"}
        a = crypto.encrypt_cookies(cookies)
        b = crypto.encrypt_cookies(cookies)
        assert a != b
