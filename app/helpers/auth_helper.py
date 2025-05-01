import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated
import uuid # Import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer  # Handles extracting token from header

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ValidationError # Import BaseModel
from dotenv import load_dotenv

from app import models
from app.database import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession
from . import user_helper # <-- Import user_helper

# Load environment variables
load_dotenv()

# --- Configuration ---
# !! CHANGE THIS IN PRODUCTION AND KEEP IT SECRET !!
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))  # Default 30 mins

# --- Password Hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hashes a plain password using bcrypt."""
    return pwd_context.hash(password)


# --- JWT Token Handling ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # Default expiration time
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- Pydantic model for JWT payload ---
class TokenPayload(BaseModel):
    sub: uuid.UUID # Expect 'sub' claim to be a UUID

# --- OAuth2 Scheme ---
# This tells FastAPI how to find the token (in Authorization header as Bearer token)
# tokenUrl should point to your actual token endpoint (relative path)
# Corrected tokenUrl based on router prefix
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/signin")


# --- Dependency to get current user ---
async def get_current_user(
        token: Annotated[str, Depends(oauth2_scheme)],  # Extracts token from header
        db: Annotated[AsyncSession, Depends(get_db_session)]  # Gets DB session
) -> models.User:
    """
    Dependency to verify JWT token and return the current user based on user_id.
    Raises HTTPException if token is invalid or user not found.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Decode the JWT token
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        
        # Extract user_id string from 'sub' claim
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        
        # Validate the payload using the TokenPayload schema
        # This implicitly converts the user_id_str to UUID and validates format
        token_data = TokenPayload(sub=user_id_str)

    except (JWTError, ValidationError, ValueError) as e: # Catch potential UUID conversion error
        # Handle errors during decoding, Pydantic validation, or UUID conversion
        print(f"Token validation error: {e}") # Add logging
        raise credentials_exception

    # Get the user from the database based on the user_id in the token
    user = await user_helper.get_user_by_id(db, user_id=token_data.sub)
    if user is None:
        raise credentials_exception
    # Optional: Check if user is active (done by get_current_active_user)
    return user


# Dependency for getting the currently active user (optional, combines above checks)
async def get_current_active_user(
        current_user: Annotated[models.User, Depends(get_current_user)]
) -> models.User:
    """Dependency to get the current active user."""
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")
    return current_user
