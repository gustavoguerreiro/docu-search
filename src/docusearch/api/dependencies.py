"""Request dependencies shared by routers.

Authorization policy
--------------------
The identity of the caller comes from exactly one place: the verified access token. Routes take
`user: CurrentUser` and pass `user.id` to repositories as the owner. Nothing the client sends
(path, query, body, headers) is ever used to decide *whose* data to touch. Repositories enforce the
rest by filtering on the owner in the query itself, so another user's resource is reported as not
found, exactly like a missing one.
"""

from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from docusearch.api.errors import ApiError
from docusearch.auth.service import AuthService
from docusearch.core.tokens import ExpiredTokenError, InvalidTokenError
from docusearch.db.models import User

_bearer = HTTPBearer(auto_error=False, description="Access token from `POST /auth/login`.")
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: AuthServiceDep,
) -> User:
    if credentials is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "not_authenticated",
            "Authentication required.",
            headers=_CHALLENGE,
        )
    try:
        return await service.authenticate(credentials.credentials)
    except ExpiredTokenError as exc:
        # Telling the client the token *expired* lets it refresh; it reveals nothing else.
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "token_expired",
            "The access token has expired.",
            headers=_CHALLENGE,
        ) from exc
    except InvalidTokenError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_token",
            "The access token is invalid.",
            headers=_CHALLENGE,
        ) from exc


CurrentUser = Annotated[User, Depends(get_current_user)]
