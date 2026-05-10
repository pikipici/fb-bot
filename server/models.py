"""Database models.

Conventions adopted after the 2026-05-09 code review:

* ``DateTime(timezone=True)`` everywhere. Postgres stores the zone, SQLite
  preserves the string. Combined with ``datetime.now(timezone.utc)``
  defaults this keeps aware/naive confusion out of the service layer.
* Foreign keys are declared explicitly and indexed. Frequently filtered
  status columns are indexed to keep listing queries fast.
* Optional columns are typed ``Mapped[T | None]`` so type-checkers and
  SQLAlchemy agree about nullability.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="group")
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="scrape_public")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30)
    max_posts_per_run: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    health_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    health_score: Mapped[float] = mapped_column(Float, default=1.0)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fb_post_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="id")
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
    post_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), default="static")
    template_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="PENDING_REVIEW", index=True
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )


class FBAccount(Base):
    __tablename__ = "fb_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    # email/password_encrypted dipertahankan untuk back-compat dengan flow
    # manual lama, tapi nullable karena akun yang connect via cookie gak
    # butuh creds.
    email_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    purpose: Mapped[str] = mapped_column(String(20), default="both")
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    total_uses: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    # Cookie-session fields (Layer 1+2).
    cookies_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    fb_user_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    fb_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fb_profile_pic_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    cookies_expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Source(Base):
    """A scan target: home feed, group, or page.

    - ``type`` = ``home_feed`` | ``group`` | ``page``
    - ``fb_entity_id`` = numeric group id / page id (null for home_feed)
    - ``keywords_include`` / ``keywords_exclude`` = JSON-encoded list of
      case-insensitive keyword strings (normalized in the service layer).
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fb_entity_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    keywords_include: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords_exclude: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )


class TrendingPost(Base):
    """A post surfaced by the scanner that passed the trending threshold.

    ``status`` lifecycle:
      NEW -> (user clicks Generate Draft) -> DRAFTED
      NEW -> (user clicks Skip) -> SKIPPED
      DRAFTED -> (user Sends) -> COMMENTED
    Re-scans refresh metrics/score but don't overwrite non-NEW status so
    user's intent is preserved.
    """

    __tablename__ = "trending_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fb_post_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    author_fb_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    text_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    reactions_total: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    velocity: Mapped[float] = mapped_column(Float, default=0.0)
    post_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="NEW", index=True)


class CommentTemplate(Base):
    """Promotional comment template.

    MVP: single active row (``is_active=True``). Schema already supports
    multi-template to avoid another migration later.
    """

    __tablename__ = "comment_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100), default="default", nullable=False
    )
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )


class CommentHistory(Base):
    """Log of comments the human sent via the dashboard (Layer 2).

    ``status`` = ``SENT`` | ``FAILED`` | ``PENDING``.
    Rate-limit quota queries filter by ``status='SENT'`` and a rolling
    ``sent_at`` window.
    """

    __tablename__ = "comment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trending_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trending_posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    fb_comment_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )


class ScannerRun(Base):
    """Audit row for each ``scan_all_sources`` execution.

    Exposed via ``GET /api/v1/scanner/status`` so the UI can show
    ``"scan terakhir: 3 menit lalu · 4 post baru"`` instead of the
    ambiguous React-Query fetch timestamp. ``POST /scanner/run-now``
    inserts a ``status='running'`` row that gets flipped to
    ``success``/``failed`` when the task completes.

    Retention: we only ever read the most recent row, but keep history
    for debugging. A maintenance job can TRUNCATE rows older than 30d.
    """

    __tablename__ = "scanner_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    trigger: Mapped[str] = mapped_column(
        String(20), default="beat", nullable=False
    )  # 'beat' | 'manual'
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False, index=True
    )  # 'running' | 'success' | 'failed'
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled_sources: Mapped[int] = mapped_column(Integer, default=0)
    successful_scans: Mapped[int] = mapped_column(Integer, default=0)
    scan_errors: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    aborted_reason: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
