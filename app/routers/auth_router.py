import logging
from typing import Annotated
from ..helpers import auth_helper, user_helper, apple_auth_helper
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db_session
from ..models import user
from ..models.models import User
from ..models.user import EmailSignupResponse, EmailSignupRequest, EmailSigninRequest, EmailSigninResponse, UserInDB, AppleSignInRequest, AppleSignInResponse, AppleRefreshResponse, UserRead
from ..helpers.auth_helper import get_current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"]
)

# Dependency Type Hints
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
FormData = Annotated[OAuth2PasswordRequestForm, Depends()]


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def email_signup(
        user_request: user.EmailSignupRequest,
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

        logger.info(f"User added to session: {created_user.email} (ID: {created_user.user_id})")  # Log change

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
            "created_at": created_at_value,  # Use the potentially adjusted value
            "token_data": {
                "access_token": access_token,
                "token_type": "bearer"
            }
        }

        # This is where the Pydantic validation happens
        response = EmailSignupResponse(**response_data)
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
        await db.rollback()  # Explicitly rollback DB changes before raising HTTP error
        # --- Raise a generic server error ---
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create user account due to an internal server error."
        )


@router.post("/signin", status_code=status.HTTP_200_OK)
async def email_signin(
        signin_request: EmailSigninRequest,
        db: DbSession
):
    try:
        existing_user = await user_helper.get_user_by_email(db, signin_request.email)
        logger.info(f"User found in signin is {existing_user}")
        if not existing_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during signin process for {signin_request.email}, {e}", exc_info=True)

    is_correct_pwd = auth_helper.verify_password(signin_request.password, existing_user.hashed_password)

    if not is_correct_pwd:
        raise HTTPException(
            status_code=400,
            detail="Password is incorrect"
        )

    # Use USER_ID as the token subject
    token_payload_data = {"sub": str(existing_user.user_id)} 
    access_token = auth_helper.create_access_token(data=token_payload_data)

    # Prepare the dictionary for the response
    # Ensure its structure matches the EmailSigninResponse model
    response_dict = {
        "token_data": {  # This structure must match the TokenData model within EmailSigninResponse
            "access_token": access_token,
            "token_type": "bearer"
        }
    }

    # Return the dictionary directly
    return response_dict


@router.post("/apple/callback", response_model=AppleSignInResponse, status_code=status.HTTP_200_OK)
async def sign_in_with_apple(
    request_data: AppleSignInRequest,
    db: DbSession
):
    """Handles Sign in with Apple callback (using authorization code) from the iOS client."""
    logger.info(f"Apple Sign In code exchange attempt received.")

    try:
        # 1. Exchange the authorization code for tokens
        apple_tokens = await apple_auth_helper.exchange_apple_code_for_tokens(
            code=request_data.authorization_code,
            grant_type="authorization_code"
        )
        
        # Extract the id_token and refresh_token (if present)
        id_token = apple_tokens.get('id_token')
        apple_refresh_token = apple_tokens.get('refresh_token') # Store this!
        apple_access_token = apple_tokens.get('access_token') # Get Apple's access token

        if not id_token:
            logger.error("Apple token exchange response missing id_token.")
            raise HTTPException(status_code=500, detail="Failed to retrieve required token from Apple.")

        # 2. Verify the id_token received from Apple's server
        # Pass the corresponding access_token for at_hash validation
        verified_apple_user_id = await apple_auth_helper.verify_apple_identity_token(
            token=id_token,
            access_token=apple_access_token, # Pass the access token
            expected_nonce=None # Add nonce if using
        )

        # Optional Sanity check: Compare verified ID with ID sent by the client IF it was sent
        if request_data.apple_user_id and (verified_apple_user_id != request_data.apple_user_id):
            logger.error(
                f"Apple User ID mismatch: Token sub ('{verified_apple_user_id}') != Request body ('{request_data.apple_user_id}')"
            )
            # Decide how critical this mismatch is - maybe just log a warning
            # raise HTTPException(status_code=401, detail="Apple user ID mismatch.")

        # 3. Check if user exists by Apple User ID
        existing_user = await user_helper.get_user_by_apple_id(db, verified_apple_user_id)

        if existing_user:
            # --- User Found (Sign In) ---
            logger.info(f"Existing user found for Apple ID. Signing in User ID: {existing_user.user_id}")
            # Update refresh token if a new one was provided (though usually not for subsequent logins)
            if apple_refresh_token and existing_user.apple_refresh_token != apple_refresh_token:
                logger.info(f"Updating Apple refresh token for user {existing_user.user_id}")
                existing_user.apple_refresh_token = apple_refresh_token
                db.add(existing_user) # Mark for update
            user_to_return = existing_user
        else:
            # --- User Not Found (Sign Up) ---
            logger.info(f"No existing user for Apple ID '{verified_apple_user_id}'. Creating new user.")
            
            signup_email = request_data.email 
            
            if signup_email:
                email_user = await user_helper.get_user_by_email(db, signup_email)
                if email_user:
                    logger.warning(f"Apple sign up attempt with existing email: {signup_email}")
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"An account with email {signup_email} already exists."
                    )
            
            # Generate username
            generated_username = user_helper.generate_random_username()
            
            # Create new user record
            new_user_data = User(
                username=generated_username, # Assign username
                apple_user_id=verified_apple_user_id,
                apple_refresh_token=apple_refresh_token,
                email=signup_email, 
                hashed_password=None,
                is_active=True
            )
            db.add(new_user_data)
            # NOTE: If username collision occurs, flush() will raise IntegrityError
            # The transaction rollback is handled by the get_db_session dependency.
            await db.flush()
            await db.refresh(new_user_data)
            logger.info(f"New user created with User ID: {new_user_data.user_id}, Username: {generated_username} for Apple ID.")
            user_to_return = new_user_data

        # 4. Generate Backend Access Token for OUR service
        access_token_expires = timedelta(minutes=auth_helper.ACCESS_TOKEN_EXPIRE_MINUTES)
        # Use USER_ID as the token subject
        access_token = auth_helper.create_access_token(
            data={"sub": str(user_to_return.user_id)}, 
            expires_delta=access_token_expires
        )

        # 5. Prepare and Return Response
        return AppleSignInResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=user_to_return.user_id,
            username=user_to_return.username, # Added username
            email=user_to_return.email, 
            is_active=user_to_return.is_active
        )

    except HTTPException as http_exc:
        # Re-raise HTTPExceptions (like 401, 409, 500 from helpers)
        raise http_exc
    except Exception as e:
        logger.error(f"Error during Apple Sign In code exchange process: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during Apple Sign In."
        )


# === Refresh Apple Session (Generates New Backend Token) ===
@router.post("/apple/refresh", response_model=AppleRefreshResponse, status_code=status.HTTP_200_OK)
async def refresh_apple_session(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: DbSession
):
    """
    Validates the stored Apple refresh token and issues a new backend access token.
    Requires user to be authenticated with a valid backend JWT.
    """
    logger.info(f"Attempting Apple session refresh for user ID: {current_user.user_id}")

    # 1. Check if user signed in with Apple and has a refresh token
    if not current_user.apple_user_id or not current_user.apple_refresh_token:
        logger.warning(f"User {current_user.user_id} attempted refresh without Apple linkage or token.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not linked with Apple Sign In or refresh token is missing."
        )

    try:
        # 2. Attempt to validate the refresh token with Apple
        # This implicitly checks if the user's Apple session is still valid
        # We don't necessarily need the response dict unless we want to update
        # Apple access/id tokens, which isn't the primary goal here.
        _ = await apple_auth_helper.exchange_apple_code_for_tokens(
            code="", # Not used for refresh
            grant_type="refresh_token",
            refresh_token=current_user.apple_refresh_token
        )
        # If the above call succeeds without HTTPException, the refresh token is valid.
        logger.info(f"Apple refresh token validated successfully for user ID: {current_user.user_id}")

        # 3. Generate a NEW backend access token
        access_token_expires = timedelta(minutes=auth_helper.ACCESS_TOKEN_EXPIRE_MINUTES)
        new_backend_access_token = auth_helper.create_access_token(
            data={"sub": str(current_user.user_id)}, # Use our internal user_id
            expires_delta=access_token_expires
        )
        logger.info(f"Issued new backend access token for user ID: {current_user.user_id}")

        # 4. Return the new backend token
        return AppleRefreshResponse(
            access_token=new_backend_access_token,
            token_type="bearer"
        )

    except HTTPException as http_exc:
        # Handle specific errors from token exchange (e.g., invalid_grant -> 401)
        if http_exc.status_code == 401:
            logger.warning(f"Apple refresh token invalid for user ID: {current_user.user_id}. Detail: {http_exc.detail}")
            # Could potentially revoke the stored refresh token here
            # current_user.apple_refresh_token = None
            # db.add(current_user)
            # await db.commit() # Or handle within transaction
        # Re-raise the exception (or a generic one)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, # Indicate backend session needs refresh
            detail=f"Apple session validation failed: {http_exc.detail}"
        ) from http_exc
    except Exception as e:
        logger.error(f"Unexpected error during Apple session refresh for user ID {current_user.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during session refresh."
        )

# === Get Current User Profile ===
@router.get("/me", response_model=UserRead, status_code=status.HTTP_200_OK)
async def get_current_user_profile(
    current_user: Annotated[User, Depends(get_current_active_user)] # Require auth & get user
):
    """
    Fetches the profile information for the currently authenticated user.
    """
    logger.info(f"Fetching profile for user ID: {current_user.user_id}")
    # The dependency already provides the user object loaded from the database.
    # FastAPI will automatically serialize it using the UserRead response_model.
    return current_user
