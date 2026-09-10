import hashlib
import hmac
import time
import json
import base64
from typing import Optional
from fastapi import Header, HTTPException, status
from backend.config import settings

ROLE_MOSPI_REVIEWER = "MoSPI Reviewer"
ROLE_DISTRICT_AUDITOR = "District Authority Auditor"
ROLE_PUBLIC_TIER = "Read-Only Public Tier"

VALID_ROLES = {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR, ROLE_PUBLIC_TIER}
DEFAULT_ROLE = ROLE_PUBLIC_TIER

# HMAC Secret key for signing tokens
SECRET_KEY = getattr(settings, 'SECRET_KEY', 'mplads_jan_nidhi_secret_key_2026')


def hash_password(password: str) -> str:
    """PBKDF2 password hashing."""
    salt = b"mplads_sentinel_salt"
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return pwd_hash.hex()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against PBKDF2 hash."""
    return hmac.compare_digest(hash_password(plain_password), hashed_password)


def create_access_token(username: str, role: str) -> str:
    """Generate a signed HMAC token carrying username, role, and expiration."""
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + (24 * 3600)  # 24 hour validity
    }
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode('utf-8').rstrip('=')

    signature = hmac.new(SECRET_KEY.encode('utf-8'), payload_b64.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a signed HMAC token."""
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        payload_b64, signature = parts
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), payload_b64.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None

        padding = '=' * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes.decode('utf-8'))

        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def get_current_role(
    authorization: Optional[str] = Header(default=None),
    x_user_role: Optional[str] = Header(default=None)
) -> str:
    """
    Extracts role from verified Authorization token or fallback X-User-Role header.
    Elevated roles (MoSPI Reviewer, District Auditor) require token or header assertion.
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_user_role and x_user_role.startswith("Bearer "):
        token = x_user_role[7:].strip()

    if token:
        payload = verify_token(token)
        if payload and payload.get("role") in VALID_ROLES:
            return payload["role"]

    if x_user_role and x_user_role.strip() in VALID_ROLES:
        return x_user_role.strip()

    # Fallback to public tier if no token or invalid token
    return DEFAULT_ROLE


def require_reviewer_role(
    authorization: Optional[str] = Header(default=None),
    x_user_role: Optional[str] = Header(default=None)
) -> str:
    role = get_current_role(authorization, x_user_role)
    if role not in {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Read-Only Public Tier cannot record or alter human review outcomes."
        )
    return role


def require_mospi_admin_role(
    authorization: Optional[str] = Header(default=None),
    x_user_role: Optional[str] = Header(default=None)
) -> str:
    role = get_current_role(authorization, x_user_role)
    if role != ROLE_MOSPI_REVIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Only authenticated MoSPI Reviewers can perform governance and sync operations."
        )
    return role
