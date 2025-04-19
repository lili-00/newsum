# --- Standard Library Imports ---
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, Any, Optional, List, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager
import httpx
from bs4 import BeautifulSoup
import google.generativeai as genai

from app import config

# --- Logger Setup ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # Configure basic logging if no handlers are configured elsewhere
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

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
        model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)
        response = await model.generate_content_async(prompt, request_options={'timeout': 120})
        summary = response.text.strip()
        logger.info(f"Successfully generated summary for: {title if title else 'N/A'}")
        return summary
    except Exception as e:
        logger.error(f"Error calling Gemini API for summary generation (article: {title if title else 'N/A'}): {e}",
                     exc_info=True)
        return None
