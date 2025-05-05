import os
import logging
from typing import List, Optional
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


# --- Gemini Settings ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME")

if not GEMINI_API_KEY:
     logger.error("Configuration FATAL: GEMINI_API_KEY environment variable not set.")
     # Consider raising an error or handling appropriately in app startup

# --- Environment Setting --- 
# Controls which database config to use ('local' or 'production')
ENVIRONMENT: str = os.getenv("ENVIRONMENT").lower()
if ENVIRONMENT not in ['local', 'production']:
    logger.warning(f"Invalid ENVIRONMENT '{ENVIRONMENT}' specified. Defaulting to 'production'.")
    ENVIRONMENT = 'production'
logger.info(f"Configuration: Running in '{ENVIRONMENT}' environment.")

# --- Database Settings (Relevant variables read based on ENVIRONMENT later) ---
DATABASE_URL = os.getenv("DATABASE_URL") # Used for 'local'
DB_USER = os.getenv("DB_USER") # Used for 'production' (and maybe 'local' if DATABASE_URL doesn't contain it)
DB_PASS = os.getenv("DB_PASS") # Used for 'production' (and maybe 'local')
DB_NAME = os.getenv("DB_NAME") # Used for 'production' (and maybe 'local')
INSTANCE_CONNECTION_NAME = os.getenv("INSTANCE_CONNECTION_NAME") # Used for 'production'

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

# --- Apple Sign In --- 
APPLE_BUNDLE_ID: Optional[str] = os.getenv("APPLE_BUNDLE_ID")
APPLE_TEAM_ID: Optional[str] = os.getenv("APPLE_TEAM_ID")
APPLE_KEY_ID: Optional[str] = os.getenv("APPLE_KEY_ID")
APPLE_PRIVATE_KEY: Optional[str] = os.getenv("APPLE_PRIVATE_KEY")

# Validate required Apple credentials for token exchange/refresh
apple_signin_configured = True
if not all([APPLE_BUNDLE_ID, APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY]):
    logger.warning(
        "One or more Apple Sign In credentials (BUNDLE_ID, TEAM_ID, KEY_ID, PRIVATE_KEY) are missing. "
        "Token exchange and refresh will fail."
    )
    apple_signin_configured = False # Flag that full config is missing

# Note: Removed the simple APPLE_BUNDLE_ID check as the combined check above is more comprehensive
# if not APPLE_BUNDLE_ID:
#     logger.warning("APPLE_BUNDLE_ID environment variable not set. Apple Sign In verification will fail.")

