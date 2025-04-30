import os
import logging
import json # **** ADDED: Import json ****
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator

# Import the connector library and auth libraries
from google.cloud.sql.connector import Connector, IPTypes
# **** ADDED: Import service_account credentials ****
from google.oauth2 import service_account
import asyncpg

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

db_user = os.environ.get("DB_USER")
db_pass = os.environ.get("DB_PASS")
db_name = os.environ.get("DB_NAME")
instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
gcp_sa_key_content = os.environ.get("GCP_SA_KEY")

# --- Validate Environment Variables ---
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

# --- Manually Create Credentials from Service Account JSON ---
try:
    # Parse the JSON string from the environment variable
    sa_info = json.loads(gcp_sa_key_content)
    # Create credentials object
    credentials = service_account.Credentials.from_service_account_info(sa_info)
    logger.info("Successfully created credentials from GCP_SA_KEY.")
except json.JSONDecodeError:
    logger.exception("FAILED to parse GCP_SA_KEY JSON content.")
    raise ValueError("Invalid JSON content in GCP_SA_KEY environment variable.")
except Exception as e:
    logger.exception(f"FAILED to create credentials from service account info: {e}")
    raise

# --- Cloud SQL Python Connector (Async Setup) ---
# **** CHANGED: Pass credentials directly to the constructor ****
try:
    connector = Connector(credentials=credentials)
    logger.info("Cloud SQL Connector initialized successfully with provided credentials.")
except Exception as e:
    logger.exception(f"FAILED to initialize Cloud SQL Connector: {e}")
    raise


# Async function to return the database connection object using asyncpg
async def getconn() -> asyncpg.Connection:
    """Creates an async connection to Cloud SQL using the connector."""
    try:
        # Credentials are now handled by the connector instance itself
        logger.info("Attempting to connect to Cloud SQL via Connector...")
        conn = await connector.connect_async(
            instance_connection_name,
            "asyncpg",
            user=db_user,
            password=db_pass,
            db=db_name,
            ip_type=IPTypes.PUBLIC,
            enable_iam_auth=False,
            # **** REMOVED: credentials_json is no longer needed here ****
        )
        logger.info("Successfully connected to Cloud SQL.")
        return conn
    except Exception as e:
        logger.exception(f"FAILED to connect to Cloud SQL: {e}")
        raise


try:
    # --- Create Async Engine using Connector ---
    ASYNC_DATABASE_URL_DIALECT_ONLY = "postgresql+asyncpg://"
    engine = create_async_engine(
        ASYNC_DATABASE_URL_DIALECT_ONLY,
        async_creator=getconn,
        echo=False,
        future=True,
        pool_recycle=1800
    )

    AsyncSessionFactory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    Base = declarative_base()
    logger.info("Async Database engine and session factory created successfully using Cloud SQL Connector.")

except Exception as e:
    logger.exception(f"FAILED to create async database engine or session factory: {e}")
    raise


# --- Async DB Session Dependency for FastAPI ---
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that yields an async SQLAlchemy session.
    Ensures the session is closed afterwards.
    """
    session: AsyncSession | None = None
    try:
        session = AsyncSessionFactory()
        yield session
    except Exception:
        logger.exception("Exception occurred during DB session usage.")
        raise
    finally:
        if session is not None:
            await session.close()

