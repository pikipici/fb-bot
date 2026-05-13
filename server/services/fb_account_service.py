"""FB Account Service — CRUD + rotation for Facebook credentials."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from server.crypto import decrypt, encrypt
from server.models import FBAccount

logger = logging.getLogger(__name__)

# Cooldown after use (minutes)
DEFAULT_COOLDOWN_MINUTES = 15
# Max failures before auto-block
MAX_FAILURES_BEFORE_BLOCK = 5


class FBAccountService:
    """Manage Facebook account credentials."""

    def __init__(self, db: Session):
        self.db = db

    def create_account(
        self,
        label: str,
        email: str,
        password: str,
        purpose: str = "both",
        notes: str = "",
    ) -> FBAccount:
        """Create a new FB account with encrypted credentials."""
        account = FBAccount(
            label=label,
            email_encrypted=encrypt(email),
            password_encrypted=encrypt(password),
            purpose=purpose,
            notes=notes,
            status="ACTIVE",
        )
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        logger.info("Created FB account: %s (id=%d)", label, account.id)
        return account

    def get_account(self, account_id: int) -> FBAccount | None:
        """Get account by ID."""
        return self.db.query(FBAccount).filter(FBAccount.id == account_id).first()

    def list_accounts(self, include_disabled: bool = False) -> list[FBAccount]:
        """List all accounts (without decrypted credentials)."""
        query = self.db.query(FBAccount)
        if not include_disabled:
            query = query.filter(FBAccount.status != "DISABLED")
        return query.order_by(FBAccount.id).all()

    def update_account(
        self,
        account_id: int,
        label: str | None = None,
        email: str | None = None,
        password: str | None = None,
        purpose: str | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> FBAccount | None:
        """Update account fields. Re-encrypts credentials if changed."""
        account = self.get_account(account_id)
        if not account:
            return None

        if label is not None:
            account.label = label
        if email is not None:
            account.email_encrypted = encrypt(email)
        if password is not None:
            account.password_encrypted = encrypt(password)
        if purpose is not None:
            account.purpose = purpose
        if notes is not None:
            account.notes = notes
        if status is not None:
            account.status = status

        self.db.commit()
        self.db.refresh(account)
        return account

    def delete_account(self, account_id: int) -> bool:
        """Permanently delete an account."""
        account = self.get_account(account_id)
        if not account:
            return False
        self.db.delete(account)
        self.db.commit()
        return True

    def get_credentials(self, account_id: int) -> dict[str, str] | None:
        """Get decrypted credentials for an account."""
        account = self.get_account(account_id)
        if not account:
            return None
        return {
            "email": decrypt(account.email_encrypted),
            "password": decrypt(account.password_encrypted),
        }

    def get_next_available(self, purpose: str = "scrape") -> FBAccount | None:
        """Get next available account for use (rotation).

        Concurrency:
        * On engines that support row locks (Postgres / MySQL) we use
          ``SELECT ... FOR UPDATE SKIP LOCKED`` so two workers never
          reserve the same account.
        * On SQLite ``with_for_update()`` is a no-op — SQLite's
          single-writer model already prevents true races as long as
          each call happens inside its own commit boundary.

        Selection logic:
        - Status ACTIVE or COOLDOWN with expired cooldown
        - Purpose matches (or 'both')
        - Sorted by: least recently used first
        """
        now = datetime.now(timezone.utc)
        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        supports_lock = dialect in {"postgresql", "mysql", "mariadb"}

        query = (
            self.db.query(FBAccount)
            .filter(
                FBAccount.status.in_(["ACTIVE", "COOLDOWN"]),
                FBAccount.purpose.in_([purpose, "both"]),
            )
            .order_by(FBAccount.last_used_at.asc().nullsfirst())
        )
        if supports_lock:
            # ``skip_locked=True`` so concurrent selectors pick different rows.
            query = query.with_for_update(skip_locked=True)

        accounts = query.all()

        for account in accounts:
            if account.status == "COOLDOWN" and account.cooldown_until:
                cooldown = account.cooldown_until
                if cooldown.tzinfo is None:
                    cooldown = cooldown.replace(tzinfo=timezone.utc)
                if cooldown > now:
                    continue
                account.status = "ACTIVE"

            try:
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self.db.refresh(account)
            return account

        # No rows reactivated → still commit to release any held lock.
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
        return None

    def mark_used(self, account_id: int, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES):
        """Mark account as just used, apply cooldown."""
        account = self.get_account(account_id)
        if not account:
            return

        now = datetime.now(timezone.utc)
        account.last_used_at = now
        account.total_uses += 1
        account.status = "COOLDOWN"
        account.cooldown_until = now + timedelta(minutes=cooldown_minutes)
        self.db.commit()

    def record_failure(self, account_id: int) -> str:
        """Record a failure. Auto-blocks after MAX_FAILURES_BEFORE_BLOCK."""
        account = self.get_account(account_id)
        if not account:
            return "NOT_FOUND"

        account.failure_count += 1

        if account.failure_count >= MAX_FAILURES_BEFORE_BLOCK:
            account.status = "BLOCKED"
            logger.warning(
                "FB account %s (id=%d) auto-blocked after %d failures",
                account.label, account.id, account.failure_count,
            )
        else:
            # Extended cooldown on failure
            now = datetime.now(timezone.utc)
            account.status = "COOLDOWN"
            account.cooldown_until = now + timedelta(minutes=60)

        self.db.commit()
        return account.status

    def record_success(self, account_id: int):
        """Record successful use, reset failure count."""
        account = self.get_account(account_id)
        if not account:
            return
        account.failure_count = 0
        self.db.commit()

    def reactivate(self, account_id: int) -> bool:
        """Manually reactivate a blocked/disabled account."""
        account = self.get_account(account_id)
        if not account:
            return False
        account.status = "ACTIVE"
        account.failure_count = 0
        account.cooldown_until = None
        self.db.commit()
        return True

    def to_dict(self, account: FBAccount, include_email: bool = False) -> dict[str, Any]:
        """Convert account to dict for API response (no password ever exposed)."""
        result = {
            "id": account.id,
            "label": account.label,
            "status": account.status,
            "purpose": account.purpose,
            "last_used_at": account.last_used_at.isoformat() if account.last_used_at else None,
            "cooldown_until": account.cooldown_until.isoformat() if account.cooldown_until else None,
            "failure_count": account.failure_count,
            "total_uses": account.total_uses,
            "notes": account.notes,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            # Cookie-session (safe-to-expose) fields.
            "fb_user_id": account.fb_user_id,
            "fb_name": account.fb_name,
            "fb_profile_pic_url": account.fb_profile_pic_url,
            "cookies_expired_at": (
                account.cookies_expired_at.isoformat()
                if account.cookies_expired_at
                else None
            ),
            "has_cookies": bool(account.cookies_encrypted),
        }
        if include_email:
            result["email"] = (
                decrypt(account.email_encrypted)
                if account.email_encrypted
                else None
            )
        return result

    # --- cookie-session helpers (Layer 1+2) ------------------------------

    def create_cookie_account(
        self,
        label: str,
        cookies: dict[str, str],
        fb_user_id: str,
        fb_name: str | None,
        fb_profile_pic_url: str | None,
        notes: str = "",
    ) -> FBAccount:
        """Create an account connected via cookie session (no email/pw)."""
        from server.crypto import encrypt_cookies

        account = FBAccount(
            label=label,
            email_encrypted=None,
            password_encrypted=None,
            cookies_encrypted=encrypt_cookies(cookies),
            fb_user_id=fb_user_id,
            fb_name=fb_name,
            fb_profile_pic_url=fb_profile_pic_url,
            purpose="both",
            notes=notes,
            status="ACTIVE",
        )
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        logger.info(
            "Created cookie-connected FB account %s (id=%d, fb_user_id=%s)",
            label,
            account.id,
            fb_user_id,
        )
        return account

    def get_cookies(self, account_id: int) -> dict[str, str] | None:
        """Decrypt & return the cookie dict for an account (or None)."""
        from server.crypto import decrypt_cookies

        account = self.get_account(account_id)
        if not account or not account.cookies_encrypted:
            return None
        return decrypt_cookies(account.cookies_encrypted)

    def mark_cookies_expired(self, account_id: int) -> bool:
        """Mark an account's cookie session as expired.

        Sets ``status='EXPIRED'`` and ``cookies_expired_at=now``. The
        scanner uses this to pause itself until the user re-connects.
        """
        account = self.get_account(account_id)
        if not account:
            return False
        account.status = "EXPIRED"
        account.cookies_expired_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def mark_active_from_profile(
        self,
        account_id: int,
        fb_user_id: str,
        fb_name: str | None,
        fb_profile_pic_url: str | None,
    ) -> bool:
        """Flip an account back to ACTIVE after a successful re-validation.

        Clears ``cookies_expired_at`` and refreshes the cached profile
        fields with whatever the validator returned, so the dashboard card
        shows the current FB display name / avatar without a separate
        fetch. Cookie payload itself is not touched.
        """
        account = self.get_account(account_id)
        if not account:
            return False
        account.status = "ACTIVE"
        account.cookies_expired_at = None
        account.failure_count = 0
        account.fb_user_id = fb_user_id
        account.fb_name = fb_name
        account.fb_profile_pic_url = fb_profile_pic_url
        self.db.commit()
        return True

    def replace_cookies(
        self,
        account_id: int,
        cookies: dict[str, str],
        fb_user_id: str,
        fb_name: str | None,
        fb_profile_pic_url: str | None,
    ) -> bool:
        """Swap the stored cookie bundle on an existing account.

        Encrypts the new cookies with Fernet, replaces the old payload,
        refreshes profile fields from the validator, clears
        ``cookies_expired_at``, and flips status back to ACTIVE. Useful
        when the user re-uploads a fresh cookie after a checkpoint or
        expiry without wanting to delete the account (preserves label,
        notes, and CommentHistory).
        """
        from server.crypto import encrypt_cookies

        account = self.get_account(account_id)
        if not account:
            return False
        account.cookies_encrypted = encrypt_cookies(cookies)
        account.fb_user_id = fb_user_id
        account.fb_name = fb_name
        account.fb_profile_pic_url = fb_profile_pic_url
        account.status = "ACTIVE"
        account.cookies_expired_at = None
        account.failure_count = 0
        self.db.commit()
        return True

    def ensure_fingerprint(self, account_id: int) -> tuple[str, int, int]:
        """Return the pinned ``(browser_ua, viewport_w, viewport_h)`` tuple.

        Phase I-A-2 — if any of the three fields is NULL (freshly migrated
        account or legacy row), pick a replacement from
        :mod:`bot.modules.fingerprint_pool` and persist before returning.
        Idempotent on subsequent calls: same tuple in, same tuple out.

        Raises ``ValueError`` if the account does not exist — callers should
        not reach this helper with an invalid id (scan/send paths have
        already fetched the account), so missing == bug.
        """
        # Import locally to avoid pulling bot modules into the import graph
        # of server code at startup (bot.modules uses playwright / heavy deps).
        from bot.modules.fingerprint_pool import pick_ua, pick_viewport

        account = self.get_account(account_id)
        if account is None:
            raise ValueError(f"FBAccount {account_id} not found")

        dirty = False
        if not account.browser_ua:
            account.browser_ua = pick_ua()
            dirty = True
        if not account.viewport_w or not account.viewport_h:
            w, h = pick_viewport()
            account.viewport_w = w
            account.viewport_h = h
            dirty = True

        if dirty:
            self.db.commit()
            self.db.refresh(account)

        return account.browser_ua, account.viewport_w, account.viewport_h
