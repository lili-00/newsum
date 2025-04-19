import json
import logging
from typing import Any, Dict, List, Optional

from .summary_utils import extract_text_from_html, fetch_page_content, generate_summary_from_text
from ..config import GNEWS_COUNTRY, GNEWS_API_URL, GNEWS_API_KEY
from datetime import datetime, timedelta, UTC
import httpx
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


async def fetch_headlines_data():
    """
    Fetch latest headlines from gnews api
    :return: dict or None
    """
    # Calculate the datetime object 24 hours ago in UTC
    # Use timezone-aware datetime.now(UTC)
    from_datetime = datetime.now(UTC) - timedelta(days=1)

    # Format it as an ISO 8601 string (adjust format if GNews requires something different)
    # Example: "2025-04-19T04:50:00Z"
    from_timestamp_str = from_datetime.isoformat(timespec='seconds').replace('+00:00', 'Z')
    # Check GNews API docs for the *exact* required format!

    api_params = {
        "apikey": GNEWS_API_KEY,
        "country": GNEWS_COUNTRY,
        "from": from_timestamp_str  # Use the formatted string
    }

    url = GNEWS_API_URL

    # Use parentheses to instantiate the client
    async with httpx.AsyncClient() as client:
        try:
            # Log the actual params being sent
            logger.info(f"Requesting data from API: {GNEWS_API_URL} with params {api_params}")
            response = await client.get(url=url, params=api_params)
            response.raise_for_status()  # Raise exception for 4xx/5xx status codes
            news_data = response.json()
            # Log the total number of articles found
            total_articles = news_data.get('totalArticles', 'N/A')
            logger.info(f"Successfully fetch gnews api data, total results: {total_articles}")
            return news_data
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error fetching latest news list: {exc.response.status_code} for URL {exc.request.url!r} - Response: {exc.response.text}")
            raise HTTPException(status_code=exc.response.status_code,
                                detail=f"Error fetching news list from source: Status {exc.response.status_code}")
        except httpx.RequestError as exc:
            logger.error(f"Request error fetching latest news list: {exc}")
            raise HTTPException(status_code=503, detail="Network error fetching news list from source.")
        except json.JSONDecodeError as exc:
            # It might be helpful to log the text that failed to parse
            response_text = await exc.response.text() if hasattr(exc, 'response') else 'N/A'
            logger.error(
                f"Error decoding JSON from news list API response: {exc}. Response text: {response_text[:500]}...")  # Log partial response
            raise HTTPException(status_code=500, detail="Invalid JSON response from news list source.")
        except Exception as exc:
            logger.error(f"Unexpected error fetching latest news list with params {api_params}: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Unexpected error fetching latest news list.")


async def analyze_gnews_data() -> List[Dict[str, Any]]:
    """
    Fetches headlines, attempts to generate a summary and record its
    generation time for each, and returns a list of dictionaries formatted
    for saving (including 'summary' and 'summary_generated_at').
    """
    processed_articles: List[Dict[str, Any]] = []
    fetched_timestamp = datetime.now(UTC)
    logger.info(f"Starting GNews data analysis at {fetched_timestamp.isoformat()}")

    try:
        news_data = await fetch_headlines_data()
        articles = news_data.get('articles') if isinstance(news_data, dict) else None
        if not articles:
            logger.warning("No articles found in fetched GNews data.")
            return []
    except Exception as e:
        logger.error(f"Failed to fetch or process initial news list: {e}", exc_info=True)
        return []

    logger.info(f"Processing {len(articles)} articles fetched from GNews.")

    for article in articles:
        url = article.get('url')
        title = article.get('title')

        if not url or not title:
            logger.warning(f"Article missing URL or Title. Skipping. Data: {article}")
            continue

        logger.debug(f"Processing article: {title} ({url})")

        description = article.get('description')
        image_url = article.get('image')
        published_at_str = article.get('publishedAt')  # Keep as string for Pydantic parsing

        source_dict = article.get('source')
        source_name: Optional[str] = None
        source_url: Optional[str] = None
        if isinstance(source_dict, dict):
            source_name = source_dict.get('name')
            source_url = source_dict.get('url')
        else:
            logger.warning(f"Missing or invalid source data for article (URL: {url}): {source_dict}")

        # --- Summarization Logic ---
        summary_to_save: Optional[str] = description  # Default/fallback to original description
        summary_generated_ts: Optional[datetime] = None  # Initialize timestamp as None
        article_text_to_summarize: Optional[str] = None

        try:
            logger.debug(f"Fetching content for summarization: {url}")
            html_content = await fetch_page_content(url)  # Assuming returns str or None

            if html_content:
                logger.debug(f"Extracting text from content: {url}")
                article_text_to_summarize = extract_text_from_html(html_content)  # Assuming returns str or None
                if not article_text_to_summarize:
                    logger.warning(f"Could not extract text from {url}. Summary will be original description.")
            else:
                logger.warning(f"Failed to fetch HTML content from {url}. Summary will be original description.")

            # Attempt summarization only if text was successfully extracted
            if article_text_to_summarize:
                logger.debug(f"Generating summary from extracted text (length={len(article_text_to_summarize)}): {url}")
                generated_summary = await generate_summary_from_text(article_text_to_summarize, title)  # Assuming returns str or None
                if generated_summary:
                    logger.info(f"Successfully generated summary for: {url}")
                    summary_to_save = generated_summary
                    summary_generated_ts = datetime.now(UTC)  # *** Record timestamp ONLY on success ***
                else:
                    logger.warning(
                        f"generate_summary_from_text returned empty for {url}. Fallback description will be used.")
                    # summary_generated_ts remains None

        except Exception as summary_error:
            logger.error(f"Error during content fetching/summarization step for {url}: {summary_error}", exc_info=True)
            # summary_generated_ts remains None on error

        # --- Create Flattened Data Dictionary ---
        # This dictionary structure should align with your Pydantic model (GNewsSummaryData)
        processed_article_data = {
            "title": title,
            "description": description,  # Original description
            "url": url,
            "image_url": image_url,
            "published_at": published_at_str,  # Pass string; Pydantic handles parsing
            "source_name": source_name,
            "source_url": source_url,
            "summary": summary_to_save,  # Generated summary or fallback description
            "summary_generated_at": summary_generated_ts  # *** Add the timestamp (or None) ***
        }

        processed_articles.append(processed_article_data)

    logger.info(f"Finished analysis. Prepared {len(processed_articles)} articles with summaries for saving.")
    return processed_articles
