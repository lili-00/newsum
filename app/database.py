import os
import logging
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator

# Import the connector library
from google.cloud.sql.connector import Connector, IPTypes
import asyncpg  # Ensure asyncpg is imported if needed by driver string

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
# load_dotenv() is useful locally, but Heroku uses Config Vars directly
# It's generally safe to leave load_dotenv() - it won't overwrite existing env vars
load_dotenv()

db_user = os.environ.get("DB_USER")
db_pass = os.environ.get("DB_PASS")
db_name = os.environ.get("DB_NAME")
instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
# --- ADDED: Read the service account key content ---
gcp_sa_key_content = os.environ.get("GCP_SA_KEY")

# --- Validate Environment Variables ---
# --- ADDED: Validation for GCP_SA_KEY ---
if not all([db_user, db_pass, db_name, instance_connection_name, gcp_sa_key_content]):
    missing_vars = [
        var_name for var_name, var_value in {
            "DB_USER": db_user, "DB_PASS": db_pass, "DB_NAME": db_name,
            "INSTANCE_CONNECTION_NAME": instance_connection_name,
            "GCP_SA_KEY": gcp_sa_key_content
        }.items() if not var_value
    ]
    error_message = f"FATAL: Missing one or more environment variables: {', '.join(missing_vars)}."
    logger.error(error_message)
    raise ValueError(error_message)

logger.info(f"Database config loaded for instance: {instance_connection_name}, db: {db_name}")

# --- Cloud SQL Python Connector (Async Setup) ---
# No need to pass credentials during initialization here
connector = Connector()


# Async function to return the database connection object using asyncpg
async def getconn() -> asyncpg.Connection:  # Define as async function
    """Creates an async connection to Cloud SQL using the connector."""
    try:
        logger.info("Attempting to connect to Cloud SQL via Connector...")
        conn = await connector.connect_async(  # Use await with connect_async
            instance_connection_name,
            "asyncpg",  # Specify asyncpg driver
            user=db_user,
            password=db_pass,
            db=db_name,
            # --- CHANGED: Use PUBLIC IP unless VPC peering is configured ---
            ip_type=IPTypes.PUBLIC,
            enable_iam_auth=False,  # Set to True if using IAM DB Auth instead of password
            # --- ADDED: Pass the service account key content ---
            credentials_json=gcp_sa_key_content
        )
        logger.info("Successfully connected to Cloud SQL.")
        return conn
    except Exception as e:
        # Log the specific error during connection attempt
        logger.exception(f"FAILED to connect to Cloud SQL: {e}") # Use logger.exception to include traceback
        raise


try:
    # --- Create Async Engine using Connector ---
    # The DATABASE_URL string only informs SQLAlchemy about the dialect.
    # Host/port/user/pass/db are ignored because async_creator is used.
    ASYNC_DATABASE_URL_DIALECT_ONLY = "postgresql+asyncpg://"

    engine = create_async_engine(
        ASYNC_DATABASE_URL_DIALECT_ONLY,
        async_creator=getconn,  # Use async_creator with our async getconn function
        echo=False,  # Set to True for debugging SQL (verbose!)
        future=True,
        # Add pool configurations if needed, e.g., pool_size=5, max_overflow=10
        # Consider pool_recycle to handle potential connection drops
        pool_recycle=1800 # Recycle connections every 30 minutes (adjust as needed)
    )

    # Create a configured "AsyncSession" class
    AsyncSessionFactory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Base class for declarative models
    Base = declarative_base()
    logger.info("Async Database engine and session factory created successfully using Cloud SQL Connector.")

except Exception as e:
    # Log the specific error during engine/session creation
    logger.exception(f"FAILED to create async database engine or session factory: {e}")
    raise


# --- Async DB Session Dependency for FastAPI ---
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that yields an async SQLAlchemy session.
    Ensures the session is closed afterwards.
    """
    session: AsyncSession | None = None # Initialize to None
    try:
        session = AsyncSessionFactory()
        yield session
    except Exception:
        # Log error if yielding session fails or during session usage
        logger.exception("Exception occurred during DB session usage.")
        # Optional: await session.rollback() if needed, depends on transaction handling
        raise # Re-raise so FastAPI sees the error
    finally:
        # Ensure session is always closed if it was successfully created
        if session is not None:
            await session.close()
            # logger.debug("DB Session closed.") # Optional debug logging

