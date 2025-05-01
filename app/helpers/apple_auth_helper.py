import logging
import time
from typing import Dict, Any, Optional

import httpx
from jose import jwt, jwk
from jose.exceptions import JOSEError

from app import config # For APPLE_BUNDLE_ID
from fastapi import HTTPException, status
import datetime

logger = logging.getLogger(__name__)

# --- Apple Public Key Caching --- 
APPLE_PUBLIC_KEYS_URL = "https://appleid.apple.com/auth/keys"
# Simple in-memory cache. Consider Redis/Memcached for production.
_apple_public_keys: Optional[Dict[str, Any]] = None
_apple_keys_last_fetched: float = 0
_apple_keys_cache_ttl: int = 3600 # Cache for 1 hour

async def _get_apple_public_keys() -> Dict[str, Any]:
    """Fetches Apple's public keys, with simple caching."""
    global _apple_public_keys, _apple_keys_last_fetched
    now = time.time()
    if _apple_public_keys and (now - _apple_keys_last_fetched < _apple_keys_cache_ttl):
        logger.debug("Using cached Apple public keys.")
        return _apple_public_keys

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(APPLE_PUBLIC_KEYS_URL)
            response.raise_for_status()
            jwks = response.json()
            _apple_public_keys = {key['kid']: key for key in jwks.get('keys', [])}
            _apple_keys_last_fetched = now
            logger.info(f"Fetched and cached Apple public keys (found {len(_apple_public_keys)} keys).")
            return _apple_public_keys
    except Exception as e:
        logger.error(f"Failed to fetch or cache Apple public keys: {e}", exc_info=True)
        # If cache exists but is stale, return stale keys? Or raise? Let's raise for now.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not fetch Apple public keys for verification."
        )


async def verify_apple_identity_token(
    token: str, 
    access_token: Optional[str] = None,
    expected_nonce: Optional[str] = None
) -> str:
    """
    Verifies the Apple identity token.

    Args:
        token: The identity token string (id_token from Apple).
        access_token: The access token issued by Apple alongside the id_token.
        expected_nonce: The nonce the client should have sent (optional).

    Returns:
        The verified Apple User ID (subject claim).

    Raises:
        HTTPException: If verification fails.
    """
    try:
        # 1. Decode header to get Key ID (kid)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get('kid')
        if not kid:
            raise HTTPException(status_code=401, detail="Token missing key ID (kid).")

        # 2. Fetch Apple Public Keys
        apple_keys = await _get_apple_public_keys()
        public_key_data = apple_keys.get(kid)
        if not public_key_data:
            # Maybe keys rotated? Force refresh cache once.
            logger.warning(f"Key ID '{kid}' not found in cached Apple keys. Forcing refresh.")
            global _apple_keys_last_fetched
            _apple_keys_last_fetched = 0 # Invalidate cache
            apple_keys = await _get_apple_public_keys()
            public_key_data = apple_keys.get(kid)
            if not public_key_data:
                 raise HTTPException(status_code=401, detail=f"Key ID '{kid}' not found in Apple public keys.")

        # 3. Construct public key from JWK data
        public_key = jwk.construct(public_key_data)

        # 4. Decode and Verify Token
        audience = config.APPLE_BUNDLE_ID
        issuer = "https://appleid.apple.com"

        payload = jwt.decode(
            token,
            public_key.to_pem().decode('utf-8'),
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            access_token=access_token
        )

        # 5. Validate Claims
        apple_user_id = payload.get('sub')
        if not apple_user_id:
            raise HTTPException(status_code=401, detail="Token 'sub' (user ID) claim missing.")

        # Optional: Nonce validation (requires client to send nonce)
        if expected_nonce:
            token_nonce = payload.get('nonce')
            if not token_nonce:
                raise HTTPException(status_code=401, detail="Token missing 'nonce' claim.")
            # Remember: Compare against the HASH of the nonce if client hashes it
            if token_nonce != expected_nonce: # Adjust if client sends hash
                raise HTTPException(status_code=401, detail="Invalid 'nonce'.")

        logger.info(f"Successfully verified Apple identity token for user: {apple_user_id}")
        return apple_user_id

    except jwt.ExpiredSignatureError:
        logger.warning("Apple identity token has expired.")
        raise HTTPException(status_code=401, detail="Apple token expired.")
    except jwt.JWTClaimsError as e:
        logger.warning(f"Apple identity token claims error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token claims: {e}")
    except JOSEError as e:
        logger.error(f"JOSE error verifying Apple token: {e}", exc_info=True)
        raise HTTPException(status_code=401, detail="Invalid Apple token signature or structure.")
    except HTTPException as http_exc: # Re-raise specific HTTP exceptions
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error verifying Apple token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not verify Apple token.") 


def _generate_apple_client_secret() -> str:
    """
    Generates the client secret JWT used to authenticate with Apple's services.
    Requires APPLE_TEAM_ID, APPLE_BUNDLE_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY in config.
    """
    if not config.apple_signin_configured:
        raise RuntimeError("Apple Sign In credentials not fully configured for client secret generation.")

    now = datetime.datetime.now(datetime.timezone.utc)
    expire = now + datetime.timedelta(minutes=10) # Recommended max expiration is 6 months, but shorter is safer for this use

    headers = {
        "kid": config.APPLE_KEY_ID,
        "alg": "ES256" # Apple uses ES256
    }

    payload = {
        "iss": config.APPLE_TEAM_ID,
        "iat": now,
        "exp": expire,
        "aud": "https://appleid.apple.com",
        "sub": config.APPLE_BUNDLE_ID
    }

    try:
        # Ensure private key is in the correct PEM format (with newlines)
        # --- Debugging Start --- 
        raw_key_from_config = config.APPLE_PRIVATE_KEY
        if not raw_key_from_config or not isinstance(raw_key_from_config, str):
            logger.error("APPLE_PRIVATE_KEY is missing or not a string in config!")
            raise RuntimeError("APPLE_PRIVATE_KEY configuration error.")
            
        logger.debug(f"Raw APPLE_PRIVATE_KEY from config (repr): {repr(raw_key_from_config)}")
        # Reinstate the replace call
        private_key_pem = raw_key_from_config.replace("\\n", "\n") 
        logger.debug(f"Processed private_key_pem for jwt.encode (repr): {repr(private_key_pem)}")
        # --- Debugging End ---

        client_secret = jwt.encode(
            payload,
            private_key_pem, # Using processed key
            algorithm="ES256",
            headers=headers
        )
        logger.debug("Generated Apple client secret JWT.")
        return client_secret
    except Exception as e:
        logger.error(f"Failed to generate Apple client secret: {e}", exc_info=True)
        # Don't raise HTTP directly here, let caller handle failure
        raise RuntimeError("Could not generate Apple client secret.") from e


async def exchange_apple_code_for_tokens(code: str, grant_type: str = "authorization_code", refresh_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Exchanges an authorization code or refresh token with Apple for access/id/refresh tokens.

    Args:
        code: The authorization code (if grant_type is authorization_code).
        grant_type: Either 'authorization_code' or 'refresh_token'.
        refresh_token: The refresh token (if grant_type is refresh_token).

    Returns:
        A dictionary containing the token response from Apple.

    Raises:
        HTTPException: If the exchange fails.
    """
    if not config.apple_signin_configured:
         raise HTTPException(status_code=500, detail="Apple Sign In not configured on server.")

    client_secret = _generate_apple_client_secret() # Can raise RuntimeError

    token_url = "https://appleid.apple.com/auth/token"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'client_id': config.APPLE_BUNDLE_ID,
        'client_secret': client_secret,
        'grant_type': grant_type,
    }
    if grant_type == "authorization_code":
        data['code'] = code
        # Optional: Add redirect_uri if it was used during the initial auth request
        # data['redirect_uri'] = "YOUR_REDIRECT_URI"
    elif grant_type == "refresh_token":
        if not refresh_token:
            raise ValueError("refresh_token is required for grant_type 'refresh_token'")
        data['refresh_token'] = refresh_token
    else:
        raise ValueError(f"Invalid grant_type: {grant_type}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, headers=headers, data=data)
            # Apple returns errors with 200 OK sometimes, check body
            response_data = response.json()
            if response.status_code >= 400 or 'error' in response_data:
                error = response_data.get('error', 'unknown_error')
                error_desc = response_data.get('error_description', 'No description provided.')
                logger.error(f"Apple token exchange failed (Status: {response.status_code}, Error: {error}): {error_desc}")
                # Map common errors to specific HTTP statuses if needed
                if error == "invalid_grant": # e.g., expired code, bad refresh token
                    raise HTTPException(status_code=401, detail=f"Invalid Apple grant: {error_desc}")
                elif error == "invalid_client": # e.g., bad client secret
                     raise HTTPException(status_code=401, detail="Invalid Apple client configuration.")
                else:
                    raise HTTPException(status_code=400, detail=f"Apple token exchange error: {error}")

            logger.info(f"Successfully exchanged Apple {grant_type}."
                        f" Received keys: {list(response_data.keys())}")
            return response_data

    except HTTPException as http_exc:
        raise http_exc # Re-raise specific exceptions
    except Exception as e:
        logger.error(f"Unexpected error during Apple token exchange: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to communicate with Apple services.") 