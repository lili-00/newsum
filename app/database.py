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
load_dotenv()

db_user = os.environ.get("DB_USER")
db_pass = os.environ.get("DB_PASS")
db_name = os.environ.get("DB_NAME")
instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")

# --- Validate Environment Variables ---
if not all([db_user, db_pass, db_name, instance_connection_name]):
    error_message = "FATAL: Missing one or more database environment variables (DB_USER, DB_PASS, DB_NAME, INSTANCE_CONNECTION_NAME)."
    logger.error(error_message)
    raise ValueError(error_message)

logger.info(f"Database config loaded for instance: {instance_connection_name}, db: {db_name}")

# --- Cloud SQL Python Connector (Async Setup) ---
connector = Connector()


# Async function to return the database connection object using asyncpg
async def getconn() -> asyncpg.Connection:  # Define as async function
    try:
        conn = await connector.connect_async(  # Use await with connect_async
            instance_connection_name,
            "asyncpg",  # Specify asyncpg driver
            user=db_user,
            password=db_pass,
            db=db_name,
            ip_type=IPTypes.PRIVATE,  # Connect via PRIVATE IP
            enable_iam_auth=False  # Set to True if using IAM DB Auth
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to Cloud SQL: {e}")
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
        # Add pool configurations if needed, e.g., pool_size=5
    )

    # Create a configured "AsyncSession" class
    AsyncSessionFactory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Base class for declarative models
    Base = declarative_base()
    logger.info("Async Database engine and session factory created successfully using Cloud SQL Connector.")

except Exception as e:
    logger.error(f"Error creating async database engine or session factory: {e}")
    raise


# --- Async DB Session Dependency for FastAPI ---
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that yields an async SQLAlchemy session.
    """
    async with AsyncSessionFactory() as session:
        # Using session.begin() is often good practice for transaction management
        # async with session.begin():
        #    yield session
        # If not using session.begin(), you'll manage commits/rollbacks manually
        try:
            yield session
            # If you need to commit manually (outside session.begin())
            # await session.commit()
        except Exception:
            logger.error("Exception occurred in DB session, rolling back.")
            # If you need to rollback manually (outside session.begin())
            # await session.rollback()
            raise  # Re-raise so FastAPI sees the error
