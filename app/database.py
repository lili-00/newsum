import os
import logging
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator

# Load environment variables from .env file
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.error("FATAL: DATABASE_URL environment variable not set.")
    raise ValueError("Please set the DATABASE_URL in your .env file.")

logging.info(f"Database URL loaded: {DATABASE_URL.split('@')[-1]}")  # Log DB host/name, hide credentials

try:
    # Create the async engine
    # echo=False is recommended for production to avoid excessive logging
    engine = create_async_engine(DATABASE_URL, echo=False, future=True)

    # Create a configured "Session" class
    # expire_on_commit=False prevents attributes from being expired after commit.
    AsyncSessionFactory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Base class for declarative models
    Base = declarative_base()
    logging.info("Database engine and session factory created successfully.")

except Exception as e:
    logging.error(f"Error creating database engine or session factory: {e}")
    raise  # Re-raise the exception to prevent app startup


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that yields an async SQLAlchemy session
    with automatic transaction commit/rollback using session.begin().
    """
    async with AsyncSessionFactory() as session:
        # session.begin() starts a transaction and automatically
        # commits if the block succeeds, or rolls back if an exception occurs.
        async with session.begin():
            try:
                yield session
            except Exception:
                # The rollback is handled automatically by session.begin()
                # context manager on exception. We just need to re-raise
                # so FastAPI knows an error occurred.
                raise
        # Session is automatically closed by the outer 'async with AsyncSessionFactory()'
