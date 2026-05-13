"""Tests for FB Account Service."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from server.models import FBAccount, Base
from server.services.fb_account_service import FBAccountService, MAX_FAILURES_BEFORE_BLOCK


@pytest.fixture
def db(tmp_path):
    """Create a test database session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def svc(db):
    return FBAccountService(db)


@pytest.fixture(autouse=True)
def mock_crypto():
    """Mock encrypt/decrypt to avoid needing real key."""
    with patch("server.services.fb_account_service.encrypt", side_effect=lambda x: f"ENC:{x}"):
        with patch("server.services.fb_account_service.decrypt", side_effect=lambda x: x.replace("ENC:", "")):
            yield


class TestCreateAccount:
    def test_create_basic(self, svc):
        account = svc.create_account("Test Account", "test@fb.com", "pass123")
        assert account.id is not None
        assert account.label == "Test Account"
        assert account.email_encrypted == "ENC:test@fb.com"
        assert account.password_encrypted == "ENC:pass123"
        assert account.status == "ACTIVE"
        assert account.purpose == "both"

    def test_create_with_purpose(self, svc):
        account = svc.create_account("Scraper", "s@fb.com", "p", purpose="scrape")
        assert account.purpose == "scrape"

    def test_create_with_notes(self, svc):
        account = svc.create_account("Noted", "n@fb.com", "p", notes="backup account")
        assert account.notes == "backup account"

    def test_create_fingerprint_fields_default_null(self, svc):
        """New account must start with no pinned fingerprint.

        Phase I-A-1 — fields ``browser_ua``, ``viewport_w``, ``viewport_h``
        are assigned lazily by ``ensure_fingerprint`` on first use. Creation
        path leaves them NULL so existing rows (migrated in prod) and newly
        created rows behave identically.
        """
        account = svc.create_account("Fresh", "f@fb.com", "p")
        assert account.browser_ua is None
        assert account.viewport_w is None
        assert account.viewport_h is None


class TestGetAccount:
    def test_get_existing(self, svc):
        created = svc.create_account("A", "a@fb.com", "p")
        found = svc.get_account(created.id)
        assert found is not None
        assert found.label == "A"

    def test_get_nonexistent(self, svc):
        assert svc.get_account(999) is None


class TestListAccounts:
    def test_list_active(self, svc):
        svc.create_account("A1", "a1@fb.com", "p")
        svc.create_account("A2", "a2@fb.com", "p")
        disabled = svc.create_account("A3", "a3@fb.com", "p")
        svc.update_account(disabled.id, status="DISABLED")

        accounts = svc.list_accounts(include_disabled=False)
        assert len(accounts) == 2

    def test_list_with_disabled(self, svc):
        svc.create_account("A1", "a1@fb.com", "p")
        disabled = svc.create_account("A2", "a2@fb.com", "p")
        svc.update_account(disabled.id, status="DISABLED")

        accounts = svc.list_accounts(include_disabled=True)
        assert len(accounts) == 2


class TestUpdateAccount:
    def test_update_label(self, svc):
        account = svc.create_account("Old", "o@fb.com", "p")
        updated = svc.update_account(account.id, label="New")
        assert updated.label == "New"

    def test_update_email(self, svc):
        account = svc.create_account("A", "old@fb.com", "p")
        updated = svc.update_account(account.id, email="new@fb.com")
        assert updated.email_encrypted == "ENC:new@fb.com"

    def test_update_password(self, svc):
        account = svc.create_account("A", "a@fb.com", "old")
        updated = svc.update_account(account.id, password="new")
        assert updated.password_encrypted == "ENC:new"

    def test_update_nonexistent(self, svc):
        assert svc.update_account(999, label="X") is None


class TestDeleteAccount:
    def test_delete_existing(self, svc):
        account = svc.create_account("Del", "d@fb.com", "p")
        assert svc.delete_account(account.id) is True
        assert svc.get_account(account.id) is None

    def test_delete_nonexistent(self, svc):
        assert svc.delete_account(999) is False


class TestGetCredentials:
    def test_get_decrypted(self, svc):
        account = svc.create_account("Cred", "secret@fb.com", "s3cr3t")
        creds = svc.get_credentials(account.id)
        assert creds == {"email": "secret@fb.com", "password": "s3cr3t"}

    def test_get_nonexistent(self, svc):
        assert svc.get_credentials(999) is None


class TestRotation:
    def test_get_next_available_least_recently_used(self, svc):
        a1 = svc.create_account("A1", "a1@fb.com", "p", purpose="scrape")
        a2 = svc.create_account("A2", "a2@fb.com", "p", purpose="scrape")

        # Mark a1 as used
        svc.mark_used(a1.id, cooldown_minutes=60)

        # Should return a2 (never used)
        next_acc = svc.get_next_available(purpose="scrape")
        assert next_acc.id == a2.id

    def test_get_next_available_respects_purpose(self, svc):
        svc.create_account("Scraper", "s@fb.com", "p", purpose="scrape")
        svc.create_account("Poster", "p@fb.com", "p", purpose="post")

        next_acc = svc.get_next_available(purpose="post")
        assert next_acc.label == "Poster"

    def test_get_next_available_both_purpose_matches(self, svc):
        both = svc.create_account("Both", "b@fb.com", "p", purpose="both")

        next_acc = svc.get_next_available(purpose="scrape")
        assert next_acc.id == both.id

    def test_get_next_available_skips_cooldown(self, svc):
        a1 = svc.create_account("A1", "a1@fb.com", "p")
        a2 = svc.create_account("A2", "a2@fb.com", "p")

        # Put a1 in cooldown (not expired)
        svc.mark_used(a1.id, cooldown_minutes=60)

        next_acc = svc.get_next_available()
        assert next_acc.id == a2.id

    def test_get_next_available_expired_cooldown_reactivates(self, svc, db):
        a1 = svc.create_account("A1", "a1@fb.com", "p")

        # Set cooldown in the past
        a1.status = "COOLDOWN"
        a1.cooldown_until = datetime.now(timezone.utc) - timedelta(minutes=5)
        a1.last_used_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        next_acc = svc.get_next_available()
        assert next_acc.id == a1.id
        assert next_acc.status == "ACTIVE"

    def test_get_next_available_none_when_all_busy(self, svc):
        a1 = svc.create_account("A1", "a1@fb.com", "p")
        svc.mark_used(a1.id, cooldown_minutes=60)

        next_acc = svc.get_next_available()
        assert next_acc is None

    def test_get_next_available_skips_blocked(self, svc):
        a1 = svc.create_account("A1", "a1@fb.com", "p")
        svc.update_account(a1.id, status="BLOCKED")

        assert svc.get_next_available() is None


class TestMarkUsed:
    def test_mark_used_sets_cooldown(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        svc.mark_used(account.id, cooldown_minutes=30)

        refreshed = svc.get_account(account.id)
        assert refreshed.status == "COOLDOWN"
        assert refreshed.total_uses == 1
        assert refreshed.last_used_at is not None
        assert refreshed.cooldown_until is not None


class TestFailureTracking:
    def test_failure_increments_count(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        svc.record_failure(account.id)

        refreshed = svc.get_account(account.id)
        assert refreshed.failure_count == 1
        assert refreshed.status == "COOLDOWN"

    def test_auto_block_after_max_failures(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")

        for _ in range(MAX_FAILURES_BEFORE_BLOCK):
            svc.record_failure(account.id)

        refreshed = svc.get_account(account.id)
        assert refreshed.status == "BLOCKED"

    def test_success_resets_failure_count(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        svc.record_failure(account.id)
        svc.record_failure(account.id)
        svc.record_success(account.id)

        refreshed = svc.get_account(account.id)
        assert refreshed.failure_count == 0


class TestReactivate:
    def test_reactivate_blocked(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        svc.update_account(account.id, status="BLOCKED")

        assert svc.reactivate(account.id) is True
        refreshed = svc.get_account(account.id)
        assert refreshed.status == "ACTIVE"
        assert refreshed.failure_count == 0

    def test_reactivate_nonexistent(self, svc):
        assert svc.reactivate(999) is False


class TestToDict:
    def test_without_email(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        d = svc.to_dict(account, include_email=False)
        assert "email" not in d
        assert d["label"] == "A"
        assert d["status"] == "ACTIVE"

    def test_with_email(self, svc):
        account = svc.create_account("A", "a@fb.com", "p")
        d = svc.to_dict(account, include_email=True)
        assert d["email"] == "a@fb.com"
        assert "password" not in d  # Never expose password


class TestEnsureFingerprint:
    """Phase I-A-2 — per-account UA + viewport pinning.

    ``ensure_fingerprint`` is the idempotent accessor called from the bot
    code (scan/send) before building a Playwright context. If the account
    has no pinned UA/viewport yet, we pick one from the pool and persist.
    On subsequent calls the same values are returned — key property, because
    rotating fingerprint is exactly the anti-pattern we're fixing.
    """

    def test_assigns_when_all_null(self, svc):
        account = svc.create_account("Fresh", "f@fb.com", "p")
        ua, w, h = svc.ensure_fingerprint(account.id)
        assert isinstance(ua, str) and ua.startswith("Mozilla/5.0")
        assert isinstance(w, int) and w >= 1000
        assert isinstance(h, int) and h >= 600

        refreshed = svc.get_account(account.id)
        assert refreshed.browser_ua == ua
        assert refreshed.viewport_w == w
        assert refreshed.viewport_h == h

    def test_idempotent_when_already_set(self, svc):
        account = svc.create_account("Pinned", "p@fb.com", "p")
        account.browser_ua = "UA-PINNED"
        account.viewport_w = 1366
        account.viewport_h = 768
        svc.db.commit()

        for _ in range(3):
            ua, w, h = svc.ensure_fingerprint(account.id)
            assert (ua, w, h) == ("UA-PINNED", 1366, 768)

        refreshed = svc.get_account(account.id)
        assert refreshed.browser_ua == "UA-PINNED"
        assert refreshed.viewport_w == 1366
        assert refreshed.viewport_h == 768

    def test_raises_on_missing_account(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.ensure_fingerprint(99999)

    def test_partial_null_still_gets_full_tuple(self, svc):
        """Only UA set (viewport null) -> picks new viewport, keeps existing UA."""
        account = svc.create_account("Half", "h@fb.com", "p")
        account.browser_ua = "UA-HALF"
        svc.db.commit()

        ua, w, h = svc.ensure_fingerprint(account.id)
        assert ua == "UA-HALF"
        assert w and h

        refreshed = svc.get_account(account.id)
        assert refreshed.browser_ua == "UA-HALF"
        assert refreshed.viewport_w == w
        assert refreshed.viewport_h == h


class TestRefreshCookiesSilent:
    """Phase I-B-2 — silent cookie refresh after rotation capture.

    Unlike ``replace_cookies`` (used by admin re-upload flow) this method
    must NOT touch status / fb_name / failure_count / cookies_expired_at.
    It's called from the scanner/sender happy path to persist rotated
    cookies that FB handed us mid-session. Touching status would flip
    EXPIRED → ACTIVE silently on every scan, masking real failures.
    """

    @pytest.fixture(autouse=True)
    def _stub_cookie_crypto(self):
        """Stub ``encrypt_cookies`` / ``decrypt_cookies`` used by the service.

        ``create_cookie_account`` and ``refresh_cookies_silent`` import these
        helpers locally from ``server.crypto``. Patching at that module level
        makes the stubs effective for both import sites.
        """
        def _fake_enc(d):
            return "ENC:" + "|".join(f"{k}={v}" for k, v in d.items())

        def _fake_dec(s):
            body = s.replace("ENC:", "", 1)
            out: dict[str, str] = {}
            for piece in body.split("|"):
                if "=" in piece:
                    k, _, v = piece.partition("=")
                    out[k] = v
            return out

        with patch("server.crypto.encrypt_cookies", side_effect=_fake_enc):
            with patch(
                "server.crypto.decrypt_cookies", side_effect=_fake_dec
            ):
                yield

    @pytest.fixture
    def cookie_account(self, svc):
        acc = svc.create_cookie_account(
            label="cookie-acc",
            cookies={"c_user": "10", "xs": "OLD"},
            fb_user_id="10",
            fb_name="Foo",
            fb_profile_pic_url=None,
        )
        acc.status = "ACTIVE"
        acc.failure_count = 0
        svc.db.commit()
        return acc

    def test_overwrites_encrypted_cookies(self, svc, cookie_account):
        svc.refresh_cookies_silent(
            cookie_account.id, cookies={"c_user": "10", "xs": "NEW"}
        )
        fresh = svc.get_cookies(cookie_account.id)
        assert fresh == {"c_user": "10", "xs": "NEW"}

    def test_does_not_touch_status(self, svc, cookie_account):
        cookie_account.status = "EXPIRED"
        svc.db.commit()

        svc.refresh_cookies_silent(
            cookie_account.id, cookies={"c_user": "10", "xs": "ROT"}
        )
        svc.db.refresh(cookie_account)
        assert cookie_account.status == "EXPIRED"

    def test_does_not_touch_profile_fields(self, svc, cookie_account):
        svc.refresh_cookies_silent(
            cookie_account.id, cookies={"c_user": "10", "xs": "ROT"}
        )
        svc.db.refresh(cookie_account)
        assert cookie_account.fb_name == "Foo"
        assert cookie_account.fb_user_id == "10"
        assert cookie_account.failure_count == 0

    def test_rejects_empty_dict(self, svc, cookie_account):
        """Empty capture = transient read error, must NOT nuke the stored cookie."""
        svc.refresh_cookies_silent(cookie_account.id, cookies={})
        fresh = svc.get_cookies(cookie_account.id)
        assert fresh == {"c_user": "10", "xs": "OLD"}

    def test_rejects_missing_c_user(self, svc, cookie_account):
        """Safety: refuse to persist a partial capture w/o session anchor."""
        svc.refresh_cookies_silent(
            cookie_account.id, cookies={"datr": "X"}
        )
        fresh = svc.get_cookies(cookie_account.id)
        assert fresh == {"c_user": "10", "xs": "OLD"}

    def test_no_op_on_missing_account(self, svc):
        """Silent refresh is best-effort — never raise on unknown id."""
        svc.refresh_cookies_silent(99999, cookies={"c_user": "1"})
        assert svc.get_account(99999) is None
