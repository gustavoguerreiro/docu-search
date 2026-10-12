"""Authentication use cases: register and log in.

Two properties are deliberate:
- `register` does the same work and gives the same outcome whether or not the email exists, so it
  cannot be used to discover which emails have accounts.
- `login` spends the same password-verification time for an unknown email as for a wrong password,
  and raises the same error for both.
"""

import logging
import secrets
from dataclasses import dataclass, field

from docusearch.auth.refresh_tokens import RefreshTokenService
from docusearch.core.passwords import hash_password, verify_password
from docusearch.core.settings import Settings
from docusearch.core.tokens import create_access_token
from docusearch.db.repositories.users import EmailAlreadyExistsError, UserRepository

logger = logging.getLogger(__name__)


class InvalidCredentialsError(Exception):
    """Unknown email or wrong password. Intentionally indistinguishable."""


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int  # access token lifetime in seconds


class AuthService:
    def __init__(
        self, users: UserRepository, refresh_tokens: RefreshTokenService, settings: Settings
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._settings = settings
        self._dummy_hash: str | None = None

    async def register(self, email: str, password: str) -> None:
        """Create the account if the email is free. Returns normally either way."""
        # Hash first and unconditionally: the expensive step is identical for new and existing
        # emails, so response time does not reveal which one this was.
        password_hash = await hash_password(password)
        try:
            user = await self._users.create(email=email, password_hash=password_hash)
        except EmailAlreadyExistsError:
            logger.info("registration ignored: email already registered")
            return
        logger.info("user registered", extra={"user_id": str(user.id)})

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self._users.get_by_email(email)
        stored_hash = user.password_hash if user is not None else await self._get_dummy_hash()
        password_ok = await verify_password(password, stored_hash)
        if user is None or not password_ok:
            raise InvalidCredentialsError

        refresh = await self._refresh_tokens.issue(user.id)
        return TokenPair(
            access_token=create_access_token(user.id, self._settings),
            refresh_token=refresh.token,
            expires_in=self._settings.access_token_ttl_minutes * 60,
        )

    async def _get_dummy_hash(self) -> str:
        """A real hash of a random password: verifying against it costs what a real check costs."""
        if self._dummy_hash is None:
            self._dummy_hash = await hash_password(secrets.token_urlsafe(16))
        return self._dummy_hash
