import os
import logging
from typing import List
from dotenv import load_dotenv

# Configure logging for this module
logger = logging.getLogger(__name__)
# Basic config if not configured elsewhere
if not logger.hasHandlers():
     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
# It's often good practice to call load_dotenv() early, maybe in main.py or here.
# If called in multiple places, subsequent calls are usually no-ops.
load_dotenv()
logger.info("Configuration: Loading environment variables.")

# --- Scheduler Settings ---

# Load target timezones string directly from environment variable
# The consuming module (e.g., scheduler) will handle parsing and defaults.
TARGET_TIMEZONES_STR = os.getenv("TARGET_TIMEZONES") # Reads the raw string or None

if TARGET_TIMEZONES_STR:
    logger.info(f"Loaded TARGET_TIMEZONES string from environment: '{TARGET_TIMEZONES_STR}'")
else:
    logger.warning("TARGET_TIMEZONES environment variable not set or empty. Default will be applied by consumer if needed.")


# --- Gemini Settings ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME")

if not GEMINI_API_KEY:
     logger.error("Configuration FATAL: GEMINI_API_KEY environment variable not set.")
     # Consider raising an error or handling appropriately in app startup

# --- Database Settings ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Configuration FATAL: DATABASE_URL environment variable not set.")
    # Consider raising an error

# --- JWT Settings ---
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change_this_super_secret_key_in_production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

# --- News data api ---
NEWS_API_URL = os.getenv("NEWS_API_URL")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
NEWS_COUNTRY = os.getenv("NEWS_COUNTRY")
PRIORITY_DOMAIN = os.getenv("PRIORITY_DOMAIN")


# --- GNEWS API ---
GNEWS_API_URL = os.getenv("GNEWS_API_URL")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
GNEWS_COUNTRY = os.getenv("GNEWS_COUNTRY")

