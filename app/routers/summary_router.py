import logging
from typing import Annotated, List, Optional
from sqlalchemy import desc, select

from ..helpers.gnews_helper import analyze_gnews_data
from ..helpers.summary_helper import fetch_latest_news_data
from fastapi import routing, Depends, HTTPException, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from app.database import get_db_session
from ..models import ArticleRecord
from ..models.models import GNewsArticleSummary
from ..models.summary import ArticleResponse, GNewsSummaryData, GNewsHeadlineResponse, GNewsHeadlinePreviewResponse

logger = logging.getLogger(__name__)

router = routing.APIRouter(
    prefix="/api/summary",
    tags=["Summary"]
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/headline-previews", response_model=List[GNewsHeadlinePreviewResponse])
async def get_headline_previews(
        db: DbSession,
        limit: int = Query(
            default=10,  # Default to 10 headlines
            ge=1,
            le=100,  # Allow fetching up to 100
            description="Number of latest headline previews to fetch (1-100, default is 10)"
        )
):
    """
    Fetches preview data for the most recent headlines including:
    - Title
    - Source name
    - Published date
    - Preview image
    - Headline ID (for fetching details)
    
    This lighter endpoint is optimized for UI display in a list/grid view.
    """
    logger.info(f"Fetching {limit} headline previews for UI display")
    try:
        # Select exactly the fields needed for preview (including ID)
        stmt = (
            select(GNewsArticleSummary)
            .order_by(desc(GNewsArticleSummary.published_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        headlines = result.scalars().all()

        if not headlines:
            logger.info("No headline previews found")
            return []

        logger.info(f"Returning {len(headlines)} headline previews")
        return headlines

    except Exception as e:
        logger.error(f"Error fetching headline previews: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching headline previews")


@router.get("/headline/{headline_id}", response_model=GNewsHeadlineResponse)
async def get_headline_detail(
        db: DbSession,
        headline_id: int = Path(..., description="The ID of the headline to fetch")
):
    """
    Fetches detailed information for a specific headline by its ID.
    Returns the complete headline data including the full summary text.
    """
    logger.info(f"Fetching detailed data for headline ID: {headline_id}")
    try:
        stmt = select(GNewsArticleSummary).where(GNewsArticleSummary.id == headline_id)
        result = await db.execute(stmt)
        headline = result.scalar_one_or_none()

        if not headline:
            logger.warning(f"Headline with ID {headline_id} not found")
            raise HTTPException(status_code=404, detail="Headline not found")

        logger.info(f"Returning detailed data for headline ID: {headline_id}")
        return headline

    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        logger.error(f"Error fetching headline detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching headline detail")


@router.get("/headlines", response_model=List[GNewsHeadlineResponse])
async def get_gnews_headlines(
        db: DbSession,
        limit: int = Query(
            default=8,  # Default to 10 headlines
            ge=1,
            le=100,  # Allow fetching up to 100
            description="Number of latest headlines to fetch (1-100, default is 10)"
        )
):
    """
    Fetches the most recent GNews article summaries from the database,
    ordered by publication date.
    """
    logger.info(f"Fetching latest {limit} GNews headlines from 'gnews_summaries' table.")
    try:
        stmt = (
            select(GNewsArticleSummary)
            # Order by publication date, newest first
            .order_by(desc(GNewsArticleSummary.published_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        latest_headlines = result.scalars().all()  # Fetch all results up to the limit

        if not latest_headlines:
            logger.info("No GNews headlines found in the database.")
            return []  # Return empty list if none found

        logger.info(f"Returning {len(latest_headlines)} latest GNews headlines.")
        # FastAPI uses response_model=List[GNewsHeadlineResponse]
        # to convert the list of GNewsArticleSummary ORM objects
        return latest_headlines

    except Exception as e:
        logger.error(f"Error fetching GNews headlines: {e}", exc_info=True)
        # Return a generic error response
        raise HTTPException(status_code=500, detail="Internal server error fetching headlines.")
