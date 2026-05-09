"""Tests for draft service."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import Draft, Post, Approval, AuditLog
from bot.services.draft_service import DraftService


@pytest.fixture
def db_session():
    """Create in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed a post for foreign key reference
    post = Post(
        fb_post_id="test_post_1",
        target_id="test_target",
        text_snippet="Test post text",
        language="id",
        likes=10,
        comments=5,
        shares=2,
        score=0.7,
        status="QUEUED",
    )
    session.add(post)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def draft_service(db_session):
    return DraftService(db_session)


class TestSaveDraft:
    def test_save_draft(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft_data = {
            "post_id": post.id,
            "text": "Halo kak, semoga membantu.",
            "source_type": "static",
            "template_id": "static_01",
            "status": "PENDING_REVIEW",
            "fingerprint": "abc123",
        }
        draft = draft_service.save_draft(draft_data)
        assert draft.id is not None
        assert draft.text == "Halo kak, semoga membantu."
        assert draft.status == "PENDING_REVIEW"

    def test_save_manual_draft(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft_data = {
            "post_id": post.id,
            "text": None,
            "source_type": "manual",
            "status": "NEEDS_MANUAL_WRITE",
            "fingerprint": None,
        }
        draft = draft_service.save_draft(draft_data)
        assert draft.status == "NEEDS_MANUAL_WRITE"
        assert draft.text is None


class TestGetPendingDrafts:
    def test_get_pending(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft_service.save_draft({
            "post_id": post.id,
            "text": "Draft 1",
            "source_type": "static",
            "status": "PENDING_REVIEW",
        })
        draft_service.save_draft({
            "post_id": post.id,
            "text": "Draft 2",
            "source_type": "semi_dynamic",
            "status": "PENDING_REVIEW",
        })

        drafts, total = draft_service.get_pending_drafts()
        assert total == 2
        assert len(drafts) == 2


class TestApproval:
    def test_approve_draft(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft = draft_service.save_draft({
            "post_id": post.id,
            "text": "Approve me",
            "source_type": "static",
            "status": "PENDING_REVIEW",
        })

        result = draft_service.approve_draft(draft.id, user_id=1)
        assert result.status == "APPROVED"

        # Check approval record
        approval = db_session.query(Approval).first()
        assert approval.action == "approve"
        assert approval.draft_id == draft.id

    def test_approve_with_edit(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft = draft_service.save_draft({
            "post_id": post.id,
            "text": "Original text",
            "source_type": "static",
            "status": "PENDING_REVIEW",
        })

        result = draft_service.approve_draft(draft.id, user_id=1, edited_text="Edited text")
        assert result.status == "APPROVED"
        assert result.text == "Edited text"

    def test_reject_draft(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft = draft_service.save_draft({
            "post_id": post.id,
            "text": "Reject me",
            "source_type": "static",
            "status": "PENDING_REVIEW",
        })

        result = draft_service.reject_draft(draft.id, user_id=1, reason="Too generic")
        assert result.status == "REJECTED"

        approval = db_session.query(Approval).first()
        assert approval.action == "reject"
        assert approval.reason == "Too generic"

    def test_audit_log_created(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft = draft_service.save_draft({
            "post_id": post.id,
            "text": "Audit me",
            "source_type": "static",
            "status": "PENDING_REVIEW",
        })

        draft_service.approve_draft(draft.id, user_id=1)
        logs = db_session.query(AuditLog).all()
        assert len(logs) == 1
        assert logs[0].action == "approve_draft"


class TestDraftStats:
    def test_stats(self, draft_service, db_session):
        post = db_session.query(Post).first()
        draft_service.save_draft({"post_id": post.id, "text": "A", "source_type": "static", "status": "PENDING_REVIEW"})
        draft_service.save_draft({"post_id": post.id, "text": "B", "source_type": "static", "status": "PENDING_REVIEW"})
        draft_service.save_draft({"post_id": post.id, "text": None, "source_type": "manual", "status": "NEEDS_MANUAL_WRITE"})

        stats = draft_service.get_draft_stats()
        assert stats["pending"] == 2
        assert stats["needs_manual"] == 1
        assert stats["approved"] == 0
        assert stats["rejected"] == 0
