import os
import logging
import json
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator

# --- Google Cloud SQL Specific Imports (only needed for 'production' environment) ---
from google.cloud.sql.connector import Connector, IPTypes
from google.oauth2 import service_account
import asyncpg

# --- Import App Config FIRST --- 
from app import config # Reads .env and determines ENVIRONMENT

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Define Shared Declarative Base --- 
# All models should inherit from this Base
Base = declarative_base()

# --- Initialize Variables --- 
engine = None
AsyncSessionFactory = None

# === Log the environment value right before the conditional ===
logger.info(f"DATABASE_CONFIG: Checking environment. config.ENVIRONMENT = '{config.ENVIRONMENT}'")

# === Environment-Specific Configuration ===

if config.ENVIRONMENT == 'local':
    logger.info("--- Configuring for LOCAL environment (DATABASE_URL) --- ")
    DATABASE_URL_LOCAL = config.DATABASE_URL # Use DATABASE_URL for local
    if not DATABASE_URL_LOCAL:
        logger.error("FATAL: DATABASE_URL environment variable not set for local environment.")
        raise ValueError("DATABASE_URL is required for local environment.")

    try:
        # Assume DATABASE_URL contains all necessary info (user, pass, host, db)
        engine = create_async_engine(DATABASE_URL_LOCAL, echo=False, future=True, pool_recycle=1800)
        AsyncSessionFactory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        # Log db host/name if possible, hiding credentials
        log_url_part = DATABASE_URL_LOCAL.split('@')[-1] if '@' in DATABASE_URL_LOCAL else DATABASE_URL_LOCAL
        logger.info(f"Local database configured using DATABASE_URL ending in: ...{log_url_part}")
    except Exception as e:
        logger.error(f"Error configuring local database engine/session using DATABASE_URL: {e}", exc_info=True)
        raise

elif config.ENVIRONMENT == 'production':
    logger.info("--- Configuring for PRODUCTION environment (Google Cloud SQL Connector + ADC) ---")
    # --- Load Production Environment Variables --- 
    db_user = config.DB_USER
    db_pass = config.DB_PASS
    db_name = config.DB_NAME
    instance_connection_name = config.INSTANCE_CONNECTION_NAME
    # gcp_sa_key_content is no longer needed

    # --- Validate Production Variables (excluding GCP_SA_KEY) ---
    if not all([db_user, db_pass, db_name, instance_connection_name]):
        missing_vars = [
            var_name for var_name, var_value in {
                "DB_USER": db_user, "DB_PASS": db_pass, "DB_NAME": db_name,
                "INSTANCE_CONNECTION_NAME": instance_connection_name,
                # Removed GCP_SA_KEY from check
            }.items() if not var_value
        ]
        error_message = f"FATAL: Missing environment variables for PRODUCTION (Cloud SQL) connection: {missing_vars}"
        logger.error(error_message)
        raise ValueError(error_message)

    logger.info(f"Production DB config loaded for instance: {instance_connection_name}, db: {db_name}")

    # --- Set up Cloud SQL Connector (using ADC) --- 
    try:
        # Initialize connector without explicit credentials
        # It will use Application Default Credentials when running on GCP
        connector = Connector()
        logger.info("Cloud SQL Connector initialized successfully for PRODUCTION environment (using ADC).")

        async def getconn_prod() -> asyncpg.Connection:
            # The connector instance handles authentication automatically
            conn = await connector.connect_async(
                instance_connection_name, "asyncpg", user=db_user,
                password=db_pass, db=db_name, ip_type=IPTypes.PUBLIC # Assuming Public IP, adjust if needed
                # enable_iam_auth=True # Set this if using IAM DB Authentication
            )
            return conn

        engine = create_async_engine(
            "postgresql+asyncpg://", async_creator=getconn_prod,
            echo=False, future=True, pool_recycle=1800
        )
        AsyncSessionFactory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info("PRODUCTION database configured using Cloud SQL Connector.")

    # Remove JSONDecodeError handling as it's no longer relevant
    except Exception as e:
        logger.exception(f"FAILED to configure PRODUCTION database via Cloud SQL Connector: {e}")
        raise

else:
    # This case should ideally be caught by config.py, but handle defensively
    error_message = f"FATAL: Unexpected ENVIRONMENT value in database.py: '{config.ENVIRONMENT}'. Should be 'local' or 'production'."
    logger.error(error_message)
    raise ValueError(error_message)

# === Shared DB Session Dependency ===
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function yields an async SQLAlchemy session based on the environment.
    Uses session.begin() for automatic commit/rollback.
    """
    if not AsyncSessionFactory:
         raise RuntimeError("Database session factory not initialized. Check environment configuration.")

    session: AsyncSession | None = None
    try:
        # Using session.begin() for automatic commit/rollback
        async with AsyncSessionFactory() as session:
            async with session.begin():
                yield session
    except Exception:
        # Logger adapted from original file's finally block
        logger.exception("Exception occurred during DB session usage, transaction likely rolled back by session.begin().")
        raise # Re-raise after logging
    # No explicit close needed when using AsyncSessionFactory context manager


# Make sure models import Base from here: from app.database import Base

