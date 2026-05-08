"""Tests for user service."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.services.user_service import UserService


@pytest.fixture
def db_session():
    """Create in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def user_service(db_session):
    return UserService(db_session)


class TestCreateUser:
    def test_create_user_success(self, user_service):
        user = user_service.create_user("testuser", "password123", "viewer")
        assert user.username == "testuser"
        assert user.role == "viewer"
        assert user.is_active is True
        assert user.password_hash != "password123"  # Should be hashed

    def test_create_admin_user(self, user_service):
        user = user_service.create_user("admin", "admin123", "admin")
        assert user.role == "admin"


class TestGetUser:
    def test_get_by_username(self, user_service):
        user_service.create_user("findme", "pass123", "viewer")
        found = user_service.get_user_by_username("findme")
        assert found is not None
        assert found.username == "findme"

    def test_get_by_username_not_found(self, user_service):
        found = user_service.get_user_by_username("ghost")
        assert found is None

    def test_get_by_id(self, user_service):
        user = user_service.create_user("byid", "pass123", "viewer")
        found = user_service.get_user_by_id(user.id)
        assert found is not None
        assert found.username == "byid"


class TestVerifyCredentials:
    def test_valid_credentials(self, user_service):
        user_service.create_user("valid", "correct_pass", "viewer")
        user = user_service.verify_credentials("valid", "correct_pass")
        assert user is not None
        assert user.username == "valid"

    def test_wrong_password(self, user_service):
        user_service.create_user("valid", "correct_pass", "viewer")
        user = user_service.verify_credentials("valid", "wrong_pass")
        assert user is None

    def test_nonexistent_user(self, user_service):
        user = user_service.verify_credentials("ghost", "pass")
        assert user is None

    def test_inactive_user(self, user_service):
        user = user_service.create_user("inactive", "pass123", "viewer")
        user_service.deactivate_user(user.id)
        result = user_service.verify_credentials("inactive", "pass123")
        assert result is None


class TestUserManagement:
    def test_update_role(self, user_service):
        user = user_service.create_user("promote", "pass123", "viewer")
        updated = user_service.update_role(user.id, "operator")
        assert updated.role == "operator"

    def test_deactivate_user(self, user_service):
        user = user_service.create_user("deact", "pass123", "viewer")
        deactivated = user_service.deactivate_user(user.id)
        assert deactivated.is_active is False

    def test_get_user_count(self, user_service):
        assert user_service.get_user_count() == 0
        user_service.create_user("one", "pass", "viewer")
        user_service.create_user("two", "pass", "viewer")
        assert user_service.get_user_count() == 2

    def test_list_users(self, user_service):
        user_service.create_user("a", "pass", "viewer")
        user_service.create_user("b", "pass", "operator")
        users = user_service.list_users()
        assert len(users) == 2
