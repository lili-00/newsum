from datetime import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# --- Base Model ---
# Contains fields common to both creation and reading.
# Excludes generated fields (like id, created_at) and sensitive fields (like password).
class UserBase(BaseModel):
    """
    Base Pydantic model for User, containing shared fields.
    """
    email: EmailStr  # Use EmailStr for automatic email validation
    is_active: bool = True  # Default matches the SQLAlchemy model

    # Configuration to allow creating Pydantic models from ORM objects
    # (e.g., user_pydantic = UserRead.model_validate(db_user_object))
    model_config = ConfigDict(
        from_attributes=True
    )


# --- Create Model ---
# Inherits from UserBase and adds fields required only during creation.
# This model expects the PLAIN TEXT password, which you'll hash before saving.
class UserCreate(UserBase):
    """
    Pydantic model for creating a new User. Includes the plain password.
    """
    password: str  # Plain password provided by the user


# --- Update Model ---
# Model for updating an existing user. All fields are optional.
# You might choose to inherit from UserBase or define fields explicitly.
# Defining explicitly makes it clear which fields are updatable.
class UserUpdate(BaseModel):
    """
    Pydantic model for updating an existing User. All fields are optional.
    Allows updating email, password (plain text), and active status.
    """
    email: Optional[EmailStr] = None
    password: Optional[str] = None  # Allow password updates (provide plain text)
    is_active: Optional[bool] = None

    # Optional: Add config if you ever need to create this from an ORM object
    # model_config = ConfigDict(from_attributes=True)


# --- Read Model (Response Model) ---
# Inherits from UserBase and adds fields that are present when reading from DB.
# This is typically the model returned by your API endpoints.
# Crucially, it EXCLUDES the `hashed_password`.
class UserRead(UserBase):
    """
    Pydantic model for reading/returning User data (e.g., in API responses).
    Includes database-generated fields like user_id and created_at.
    Excludes sensitive fields like hashed_password.
    """
    user_id: uuid.UUID
    created_at: datetime
    # Note: hashed_password from the SQLAlchemy model is intentionally omitted here for security.


# --- Optional: Internal Model (if needed) ---
# Sometimes you might need a model that includes the hashed password for internal use,
# but be very careful not to expose this via APIs.
class UserInDB(UserRead):
    """
    Pydantic model representing a User as stored in the database,
    including the hashed password. Use with caution, avoid exposing via API.
    """
    hashed_password: str


class TokenData(BaseModel):
    access_token: str
    token_type: str


# Properties stored inside the JWT token payload
class TokenPayload(BaseModel):
    user_id: uuid.UUID
    email: Optional[EmailStr] = None


class EmailSignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class EmailSignupResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime

    token_data: TokenData

    # Fields from schemas.Token
    # access_token: str
    # token_type: str = "bearer"

    # Pydantic V2 configuration (recommended)
    model_config = {
        "from_attributes": True  # Replaces orm_mode
    }


class EmailSigninRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class EmailSigninResponse(BaseModel):
    token_data: TokenData


# --- Sign in with Apple Models ---

class AppleSignInRequest(BaseModel):
    """Data received from iOS app after Apple Sign In success."""
    authorization_code: str = Field(..., description="The authorization code from Apple.")
    # identity_token: str = Field(..., description="The JWT identity token from Apple.") # Optional if verifying id_token from token exchange
    apple_user_id: Optional[str] = Field(None, description="The stable user identifier from Apple (optional, primarily for reference).")
    email: Optional[EmailStr] = Field(None, description="Email provided by Apple (often only on first sign-in).")
    first_name: Optional[str] = Field(None, description="First name provided by Apple (often only on first sign-in).")
    last_name: Optional[str] = Field(None, description="Last name provided by Apple (often only on first sign-in).")
    # Nonce can be added here if needed for validation during token exchange
    # nonce: Optional[str] = None 

class AppleSignInResponse(BaseModel):
    """Response sent back after successful Apple Sign In/Sign Up."""
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID # Our internal user ID
    email: Optional[EmailStr] # Email stored in our DB
    is_active: bool

    model_config = ConfigDict(
        from_attributes=True
    )


class AppleRefreshResponse(BaseModel):
    """Response containing a new backend access token after Apple refresh validation."""
    access_token: str
    token_type: str = "bearer"
