import os
import asyncio
import logging

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

# --- Crucial: Ensure models are imported BEFORE Base is used extensively ---
# Import Base first (assuming it's defined in models/base.py)
from app.models.models import Base
# Import all modules containing models that inherit from Base
# This registers the models with Base.metadata
# --- End of crucial imports ---

# --- Configuration & Setup ---

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
load_dotenv()
logging.info("Loaded environment variables from .env file.")

# Get Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.error("FATAL: DATABASE_URL environment variable not set.")
    exit("Please set the DATABASE_URL in your .env file.")


# --- Database Initialization Function ---
async def init_database():
    """
    Connects to the database, drops all known tables (defined in models),
    and creates them anew based on the current model definitions.
    """
    logging.info(
        f"Attempting to connect to database: {DATABASE_URL.split('@')[-1]}")  # Log DB host/name, hide credentials
    try:
        # Create an async engine
        # Set echo=True to see all SQL commands executed by SQLAlchemy
        engine = create_async_engine(DATABASE_URL, echo=False)

        async with engine.begin() as conn:
            logging.info("Dropping existing tables defined in Base.metadata (if any)...")
            # !!! WARNING !!!: drop_all deletes ALL tables defined in Base.
            # Ensure Base.metadata knows about User and Summary etc. *before* dropping/creating
            # due to the imports added at the top of this script.
            await conn.run_sync(Base.metadata.drop_all)
            logging.info("Tables dropped.")

            logging.info("Creating new tables based on Base.metadata...")
            # Create all tables defined in the Base metadata
            # User and Summary tables should be created now.
            await conn.run_sync(Base.metadata.create_all)
            logging.info("Database tables created successfully.")

        # Dispose of the engine connection pool
        await engine.dispose()
        logging.info("Database connection closed.")

    except Exception as e:
        logging.error(f"An error occurred during database initialization: {e}", exc_info=True)
        exit("Database initialization failed.")


# --- Main Execution Block ---
if __name__ == "__main__":
    logging.info("Starting database initialization script (using drop_all/create_all)...")
    # Run the asynchronous initialization function
    asyncio.run(init_database())
    logging.info("Database initialization script finished.")