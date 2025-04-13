from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select  # Use select for async queries
from sqlalchemy.orm import selectinload  # If needed for relationships later

from . import auth_helper  # Import necessary modules
from ..models import user, models


async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    """Fetches a user from the database by email."""
    result = await db.execute(select(models.User).where(models.User.email == email))
    return result.scalar_one_or_none()


async def create_db_user(db: AsyncSession, user: user.EmailUserCreate) -> models.User:
    """Creates a new user in the database."""
    # Hash the password before saving
    hashed_password = auth_helper.get_password_hash(user.password)
    # Create SQLAlchemy User model instance
    db_user = models.User(email=user.email, hashed_password=hashed_password)
    # Add to session and commit
    db.add(db_user)
    await db.flush() # Flush changes to DB within the transaction
    # Refresh to get DB-generated values like ID and defaults
    await db.refresh(db_user)
    return db_user

# Add CRUD functions for Summaries here later if needed
# e.g., get_summary_by_date_period, create_summary, etc.
