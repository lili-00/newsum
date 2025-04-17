# --- Standard Library Imports ---
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, Any, Optional, List
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
from sqlalchemy.ext.asyncio import AsyncSession

# --- Application Imports (Adjust paths as necessary) ---
from app import config  # Your application config
from app.database import get_db_session  # Your DB session dependency
from app.models.models import ArticleRecord  # Your SQLAlchemy model
from app.models.summary import ArticleForProcessing  # Your Pydantic schema for validation/response

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

async def fetch_page_content(link: str) -> Optional[str]:
    """
    Fetches the HTML content of a given URL.
    Returns the text content on success, None on failure.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    timeout = httpx.Timeout(15.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        try:
            logger.info(f"Attempting to fetch content from: {link}")
            response = await client.get(link)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').lower()
            if 'html' in content_type:
                logger.info(f"Successfully fetched HTML content from: {link}")
                return response.text
            else:
                logger.warning(f"Fetched content from {link} is not HTML (type: {content_type}). Skipping.")
                return None
        except httpx.HTTPStatusError as exc:
            logger.error(f"HTTP error {exc.response.status_code} fetching {link}: {exc}")
            return None
        except httpx.RequestError as exc:
            logger.error(f"Request error fetching {link}: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Unexpected error fetching {link}: {exc}", exc_info=True)
            return None


def extract_text_from_html(html_content: str) -> str:
    """
    Extracts meaningful text content from HTML using BeautifulSoup.
    """
    if not html_content: return ""
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()
        main_content = soup.find('article') or soup.find('main') or soup.find(role='main')
        paragraphs = main_content.find_all('p') if main_content else soup.find_all('p')
        text_parts = [para.get_text(strip=True) for para in paragraphs]
        full_text = "\n".join(part for part in text_parts if part)
        full_text = full_text.replace('\xa0', ' ')
        return full_text.strip()
    except Exception as e:
        logger.error(f"Error parsing HTML with BeautifulSoup: {e}", exc_info=True)
        return ""


async def generate_summary_from_text(text_content: str, title: Optional[str] = None) -> Optional[str]:
    """
    Uses Gemini to generate a summary for the provided text content.
    """
    if not text_content or not text_content.strip():
        logger.warning("Cannot generate summary: Input text content is empty.")
        return None
    if not genai_configured:
        logger.error("Cannot generate summary: Google API Key not configured or configuration failed.")
        return None

    prompt = f"""
    Please provide a concise and neutral summary (around 200-350 words) of the following news article content. Focus on the main points and key information presented in the text.
    **Important:** The summary itself is the ONLY respond. Do not include any introductory text, explanations, markdown formatting (like ```json), or code fences before or after the JSON structure itself. The entire response must be only the summary.

    Article Title (for context, if available): {title if title else 'N/A'}

    Article Content to Summarize:
    ---
    {text_content}
    ---
    """
    try:
        logger.info(f"Generating summary with Gemini for article: {title if title else 'N/A'}")
        model = genai.GenerativeModel(MODEL_NAME)
        response = await model.generate_content_async(prompt, request_options={'timeout': 120})
        summary = response.text.strip()
        logger.info(f"Successfully generated summary for: {title if title else 'N/A'}")
        return summary
    except Exception as e:
        logger.error(f"Error calling Gemini API for summary generation (article: {title if title else 'N/A'}): {e}",
                     exc_info=True)
        return None


# === Data Fetching and Processing Functions ===

async def fetch_latest_news_data():
    """
    Fetch latest news from top resources using configured parameters.
    (Removed hours parameter)
    """
    api_params = {
        "apikey": config.NEWS_API_KEY,
        "country": config.NEWS_COUNTRY,
        "prioritydomain": config.PRIORITY_DOMAIN,
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
    Checks for existing articles based on article_id to avoid duplicates.
    (Corrected mapping from Pydantic model)
    """
    articles_added_count = 0
    articles_skipped_count = 0
    logger.info(f"Attempting to save {len(processed_articles)} processed articles to database.")

    for article_data in processed_articles:
        article_id = article_data.get("article_id")
        if not article_id:
            logger.warning("Skipping article data with missing article_id.")
            articles_skipped_count += 1
            continue

        try:
            # Check if article already exists
            stmt = select(exists().where(ArticleRecord.article_id == article_id))
            article_exists = await db.scalar(stmt)

            if article_exists:
                logger.debug(f"Article ID {article_id} already exists in DB. Skipping.")
                articles_skipped_count += 1
                continue

            # Validate data with Pydantic
            try:
                # Initialize Pydantic model using alias 'pubDate' for input string
                pydantic_article = ArticleForProcessing(
                    article_id=article_id,
                    title=article_data.get('title'),
                    reference_url=article_data.get('reference_url'),
                    description=article_data.get('description'),
                    keywords=article_data.get('keywords'),
                    summary=article_data.get('summary'),
                    source_name=article_data.get('source_name'),
                    pubDate=article_data.get('pubDate'),  # Pass input string via alias
                    summary_generated_at=article_data.get('summary_generated_at')  # Pass timestamp directly
                )
                # Create SQLAlchemy model instance using the validated Pydantic object attributes
                new_record = ArticleRecord(
                    article_id=pydantic_article.article_id,
                    title=pydantic_article.title,
                    reference_url=str(pydantic_article.reference_url) if pydantic_article.reference_url else None,
                    description=pydantic_article.description,
                    keywords=pydantic_article.keywords,
                    summary=pydantic_article.summary,
                    source_name=pydantic_article.source_name,
                    # --- CORRECTED LINE ---
                    # Use the attribute holding the parsed datetime from Pydantic
                    publication_date=pydantic_article.publication_date,
                    # --- END CORRECTION ---
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

            db.add(new_record)
            articles_added_count += 1
            logger.debug(f"Added article ID {article_id} to session.")

        except Exception as e:
            logger.error(f"Error processing article ID {article_id} for saving: {e}", exc_info=True)
            articles_skipped_count += 1

    # Commit changes if any articles were added
    if articles_added_count > 0:
        try:
            await db.commit()
            logger.info(f"Successfully saved {articles_added_count} new articles. Skipped {articles_skipped_count}.")
        except Exception as commit_err:
            logger.error(f"Database commit failed: {commit_err}", exc_info=True)
            await db.rollback()
            logger.info("Database transaction rolled back due to commit error.")
    else:
        logger.info(f"No new articles were added in this batch. Skipped {articles_skipped_count}.")


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


def add_jobs_to_scheduler(scheduler: AsyncIOScheduler, timezones: Optional[List[str]] = None):
    """Adds the hourly processing job and optionally daily jobs."""

    # --- Add Hourly Job ---
    past_minutes = 8
    try:
        scheduler.add_job(
            hourly_job_wrapper,
            trigger=CronTrigger(minute=past_minutes),  # Run at 5 minutes past every hour UTC
            id='hourly_article_processing',
            name='Process Recent Articles Hourly',
            replace_existing=True,
            misfire_grace_time=600  # Allow 10 minutes grace
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
