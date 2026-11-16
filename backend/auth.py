
from pwdlib import PasswordHash
from jose import jwt
from jose.exceptions import JWTError
from datetime import datetime, timedelta, timezone
import os

# Password hashing configuration
password_hash = PasswordHash.recommended()

APP_ENV = os.getenv("CRYSTALSCOUT_ENV", "production").strip().lower()
SECRET_KEY = os.getenv("CRYSTALSCOUT_SECRET_KEY", "").strip()
if SECRET_KEY:
    lowered_secret = SECRET_KEY.lower().replace("_", "-")
    if (
        len(SECRET_KEY) < 32
        or lowered_secret.startswith(("replace-", "generate-", "change-me", "your-"))
    ):
        raise RuntimeError(
            "CRYSTALSCOUT_SECRET_KEY must be a genuine, non-placeholder value of at least 32 characters."
        )
else:
    if APP_ENV == "development":
        # Local-only fallback. Production must set CRYSTALSCOUT_SECRET_KEY.
        SECRET_KEY = "crystalscout-local-development-only-key"
    else:
        raise RuntimeError("CRYSTALSCOUT_SECRET_KEY must be set outside development.")

ALGORITHM = "HS256"


# Hash a password
def hash_password(password):
    return password_hash.hash(password)


# Verify a password
def verify_password(password, hashed_password):
    return password_hash.verify(password, hashed_password)


# Create login token
def create_access_token(user_id):
    expiration = datetime.now(timezone.utc) + timedelta(hours=2)

    data = {
        "sub": str(user_id),
        "exp": expiration
    }

    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def read_access_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return int(user_id)
    except (JWTError, TypeError, ValueError):
        return None
