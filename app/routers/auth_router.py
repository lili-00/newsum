from fastapi import APIRouter
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
import logging
from typing import Annotated
from ..helpers import auth_helper, user_helper

from sqlalchemy.sql import crud

from ..models import user
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db_session
from ..models import user
from ..models.models import User
from ..models.user import EmailUserCreateResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

# Dependency Type Hints
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
FormData = Annotated[OAuth2PasswordRequestForm, Depends()]


@router.post("/signup", response_model=EmailUserCreateResponse, status_code=status.HTTP_201_CREATED)
async def email_signup(
        user_request: user.EmailUserCreate,
        db: DbSession
):
    logger.info(f"Signup attempt for email: {user_request.email}")

    existing_user = await user_helper.get_user_by_email(db, user_request.email)

    if existing_user:
        logger.warning(f"Signup failed: Email already registered - {user_request.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered.",
        )

    # --- Start Transaction Scope (implicitly handled by get_db_session) ---
    try:
        # 1. Create user in DB *without* committing inside the helper
        #    Ensure create_db_user only does db.add() and maybe db.flush()
        #    but NOT db.commit()
        created_user = await user_helper.create_db_user(db, user_request)
        # If create_db_user raises an exception (like the bcrypt one),
        # it will be caught below, and the session handler will rollback.

        logger.info(f"User added to session: {created_user.email} (ID: {created_user.user_id})") # Log change

        # 2. Generate JWT Token
        access_token_expires = timedelta(minutes=auth_helper.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = auth_helper.create_access_token(
            data={"sub": created_user.email}, expires_delta=access_token_expires
        )
        logger.info(f"Token generated for new user: {created_user.email}")

        # 3. Prepare and Validate Response Data
        # Ensure created_user has the necessary attributes loaded.
        # If create_db_user doesn't refresh, you might need db.refresh here,
        # but usually, the session handles loading after flush/commit.
        # Let's assume created_at is available.

        # --- FIX for Pydantic Error ---
        # Option A: If EmailUserCreateResponse.created_at is datetime.datetime
        # No change needed here if the model expects datetime.

        # Option B: If EmailUserCreateResponse.created_at is datetime.date
        # created_at_value = created_user.created_at.date()

        # Let's assume your model should accept datetime (more common)
        created_at_dt = getattr(created_user, 'created_at', None)
        if not created_at_dt:
             # This case shouldn't happen if refresh worked, but handle defensively
             logger.error(f"created_at not loaded for user {created_user.email}")
             raise ValueError("User creation timestamp not available after creation.")
        created_at_value = created_at_dt.date()


        response_data = {
            "user_id": created_user.user_id,
            "email": created_user.email,
            "is_active": created_user.is_active,
            "created_at": created_at_value, # Use the potentially adjusted value
            "access_token": access_token,
            "token_type": "bearer"
        }

        # This is where the Pydantic validation happens
        response = EmailUserCreateResponse(**response_data)
        logger.info(f"Response model created for user: {created_user.email}")

        # 4. If everything above succeeded, the transaction will be committed
        #    automatically by the get_db_session dependency handler AFTER this return.
        return response

    except HTTPException:
        # If it's an HTTPException we raised intentionally (like 400 Bad Request),
        # re-raise it so FastAPI handles it correctly.
        # The session handler should still rollback.
        raise
    except Exception as e:
        # --- Explicit Rollback ---
        logger.error(f"Error during signup process for {user_request.email}, rolling back: {e}", exc_info=True)
        await db.rollback() # Explicitly rollback DB changes before raising HTTP error
        # --- Raise a generic server error ---
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create user account due to an internal server error."
        )
