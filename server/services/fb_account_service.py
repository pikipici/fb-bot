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

        Selection logic:
        - Status ACTIVE or COOLDOWN with expired cooldown
        - Purpose matches (or 'both')
        - Sorted by: least recently used first
        """
        now = datetime.now(timezone.utc)

        accounts = (
            self.db.query(FBAccount)
            .filter(
                FBAccount.status.in_(["ACTIVE", "COOLDOWN"]),
                FBAccount.purpose.in_([purpose, "both"]),
            )
            .order_by(FBAccount.last_used_at.asc().nullsfirst())
            .all()
        )

        for account in accounts:
            # Check cooldown
            if account.status == "COOLDOWN" and account.cooldown_until:
                cooldown = account.cooldown_until
                # Ensure timezone-aware comparison
                if cooldown.tzinfo is None:
                    cooldown = cooldown.replace(tzinfo=timezone.utc)
                if cooldown > now:
                    continue
                # Cooldown expired, reactivate
                account.status = "ACTIVE"
                self.db.commit()

            return account

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
        }
        if include_email:
            result["email"] = decrypt(account.email_encrypted)
        return result
