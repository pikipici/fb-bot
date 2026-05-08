"""User service — business logic for user management."""

from sqlalchemy.orm import Session

from server.auth import hash_password, verify_password
from server.models import User


class UserService:
    """Handle user CRUD and authentication logic."""

    def __init__(self, db: Session):
        self.db = db

    def create_user(self, username: str, password: str, role: str = "viewer") -> User:
        """Create a new user with hashed password."""
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_user_by_username(self, username: str) -> User | None:
        """Find user by username."""
        return self.db.query(User).filter(User.username == username).first()

    def get_user_by_id(self, user_id: int) -> User | None:
        """Find user by ID."""
        return self.db.query(User).filter(User.id == user_id).first()

    def verify_credentials(self, username: str, password: str) -> User | None:
        """Verify username + password. Returns user if valid, None otherwise."""
        user = self.get_user_by_username(username)
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        return user

    def get_user_count(self) -> int:
        """Get total number of users."""
        return self.db.query(User).count()

    def list_users(self) -> list[User]:
        """List all users."""
        return self.db.query(User).all()

    def update_role(self, user_id: int, new_role: str) -> User | None:
        """Update user role."""
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user.role = new_role
        self.db.commit()
        self.db.refresh(user)
        return user

    def deactivate_user(self, user_id: int) -> User | None:
        """Deactivate a user account."""
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user.is_active = False
        self.db.commit()
        self.db.refresh(user)
        return user
