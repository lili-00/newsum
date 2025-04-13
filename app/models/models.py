import uuid
from datetime import datetime, timezone, date
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, Index, UUID, Boolean
from ..database import Base


# Define the User model matching the schema
class User(Base):
    __tablename__ = "users"

    user_id = Column(UUID, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)  # Added index=True here
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f'<User {self.user_id} {self.email}>'


# Define the Summary model matching the schema
class Summary(Base):
    __tablename__ = "summaries"

    sum_id = Column(Integer, primary_key=True)
    generation_timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    period = Column(String(10), nullable=False)  # 'morning' or 'evening' or 'single'
    summary_text = Column(Text, nullable=False)
    target_date = Column(Date, nullable=False, default=date.today)
    reference_url = Column(String(2048), nullable=True)  # Single URL, nullable

    # Define index explicitly if preferred over doing it via SQL
    __table_args__ = (
        Index('idx_summaries_date_period', 'target_date', 'period'),
    )

    def __repr__(self):
        return f'<Summary {self.sum_id} {self.target_date} {self.period}>'
