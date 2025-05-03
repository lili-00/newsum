# --- Standard Library Imports ---
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, Any, Optional, List, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager

# --- Third-party Imports ---
import httpx
from bs4 import BeautifulSoup
import google.generativeai as genai
from pydantic import ValidationError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import select, exists, desc  # Added desc import
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# --- Application Imports (Adjust paths as necessary) ---
from app import config  # Your application config
from app.database import get_db_session  # Your DB session dependency
from app.helpers.gnews_helper import analyze_gnews_data
from app.helpers.summary_utils import fetch_page_content, extract_text_from_html, generate_summary_from_text
from app.models.models import ArticleRecord, GNewsArticleSummary  # Your SQLAlchemy model
from app.models.summary import ArticleForProcessing, GNewsSummaryData  # Your Pydantic schema for validation/response

# --- Logger Setup ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # Configure basic logging if no handlers are configured elsewhere
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Global Variables & Configuration ---
DbSession = Annotated[AsyncSession, Depends(get_db_session)]

genai_configured = False
if not config.GEMINI_API_KEY:
    logger.error("FATAL: GEMINI_API_KEY not found in configuration.")
else:
    try:
        genai.configure(api_key=config.GEMINI_API_KEY)
        genai_configured = True
        logger.info("Google Generative AI SDK configured successfully using config.")
    except Exception as e:
        logger.error(f"Error configuring Google Generative AI SDK using config: {e}", exc_info=True)

MODEL_NAME = config.GEMINI_MODEL_NAME


# === Core Helper Functions ===


# === Data Fetching and Processing Functions ===

async def fetch_latest_news_data():
    """
    Fetch latest news from top resources using configured parameters.
    (Removed hours parameter)
    """
    # 1. Get the current time in UTC
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 2. Calculate the time 12 hours ago
    twelve_hours_ago_utc = now_utc - timedelta(hours=12)

    # 3. Format the time in YYYY-MM-DDTHH:MM:SSZ format
    #    strftime formats the date and time, then we append 'Z' for UTC.
    from_time_str = twelve_hours_ago_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 4. Construct the API parameters dictionary
    api_params = {
        "apikey": config.NEWS_API_KEY,
        "country": config.NEWS_COUNTRY,
        "prioritydomain": config.PRIORITY_DOMAIN,
        "from": from_time_str,  # Add the dynamically calculated 'from' time
    }
    url = config.NEWS_API_URL  # Ensure this is set in your config

    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Requesting latest news data from API: {url} with params: {api_params}")
            response = await client.get(url, params=api_params)
            response.raise_for_status()
            news_data = response.json()
            logger.info(f"Successfully fetched latest news list. Total results: {news_data.get('totalResults')}")
            return news_data
        # ... (rest of error handling as before) ...
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error fetching latest news list: {exc.response.status_code} for URL {exc.request.url!r}")
            raise HTTPException(status_code=exc.response.status_code, detail="Error fetching news list from source.")
        except httpx.RequestError as exc:
            logger.error(f"Request error fetching latest news list: {exc}")
            raise HTTPException(status_code=503, detail="Network error fetching news list from source.")
        except json.JSONDecodeError as exc:
            logger.error(f"Error decoding JSON from news list API: {exc}")
            raise HTTPException(status_code=500, detail="Invalid JSON response from news list source.")
        except Exception as exc:
            logger.error(f"Unexpected error fetching latest news list: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Unexpected error fetching latest news list.")


async def analyze_news_data() -> List[Dict[str, Any]]:
    """
    Fetches the LATEST news list, then for each article: fetches its content,
    generates a summary using Gemini, and combines with original metadata.
    """
    processed_articles: List[Dict[str, Any]] = []
    fetch_timestamp = datetime.now(timezone.utc)

    # 1. Fetch the list of news articles
    try:
        news_data = await fetch_latest_news_data()
        if not news_data or 'results' not in news_data or not news_data['results']:
            logger.warning("No latest news articles found or received invalid data.")
            return []
    # ... (rest of error handling as before) ...
    except HTTPException as http_exc:
        logger.error(f"Failed to fetch initial latest news list: {http_exc.detail} (Status: {http_exc.status_code})")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during initial latest news list fetch: {e}", exc_info=True)
        return []

    articles_to_process = news_data.get('results', [])
    logger.info(f"Starting analysis for {len(articles_to_process)} articles fetched from the latest news feed.")

    # 2. Process each article
    for article in articles_to_process:
        article_id = article.get("article_id")
        if not article_id:
            logger.warning("Found article with no article_id. Skipping.")
            continue

        title = article.get("title")
        link = article.get("link")
        description = article.get("description")
        keywords = article.get("keywords")
        pubDate_str = article.get("pubDate")

        logger.debug(f"Processing article ID: {article_id}, Title: {title}")

        if not link:
            logger.warning(f"Article ID {article_id} ('{title}') has no link. Skipping.")
            continue

        generated_summary = None
        summary_generated_at_ts = None

        # 3. Fetch, Parse, Summarize...
        html_content = await fetch_page_content(link)
        article_text_to_summarize = None
        if html_content:
            article_text_to_summarize = extract_text_from_html(html_content)
            if not article_text_to_summarize:
                logger.warning(
                    f"Could not extract text from {link} for article ID {article_id}. Falling back to description.")
                if description: article_text_to_summarize = description
        else:
            logger.warning(
                f"Failed to fetch content from {link} for article ID {article_id}. Falling back to description.")
            if description: article_text_to_summarize = description

        if article_text_to_summarize:
            generated_summary = await generate_summary_from_text(article_text_to_summarize, title)
            if generated_summary:
                summary_generated_at_ts = datetime.now(timezone.utc)
            else:
                logger.warning(
                    f"Failed to generate summary for article ID {article_id} (used {'description' if not html_content or not article_text_to_summarize else 'fetched content'}).")
        else:
            logger.warning(f"No content (fetched or description) available to summarize for article ID {article_id}.")

        # 4. Structure the result
        processed_article_data = {
            "article_id": article_id,
            "title": title,
            "reference_url": link,
            "description": description,
            "keywords": keywords,
            "summary": generated_summary,
            "source_name": article.get("source_name"),
            "pubDate": pubDate_str,  # Keep as string for Pydantic/DB layer to handle
            "summary_generated_at": summary_generated_at_ts
        }
        processed_articles.append(processed_article_data)

    logger.info(f"Finished analysis. Processed {len(processed_articles)} articles from the latest news feed.")
    return processed_articles


async def save_processed_articles(db: AsyncSession, processed_articles: List[Dict[str, Any]]):
    """
    Saves a list of processed article data dictionaries to the database.
    Checks for existing articles based on article_id to avoid duplicates,
    and handles potential IntegrityErrors during commit due to race conditions.
    """
    articles_added_count = 0
    articles_skipped_count = 0
    articles_to_add = []  # Keep track of records added to the session
    logger.info(f"Attempting to save {len(processed_articles)} processed articles to database.")

    existing_article_ids = set()
    # Optional optimization: Fetch all potentially existing IDs in one query
    # This reduces DB load compared to checking one by one inside the loop,
    # but might fetch more than needed if processed_articles is small.
    # Comment this section out if you prefer the per-article check inside the loop.
    potential_ids = [a.get("article_id") for a in processed_articles if a.get("article_id")]
    if potential_ids:
        id_check_stmt = select(ArticleRecord.article_id).where(ArticleRecord.article_id.in_(potential_ids))
        existing_results = await db.execute(id_check_stmt)
        existing_article_ids = set(existing_results.scalars().all())
        logger.debug(f"Pre-checked existence for {len(potential_ids)} IDs, found {len(existing_article_ids)} existing.")

    for article_data in processed_articles:
        article_id = article_data.get("article_id")
        if not article_id:
            logger.warning("Skipping article data with missing article_id.")
            articles_skipped_count += 1
            continue

        try:
            # --- Check against pre-fetched set (or query DB if not pre-fetching) ---
            # article_exists = article_id in existing_article_ids # Use this if pre-fetching
            # --- OR ---
            # Check if article already exists (if not pre-fetching)
            stmt = select(exists().where(ArticleRecord.article_id == article_id))
            article_exists = await db.scalar(stmt)
            # --- End Check Choice ---

            if article_exists:
                if article_id not in existing_article_ids:  # Log only if not already known from pre-fetch
                    logger.debug(f"Article ID {article_id} already exists in DB. Skipping.")
                articles_skipped_count += 1
                continue
            # --- END CHECK ---

            # --- If article does not exist, proceed with validation and creation ---
            try:
                pydantic_article = ArticleForProcessing(
                    article_id=article_id,
                    title=article_data.get('title'),
                    reference_url=article_data.get('reference_url'),
                    description=article_data.get('description'),
                    keywords=article_data.get('keywords'),
                    summary=article_data.get('summary'),
                    source_name=article_data.get('source_name'),
                    pubDate=article_data.get('pubDate'),
                    summary_generated_at=article_data.get('summary_generated_at')
                )
                new_record = ArticleRecord(
                    article_id=pydantic_article.article_id,
                    title=pydantic_article.title,
                    reference_url=str(pydantic_article.reference_url) if pydantic_article.reference_url else None,
                    description=pydantic_article.description,
                    keywords=pydantic_article.keywords,
                    summary=pydantic_article.summary,
                    source_name=pydantic_article.source_name,
                    publication_date=pydantic_article.publication_date,
                    summary_generated_at=pydantic_article.summary_generated_at
                )
            except ValidationError as val_err:
                logger.error(f"Pydantic validation failed for article ID {article_id}: {val_err}. Skipping save.")
                articles_skipped_count += 1
                continue
            except Exception as map_err:
                logger.error(f"Error mapping data for article ID {article_id}: {map_err}. Skipping save.",
                             exc_info=True)
                articles_skipped_count += 1
                continue

            # Add the new record to the list to be added to session
            articles_to_add.append(new_record)
            articles_added_count += 1
            logger.debug(f"Prepared article ID {article_id} for insertion.")

        except Exception as e:
            logger.error(f"Error processing article ID {article_id} for saving: {e}", exc_info=True)
            articles_skipped_count += 1
            continue

    # Add all prepared records to the session
    if articles_to_add:
        db.add_all(articles_to_add)
        logger.info(f"Added {len(articles_to_add)} new article records to the session.")
    else:
        logger.info("No new articles qualified for adding to the session.")

    # Commit changes if any articles were added to the session
    if articles_added_count > 0 and articles_to_add:  # Check both to be safe
        try:
            await db.commit()
            logger.info(
                f"Successfully committed {articles_added_count} new articles to the database. Skipped {articles_skipped_count}.")
        # --- ADDED HANDLING FOR COMMIT-TIME IntegrityError ---
        except IntegrityError as commit_err:
            # Check if it's the specific unique violation error we expect
            # The specific error code for unique_violation in PostgreSQL is '23505'
            if isinstance(commit_err.orig, Exception) and getattr(commit_err.orig, 'sqlstate', None) == '23505':
                logger.warning(
                    f"Database commit failed likely due to race condition (unique constraint violation): {commit_err.orig}. Rolling back.")
            else:
                # Log other IntegrityErrors more severely
                logger.error(f"Database commit failed due to IntegrityError: {commit_err}", exc_info=True)
            await db.rollback()
            logger.info("Database transaction rolled back due to commit error.")
        # --- END HANDLING ---
        except Exception as commit_err:
            # Catch other potential commit errors
            logger.error(f"Database commit failed unexpectedly: {commit_err}", exc_info=True)
            await db.rollback()
            logger.info("Database transaction rolled back due to unexpected commit error.")
    else:
        # No need to commit if nothing was added
        logger.info(
            f"No new articles were added to the session in this batch. Total Skipped: {articles_skipped_count}.")


async def save_processed_gnews_articles(db: AsyncSession, processed_articles: List[Dict[str, Any]]):
    """
    Saves a list of processed article data dictionaries to the database
    using the GNewsArticleSummary model. Checks for existing articles
    based on the unique 'url' field to avoid duplicates, and handles potential
    IntegrityErrors during commit due to race conditions or other constraints.

    Args:
        db: The AsyncSession instance.
        processed_articles: A list of dictionaries, each representing an article's
                           data matching the fields expected by GNewsSummaryData.

    Returns:
        A tuple containing (articles_added_count, articles_skipped_count).
    """
    articles_added_count = 0
    articles_skipped_count = 0
    articles_to_add: List[GNewsArticleSummary] = []  # List of SQLAlchemy objects
    logger.info(
        f"Attempting to save {len(processed_articles)} processed articles to table '{GNewsArticleSummary.__tablename__}'.")

    existing_urls: Set[str] = set()
    # --- Pre-fetch existing URLs (Optimization) ---
    # Extract potential URLs ensuring they are strings before querying
    potential_urls = []
    for a in processed_articles:
        url_val = a.get("url")
        if isinstance(url_val, str) and url_val:
            potential_urls.append(url_val)
        elif url_val:
            # Log if URL is not a string, might indicate an issue upstream
            logger.warning(f"Article data contains non-string URL: {url_val}. Skipping pre-fetch check for this item.")

    if potential_urls:
        try:
            # Query using the correct SQLAlchemy model and field
            url_check_stmt = select(GNewsArticleSummary.url).where(GNewsArticleSummary.url.in_(potential_urls))
            existing_results = await db.execute(url_check_stmt)
            existing_urls = set(existing_results.scalars().all())
            if existing_urls:
                logger.debug(
                    f"Pre-checked existence for {len(potential_urls)} URLs, found {len(existing_urls)} existing.")
            else:
                logger.debug(f"Pre-checked existence for {len(potential_urls)} URLs, none found existing.")
        except Exception as fetch_err:
            logger.error(f"Error pre-fetching existing article URLs: {fetch_err}. Proceeding without pre-fetch.",
                         exc_info=True)
            existing_urls = set()  # Reset on error

    # --- Process each article dictionary ---
    for article_data in processed_articles:
        # --- Identify the article using URL ---
        url_str = article_data.get("url")  # Get the URL, likely as a string initially
        if not isinstance(url_str, str) or not url_str:
            # Log title if available for better identification of skipped item
            title_preview = article_data.get('title', 'N/A')[:50]
            logger.warning(
                f"Skipping article data with missing or invalid URL (Title: '{title_preview}...'). URL value: {url_str}")
            articles_skipped_count += 1
            continue

        try:
            # --- Check for Duplicates using URL ---
            if url_str in existing_urls:
                # Already known to exist from pre-fetch
                articles_skipped_count += 1
                continue
            # --- Fallback DB Check (Optional - if not pre-fetching or as double check) ---
            # Uncomment if needed
            # stmt = select(exists().where(GNewsArticleSummary.url == url_str))
            # article_exists_in_db = await db.scalar(stmt)
            # if article_exists_in_db:
            #     logger.debug(f"Article URL {url_str} already exists in DB (checked individually). Skipping.")
            #     articles_skipped_count += 1
            #     continue
            # --- End Fallback Check ---

            # --- If article URL does not exist, proceed with validation and creation ---
            try:
                # 1. Validate data using Pydantic model
                # Ensure GNewsSummaryData includes summary & summary_generated_at fields
                pydantic_article = GNewsSummaryData(**article_data)

                # 2. Create SQLAlchemy model instance from validated Pydantic object
                new_record = GNewsArticleSummary(
                    # --- Existing Mappings ---
                    title=pydantic_article.title,
                    description=pydantic_article.description,
                    url=str(pydantic_article.url),
                    image_url=str(pydantic_article.image_url) if pydantic_article.image_url else None,
                    published_at=pydantic_article.published_at,  # Pydantic ensures this is datetime
                    source_name=pydantic_article.source_name,
                    source_url=str(pydantic_article.source_url) if pydantic_article.source_url else None,
                    # --- Add Missing Mappings ---
                    summary=pydantic_article.summary,  # Add this line
                    summary_generated_at=pydantic_article.summary_generated_at  # Add this line
                    # --- End Add ---
                )
            except ValidationError as val_err:
                title_preview = article_data.get('title', 'N/A')[:50]
                logger.warning(
                    f"Pydantic validation failed for article (URL: {url_str}, Title: '{title_preview}'): {val_err}. Skipping save.")
                articles_skipped_count += 1
                continue
            except Exception as map_err:
                # Catch broader errors during Pydantic/SQLAlchemy instantiation
                title_preview = article_data.get('title', 'N/A')[:50]
                logger.error(
                    f"Error creating models for article (URL: {url_str}, Title: '{title_preview}'): {map_err}. Skipping save.",
                    exc_info=True)
                articles_skipped_count += 1
                continue

            # Add the new record to the list to be added to session
            articles_to_add.append(new_record)
            logger.debug(f"Prepared article (URL: {url_str}, Title: '{pydantic_article.title[:50]}...') for insertion.")

        except Exception as e:
            # Catch unexpected errors during the processing of a single article dict
            title_preview = article_data.get('title', 'N/A')[:50]
            logger.error(
                f"Unexpected error processing article data (URL: {url_str}, Title: '{title_preview}...') for saving: {e}",
                exc_info=True)
            articles_skipped_count += 1
            continue

    # --- Add all prepared records to the session and commit ---
    if articles_to_add:
        logger.info(
            f"Adding {len(articles_to_add)} new articles to the session for table '{GNewsArticleSummary.__tablename__}'.")
        db.add_all(articles_to_add)
        try:
            await db.commit()
            articles_added_count = len(articles_to_add)  # Count successfully committed articles
            logger.info(f"Successfully committed {articles_added_count} new articles.")
        except IntegrityError as int_err:
            await db.rollback()
            # IntegrityError likely due to unique constraint violation (URL) from race condition
            logger.error(
                f"Database integrity error during commit (likely duplicate URL race condition): {int_err}. Rolled back transaction.",
                exc_info=False)  # exc_info=False is often enough for IntegrityError
            articles_skipped_count += len(articles_to_add)
            articles_added_count = 0
        except Exception as commit_err:
            await db.rollback()
            logger.error(f"Error during database commit: {commit_err}. Rolled back transaction.", exc_info=True)
            articles_skipped_count += len(articles_to_add)
            articles_added_count = 0
    else:
        logger.info("No new articles were prepared for addition.")

    logger.info(f"Article saving finished. Added: {articles_added_count}, Skipped: {articles_skipped_count}")
    return articles_added_count, articles_skipped_count


# === Scheduler Job Functions ===
async def process_and_save_hourly_news(db: AsyncSession):
    """
    Orchestrates the hourly task: analyze the LATEST news and save results.
    """
    logger.info("Starting hourly news processing task...")
    try:
        processed_articles_list = await analyze_news_data()  # Call analysis function
        if processed_articles_list:
            await save_processed_articles(db=db, processed_articles=processed_articles_list)
        else:
            logger.info("Hourly task: No articles processed or returned from analysis.")
        logger.info("Hourly news processing task finished.")
    except Exception as e:
        logger.error(f"Error during hourly news processing: {e}", exc_info=True)
        await db.rollback()  # Ensure rollback on failure


async def process_and_save_hourly_gnews(db: AsyncSession):
    """
    Orchestrates the hourly task: analyze the LATEST news and save results.
    """
    logger.info("Starting hourly news processing task...")
    try:
        processed_articles_list = await analyze_gnews_data()  # Call analysis function
        if processed_articles_list:
            await save_processed_gnews_articles(db=db, processed_articles=processed_articles_list)
        else:
            logger.info("Hourly task: No articles processed or returned from analysis.")
        logger.info("Hourly news processing task finished.")
    except Exception as e:
        logger.error(f"Error during hourly news processing: {e}", exc_info=True)
        await db.rollback()  # Ensure rollback on failure


async def gnews_hourly_job_wrapper():
    """Wrapper to get DB session for the hourly job."""
    logger.info("Scheduler triggered hourly job...")
    session_gen = get_db_session()  # Assumes this yields an AsyncSession
    session: AsyncSession = await session_gen.__anext__()
    try:
        await process_and_save_hourly_gnews(db=session)
    except Exception as job_err:
        logger.error(f"Hourly job execution failed. Error: {job_err}", exc_info=True)
    finally:
        try:
            await session_gen.aclose()
        except Exception as close_err:
            logger.error(f"Error closing DB session for hourly job: {close_err}", exc_info=True)


async def hourly_job_wrapper():
    """Wrapper to get DB session for the hourly job."""
    logger.info("Scheduler triggered hourly job...")
    session_gen = get_db_session()  # Assumes this yields an AsyncSession
    session: AsyncSession = await session_gen.__anext__()
    try:
        await process_and_save_hourly_news(db=session)
    except Exception as job_err:
        logger.error(f"Hourly job execution failed. Error: {job_err}", exc_info=True)
    finally:
        try:
            await session_gen.aclose()
        except Exception as close_err:
            logger.error(f"Error closing DB session for hourly job: {close_err}", exc_info=True)


# === Scheduler Setup ===

sum_scheduler = AsyncIOScheduler(timezone=timezone.utc)  # Use UTC for scheduler internal timezone


def add_jobs_to_scheduler(scheduler: AsyncIOScheduler):
    """Adds the hourly processing job and optionally daily jobs."""

    # --- Add Job (8 AM / 8 PM Eastern) ---
    target_timezone_str = 'America/New_York'  # Handles EST/EDT automatically

    # --- Add Hourly Job ---
    past_minutes = 59
    try:
        # Validate timezone
        try:
            tz = ZoneInfo(target_timezone_str)
        except ZoneInfoNotFoundError:
            logger.error(f"Invalid timezone specified: {target_timezone_str}")
            return  # Or raise an error

        scheduler.add_job(
            gnews_hourly_job_wrapper,
            trigger=CronTrigger(hour='7, 19', minute=past_minutes), # run every 12 hours
            id='hourly_article_processing',
            name='Process Recent Articles Hourly',
            replace_existing=True,
            misfire_grace_time=600,  # Allow 10 minutes grace
            timezone=target_timezone_str
        )
        logger.info(f"Successfully added HOURLY article processing job (runs HH:{past_minutes} UTC).")
    except Exception as e:
        logger.error(f"Could not add HOURLY job: {e}", exc_info=True)


# === FastAPI Lifespan Management ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles scheduler startup and shutdown with the FastAPI app lifecycle."""
    logger.info("Application startup: Parsing timezones and starting scheduler...")
    try:
        # Call the function to add jobs (currently only adds the hourly one)
        add_jobs_to_scheduler(sum_scheduler)

        # Start the scheduler
        sum_scheduler.start()
        jobs = sum_scheduler.get_jobs()
        if jobs:
            logger.info(f"Scheduler started with {len(jobs)} job(s).")
            # Optional: Log job details
            # for job in jobs: logger.info(f"  - Job ID: {job.id}, Name: {job.name}, Next Run: {job.next_run_time}")
        else:
            logger.warning("Scheduler started but no jobs are currently scheduled.")
    except Exception as e:
        logger.error(f"Error during scheduler setup or start: {e}", exc_info=True)

    yield  # Application runs here

    logger.info("Application shutdown: Shutting down scheduler...")
    try:
        sum_scheduler.shutdown()
        logger.info("Scheduler shut down gracefully.")
    except Exception as e:
        logger.error(f"Error shutting down scheduler: {e}", exc_info=True)
