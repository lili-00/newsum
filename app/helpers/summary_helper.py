import logging
import os
import json
from datetime import datetime, timedelta, timezone
# Make sure List is imported from typing
from typing import Literal, Annotated, Dict, Any, Optional, List

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import google.generativeai as genai

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.models import Summary
from app import config

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

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

# --- FUNCTION MODIFIED TO RETURN LIST ---
# Update return type hint
async def generate_summary_for_past_hours(hours: int = 12) -> Optional[List[Dict[str, Any]]]:
    """
    Generates a list of news summary JSON objects for the specified number of hours
    leading up to the *current time* of execution. Each object in the list
    represents a distinct news item.
    """
    if not genai_configured:
        logger.error("Cannot generate summary: Google API Key not configured or configuration failed.")
        return None

    # Prompt requesting a JSON array
    prompt = f"""
    Search the web and analyze the most significant global news events that occurred in the past {hours} hours from now, make sure your content and url is valid.
    Focus on these categories:
    1. Major World Events (politics, conflicts, diplomacy)
    2. Technology News (major product launches, industry trends, significant research)
    3. Finance and Business (key market movements, important economic news, major corporate developments)

    Based on your analysis, identify approximately 8 distinct and significant news events from this period. For each event, create a JSON object containing the following two keys:
    1.  `summary`: A concise, neutral, and informative summary (roughly 300-500 words) specifically describing *that single news event*. Avoid speculation.
    2.  `reference_url`: A single, relevant URL link (as a string) from a reputable news source directly related to *that specific event*. If a relevant, specific URL is not readily available or applicable for that event, provide `null` for this value.

    Generate a JSON array containing these objects, with one object for each distinct news event identified.

    **Important:** Respond ONLY with the valid JSON array. Do not include any introductory text, explanations, markdown formatting (like ```json), or code fences before or after the JSON structure itself. The entire response must be parseable as a JSON array.

    Example JSON structure:
    [
      {{
        "summary": "Summary of the first significant event that occurred in the specified timeframe.",
        "reference_url": "[https://example.com/news-link-specific-to-event-1](https://example.com/news-link-specific-to-event-1)"
      }},
      {{
        "summary": "Summary of the second distinct significant event.",
        "reference_url": null
      }},
      {{
        "summary": "Description of the third key development or story.",
        "reference_url": "[https://anotherexample.org/different-story-link](https://anotherexample.org/different-story-link)"
      }}
    ]
    """

    try:
        logger.info(f"Attempting to generate JSON array summary using model: {MODEL_NAME} for the past {hours} hours.")
        model = genai.GenerativeModel(MODEL_NAME)
        response = await model.generate_content_async(prompt, request_options={'timeout': 180})

        raw_text = response.text
        logger.debug(f"Raw response text from Gemini: {raw_text}")

        try:
            cleaned_text = raw_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:-3].strip()
            elif cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:-3].strip()

            parsed_json = json.loads(cleaned_text)

            # --- MODIFIED VALIDATION ---
            # Expecting a list now
            if isinstance(parsed_json, list):
                validated_list = []
                is_valid = True
                if not parsed_json: # Handle empty list response
                    logger.warning("Gemini returned an empty JSON array.")
                    return [] # Return empty list if appropriate

                for index, item in enumerate(parsed_json):
                    # Check if each item is a dict with the required keys
                    if isinstance(item, dict) and "summary" in item and "reference_url" in item:
                        # Validate reference_url type within the item
                        ref_url = item.get("reference_url")
                        if not isinstance(ref_url, (str, type(None))):
                            logger.warning(
                                f"Item {index}: 'reference_url' has unexpected type: {type(ref_url)}. Correcting to None.")
                            item["reference_url"] = None  # Correct in place
                        validated_list.append(item) # Add valid/corrected item
                    else:
                        logger.error(
                            f"Item {index} in parsed JSON list is invalid: Not a dict or missing keys ('summary', 'reference_url'). Item: {item}")
                        is_valid = False
                        # Decide if you want to skip invalid items or fail the whole batch
                        # For now, let's fail the whole batch if any item is invalid
                        break

                if is_valid:
                    logger.info(f"JSON array with {len(validated_list)} items parsed successfully for the past {hours} hours.")
                    return validated_list # Return the list of valid items
                else:
                    logger.error("Validation failed for one or more items in the JSON array.")
                    return None # Indicate failure

            else:
                # Log error if the parsed JSON is not even a list
                logger.error(f"Parsed JSON is not a list as expected. Parsed type: {type(parsed_json)}")
                return None
            # --- END MODIFIED VALIDATION ---

        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to decode JSON response from Gemini: {json_err}. Raw text: <<< {raw_text} >>>",
                         exc_info=False)
            return None

    except Exception as e:
        logger.error(f"Error calling Gemini API for summary generation: {e}", exc_info=True)
        return None


# --- CALLING FUNCTION MODIFIED TO HANDLE LIST ---
async def generate_and_save_daily_sum(timezone_str: str, scheduled_hour: int, scheduled_minute: int, db: AsyncSession):
    """
    Generates summaries, combines them, and saves a single daily summary entry.
    """
    generation_utc_now = datetime.now(timezone.utc)
    target_date = None # Initialize target_date
    try:
        tz = ZoneInfo(timezone_str)
        now_in_tz = generation_utc_now.astimezone(tz)
        target_time_for_date = now_in_tz.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)

        if target_time_for_date > now_in_tz:
            logger.info(
                f"Scheduled time {scheduled_hour}:{scheduled_minute:02d} is in the future for {timezone_str}. Adjusting target date to yesterday.")
            target_time_for_date -= timedelta(days=1)

        target_date = target_time_for_date.date()

        logger.info(
            f"Running job for timezone {timezone_str}, scheduled for {scheduled_hour}:{scheduled_minute:02d}. Target date for record: {target_date}")

        display_hour = scheduled_hour + 1 if scheduled_minute == 55 else scheduled_hour
        period = "morning" if display_hour == 9 else "evening" if display_hour == 21 else f"{display_hour}h"
        hours_to_summarize = 12

        # --- Generate summary (now expects a list) ---
        summary_list = await generate_summary_for_past_hours(hours=hours_to_summarize)
        # --- END Generate summary ---


        # --- MODIFIED HANDLING for List ---
        # Check if the result is a list (it could be None on error, or an empty list)
        if isinstance(summary_list, list):
            if not summary_list: # Handle empty list specifically
                 logger.warning(f"Generated summary data was an empty list for {timezone_str} {period} (target date: {target_date}). Skipping save.")
                 return # Don't save anything if no news items were returned

            # Process the list: Concatenate summaries and find the first reference URL
            all_summaries = []
            first_reference_url = None
            for item in summary_list:
                item_summary = item.get("summary")
                item_url = item.get("reference_url")
                # Ensure we only add non-empty summaries
                if item_summary and isinstance(item_summary, str) and item_summary.strip():
                     all_summaries.append(item_summary.strip())
                # Take the first valid URL found
                if first_reference_url is None and item_url and isinstance(item_url, str) and item_url.strip():
                    first_reference_url = item_url

            if not all_summaries:
                logger.warning(
                    f"Generated summary list for {timezone_str} {period} (target date: {target_date}) contained no valid summary texts after processing. Skipping save.")
                return

            # Combine summaries into a single string (e.g., numbered list or newline separated)
            # Using a numbered list format:
            combined_summary_text = ""
            for i, text in enumerate(all_summaries, 1):
                combined_summary_text += f"{i}. {text}\n\n"
            combined_summary_text = combined_summary_text.strip() # Remove trailing whitespace/newlines


            # --- Database Insertion using Combined Data ---
            try:
                async with db.begin():
                    new_summary = Summary(
                        generation_timestamp=generation_utc_now,
                        period=period,
                        summary_text=combined_summary_text, # Save combined text
                        target_date=target_date,
                        reference_url=first_reference_url # Save first found URL
                    )
                    db.add(new_summary)
                    logger.info(
                        f"Added new combined {period} summary ({len(all_summaries)} items processed) for {timezone_str} (target date: {target_date}) to session.")

                logger.info(
                    f"Successfully saved combined {period} summary for {timezone_str} (target date: {target_date}) to database.")

            except Exception as db_err:
                logger.error(
                    f"Database error saving combined {period} summary for {timezone_str} (target date: {target_date}): {db_err}",
                    exc_info=True)
            # --- END Database Insertion ---

        # Handle case where generate_summary_for_past_hours returned None (error during generation/parsing)
        elif summary_list is None:
             logger.error(
                 f"Failed to generate or parse summary data for {timezone_str} {period} (target date: {target_date}). generate_summary_for_past_hours returned None.")
        # --- END MODIFIED HANDLING ---

    except ZoneInfoNotFoundError:
        logger.error(f"Invalid timezone string provided: {timezone_str}")
    except Exception as e:
        # Ensure target_date is included in the error log if available
        td_str = str(target_date) if target_date else 'unknown'
        logger.error(
            f"Error in generate_and_save_daily_sum for {timezone_str} at {scheduled_hour}:{scheduled_minute:02d}h (target date: {td_str}): {e}",
            exc_info=True)


# --- Scheduler Setup (No changes needed) ---
sum_scheduler = AsyncIOScheduler(timezone=timezone.utc)

async def scheduled_job_wrapper(timezone_str: str, scheduled_hour: int, scheduled_minute: int):
    """Wrapper to get DB session for the scheduled job."""
    logger.info(f"Scheduler triggered job for TZ: {timezone_str}, Time: {scheduled_hour}:{scheduled_minute:02d}")
    session_gen = get_db_session()
    session: AsyncSession = await session_gen.__anext__()
    try:
        await generate_and_save_daily_sum(
            timezone_str=timezone_str,
            scheduled_hour=scheduled_hour,
            scheduled_minute=scheduled_minute,
            db=session
        )
    except Exception as job_err:
         logger.error(f"Scheduled job execution failed for TZ: {timezone_str}, Time: {scheduled_hour}:{scheduled_minute:02d}. Error: {job_err}", exc_info=True)
    finally:
        # Proper cleanup depends on get_db_session implementation
        try:
             # Attempt graceful cleanup if the generator supports it
             await session_gen.aclose()
        except StopAsyncIteration:
             pass # Expected if generator finishes normally
        except Exception as close_err:
             logger.error(f"Error closing DB session generator for job {timezone_str} {scheduled_hour}:{scheduled_minute:02d}: {close_err}", exc_info=True)


# --- Add Jobs Function (No changes needed) ---
def add_jobs_to_scheduler(scheduler: AsyncIOScheduler, timezones: List[str]):
    """Adds morning and evening jobs for each specified timezone."""
    if not timezones:
        logger.warning("No target timezones provided to add_jobs_to_scheduler. No jobs will be scheduled.")
        return

    for tz_str in timezones:
        try:
            target_tz_info = ZoneInfo(tz_str)
            safe_tz_suffix = "".join(c if c.isalnum() else "_" for c in tz_str)

            scheduler.add_job(
                scheduled_job_wrapper,
                trigger=CronTrigger(hour=8, minute=55, timezone=target_tz_info),
                args=[tz_str, 8, 55], id=f'morning_summary_{safe_tz_suffix}',
                name=f'Generate Morning Summary ({tz_str} @ 8:55)',
                replace_existing=True, misfire_grace_time=300
            )
            scheduler.add_job(
                scheduled_job_wrapper,
                trigger=CronTrigger(hour=20, minute=55, timezone=target_tz_info),
                args=[tz_str, 20, 55], id=f'evening_summary_{safe_tz_suffix}',
                name=f'Generate Evening Summary ({tz_str} @ 20:55)',
                replace_existing=True, misfire_grace_time=300
            )
            logger.info(f"Successfully added morning (8:55) and evening (20:55) jobs for timezone: {tz_str}")

        except ZoneInfoNotFoundError:
            logger.error(f"Could not add jobs for timezone '{tz_str}': Timezone not found.")
        except Exception as e:
            logger.error(f"Could not add jobs for timezone '{tz_str}': {e}", exc_info=True)

# --- Lifespan Manager (No changes needed) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup: Parsing timezones and starting scheduler...")
    try:
        target_timezones_str = config.TARGET_TIMEZONES_STR
        target_timezones_list: List[str] = []
        if target_timezones_str:
            target_timezones_list = [tz.strip() for tz in target_timezones_str.split(',') if tz.strip()]
            logger.info(f"Using target timezones from config: {target_timezones_list}")
        else:
            target_timezones_list = ["America/New_York"] # Default
            logger.warning(f"TARGET_TIMEZONES not set in config, using default: {target_timezones_list}")

        add_jobs_to_scheduler(sum_scheduler, target_timezones_list)
        sum_scheduler.start()
        jobs = sum_scheduler.get_jobs()
        if jobs:
             logger.info(f"Scheduler started with {len(jobs)} job(s):")
             # Log jobs safely
        else: logger.warning("Scheduler started but no jobs are currently scheduled.")
    except Exception as e:
        logger.error(f"Error during scheduler setup or start: {e}", exc_info=True)
    yield
    logger.info("Application shutdown: Shutting down scheduler...")
    try:
        sum_scheduler.shutdown()
        logger.info("Scheduler shut down gracefully.")
    except Exception as e:
        logger.error(f"Error shutting down scheduler: {e}", exc_info=True)

# --- Example Usage (No changes needed) ---