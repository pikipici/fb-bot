"""Reports router."""

from fastapi import APIRouter, Depends

from server.auth import get_current_user

router = APIRouter()


@router.get("/reports/daily")
async def get_daily_report(
    user: dict = Depends(get_current_user),
):
    """Get daily report data."""
    # TODO: Generate daily report
    return {"report": {}, "period": "daily"}


@router.get("/reports/weekly")
async def get_weekly_report(
    user: dict = Depends(get_current_user),
):
    """Get weekly report data."""
    # TODO: Generate weekly report
    return {"report": {}, "period": "weekly"}
