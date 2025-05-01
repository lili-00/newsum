from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select  # Use select for async queries
from sqlalchemy.orm import selectinload  # If needed for relationships later
import random
import string
import uuid # Add uuid import

from . import auth_helper  # Import necessary modules
from ..models import user, models


async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    """Fetches a user from the database by email."""
    result = await db.execute(select(models.User).where(models.User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> models.User | None:
    """Fetches a user from the database by their UUID."""
    result = await db.execute(select(models.User).where(models.User.user_id == user_id))
    return result.scalar_one_or_none()


async def create_db_user(db: AsyncSession, user: user.EmailSignupRequest) -> models.User:
    """Creates a new user in the database with email/password."""
    hashed_password = auth_helper.get_password_hash(user.password)
    generated_username = generate_random_username() # Generate username
    db_user = models.User(
        username=generated_username, # Assign username
        email=user.email, 
        hashed_password=hashed_password
    )
    # Add to session and commit
    db.add(db_user)
    # NOTE: If username collision occurs, flush() will raise IntegrityError
    # The transaction rollback is handled by the get_db_session dependency.
    await db.flush() 
    await db.refresh(db_user)
    return db_user


async def get_user_by_apple_id(db: AsyncSession, apple_id: str) -> models.User | None:
    """Fetches a user from the database by their Apple User ID."""
    result = await db.execute(
        select(models.User).where(models.User.apple_user_id == apple_id)
    )
    return result.scalar_one_or_none()


def generate_random_username() -> str:
    """Generates a username like 'user123456'."""
    digits = "".join(random.choices(string.digits, k=6))
    return f"user{digits}"

# Add CRUD functions for Summaries here later if needed
# e.g., get_summary_by_date_period, create_summary, etc.
