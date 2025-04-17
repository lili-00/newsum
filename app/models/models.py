# models.py (or wherever ArticleRecord is defined)

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, UUID, Boolean, JSON, func
# from ..database import Base # Assuming Base is defined correctly elsewhere
from sqlalchemy.orm import declarative_base # Or import your actual Base

# Define a Base class for declarative models (if not imported)
Base = declarative_base()

# --- User model definition (if in the same file) ---
class User(Base):
    __tablename__ = "users"
    user_id = Column(UUID, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.utcnow()) # Use utcnow for consistency
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f'<User {self.user_id} {self.email}>'


# --- Corrected ArticleRecord Model (Merged) ---
class ArticleRecord(Base):
    """
    SQLAlchemy ORM model representing a processed news article record,
    including summary generation details.
    (Corrected index definition)
    """
    __tablename__ = 'processed_articles'

    # --- Core Article Fields ---
    id = Column(Integer, primary_key=True)
    article_id = Column(String, unique=True, index=True, nullable=False) # Keep index=True here for article_id
    title = Column(String, nullable=True)
    reference_url = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    keywords = Column(JSON, nullable=True)
    source_name = Column(String, nullable=True)
    # CORRECTED: REMOVED index=True from this line:
    publication_date = Column(DateTime(timezone=True), nullable=True)

    # --- Summary Fields ---
    summary = Column(Text, nullable=True)
    summary_generated_at = Column(DateTime(timezone=True), nullable=True)

    # --- Record Tracking Timestamps ---
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # --- Indexes (Defined ONLY here now) ---
    __table_args__ = (
        Index('ix_processed_articles_publication_date', 'publication_date'), # Explicit index definition
        Index('ix_processed_articles_summary_generated_at', 'summary_generated_at'),
    )

    def __repr__(self):
        return f"<ArticleRecord(id={self.id}, article_id='{self.article_id}', title='{self.title[:30]}...')>"