"""Settings router."""

from fastapi import APIRouter, Depends

from server.auth import require_role, Role

router = APIRouter()


@router.get("/settings")
async def get_settings(
    user: dict = Depends(require_role(Role.ADMIN)),
):
    """Get current system settings."""
    # TODO: Load from config files
    return {"settings": {}}


@router.put("/settings")
async def update_settings(
    settings: dict,
    user: dict = Depends(require_role(Role.ADMIN)),
):
    """Update system settings."""
    # TODO: Validate and save settings
    # TODO: Log to audit_logs
    return {"status": "ok"}
