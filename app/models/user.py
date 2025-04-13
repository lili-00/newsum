import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class Token(BaseModel):
    access_token: str
    token_type: str


# Properties stored inside the JWT token payload
class TokenData(BaseModel):
    user_id: uuid.UUID
    email: Optional[EmailStr] = None


class EmailUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class EmailUserCreateResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime.date

    # Fields from schemas.Token
    access_token: str
    token_type: str = "bearer"

    # Pydantic V2 configuration (recommended)
    model_config = {
        "from_attributes": True  # Replaces orm_mode
    }
