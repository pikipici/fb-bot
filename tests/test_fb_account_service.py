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
