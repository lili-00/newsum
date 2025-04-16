import logging
from typing import Annotated
from ..helpers.summary_helper import generate_summary_for_past_hours
from fastapi import routing, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from app.database import get_db_session

logger = logging.getLogger(__name__)

router = routing.APIRouter(
    prefix="/api/summary",
    tags=["Summary"]
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/generate")
async def generate_sum(db: DbSession):
    now_utc = datetime.now(timezone.utc)
    response = await generate_summary_for_past_hours()
    return response
