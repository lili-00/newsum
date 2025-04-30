import logging
from typing import Annotated, List
from sqlalchemy import desc, select

from ..helpers.gnews_helper import analyze_gnews_data
from ..helpers.summary_helper import fetch_latest_news_data
from fastapi import routing, Depends, HTTPException, Query
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


# @router.get("/headline-previews", response_model=[GNewsHeadlinePreviewResponse])
# async def get_gnews_previews(
#         db: DbSession,
#         limit: int = Query(
#             default=8,  # Default to 10 headlines
#             ge=1,
#             le=100,  # Allow fetching up to 100
#             description="Number of latest headlines to fetch (1-100, default is 10)"
#         )
# ):



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


@router.get("/latest", response_model=List[ArticleResponse])
async def get_latest_processed_articles(
        db: DbSession,
        # Updated parameter: Renamed description, changed default to 8
        limit: int = Query(
            default=8,  # Default number of summaries to fetch
            ge=1,  # Minimum value
            le=50,  # Maximum value (adjust as needed)
            description="Number of latest article summaries to fetch (1-50, default is 8)"  # Updated description
        )
):
    """
    Fetches the most recently processed articles (summaries) based on when their
    summaries were generated.
    """
    logger.info(f"Fetching latest {limit} processed articles (summaries).")
    try:
        stmt = (
            select(ArticleRecord)
            # Ensure we only get articles where a summary was actually generated
            .where(ArticleRecord.summary_generated_at.isnot(None))
            .where(ArticleRecord.summary.isnot(None))  # Also check summary text exists
            # Order by the summary generation timestamp descending
            .order_by(desc(ArticleRecord.summary_generated_at))
            # Limit the number of results
            .limit(limit)
        )
        result = await db.execute(stmt)
        latest_articles = result.scalars().all()

        if not latest_articles:
            logger.info("No processed articles with summaries found in the database.")
            return []  # Return empty list if no articles found

        logger.info(f"Returning {len(latest_articles)} latest processed articles (summaries).")
        # FastAPI handles conversion using response_model=List[ArticleResponse]
        return latest_articles

    except Exception as e:
        logger.error(f"Error fetching latest articles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching latest articles.")
