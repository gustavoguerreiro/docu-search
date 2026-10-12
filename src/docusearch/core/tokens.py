"""JWT access tokens.

Access tokens are short-lived, signed with HS256 and carry the user id as `sub`. Verification pins
the algorithm and requires every claim we rely on; anything else is rejected with the same
`InvalidTokenError`, so callers cannot accidentally reveal why a token failed.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.db.ids import InvalidIdError, parse_object_id

ISSUER = "docusearch"
AUDIENCE = "docusearch-api"
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss", "aud", "jti"]


class InvalidTokenError(Exception):
    """The token is malformed, forged, wrongly scoped or otherwise unusable."""


class ExpiredTokenError(InvalidTokenError):
    """The token was valid but has expired."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: ObjectId
    issued_at: datetime
    expires_at: datetime
    token_id: str


def create_access_token(
    user_id: ObjectId, settings: Settings, *, now: datetime | None = None
) -> str:
    issued_at = now if now is not None else utc_now()
    payload = {
        "sub": str(user_id),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_ttl_minutes),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Verify signature, algorithm, expiry, issuer, audience and subject. Raises
    `InvalidTokenError` (or its subtype `ExpiredTokenError`) for every failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        # Never include the token or the library message: both can echo attacker-controlled data.
        raise InvalidTokenError("invalid token") from exc

    try:
        user_id = parse_object_id(payload["sub"])
    except (InvalidIdError, TypeError) as exc:
        raise InvalidTokenError("invalid token") from exc

    return AccessTokenClaims(
        user_id=user_id,
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        token_id=str(payload["jti"]),
    )
