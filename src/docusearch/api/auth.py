"""Authentication endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from docusearch.api.errors import ApiError, ErrorResponse
from docusearch.auth.refresh_tokens import InvalidRefreshTokenError
from docusearch.auth.service import AuthService, InvalidCredentialsError, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

# Password policy: length only. Composition rules make passwords worse, not better. The upper bound
# keeps hashing work bounded; the hasher enforces its own byte limit as well.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254, examples=["ana@example.com"])
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class RegisterResponse(BaseModel):
    status: Literal["accepted"]
    message: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254, examples=["ana@example.com"])
    # Only bounded here: the registration policy must not lock out older, shorter passwords.
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounded only to cap request size. Whether it is a valid token is the service's call, so that
    # every bad token (short, long, malformed, unknown) gets the same 401.
    refresh_token: SecretStr = Field(max_length=4096)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds.")
    refresh_token: str


def token_response(pair: TokenPair, response: Response) -> TokenResponse:
    # Credentials must never be cached by browsers or intermediaries.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        refresh_token=pair.refresh_token,
    )


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Register an account",
    description=(
        "Always answers `202` with the same body, whether or not the email already has an account, "
        "so the endpoint cannot be used to find out who is registered. Log in afterwards."
    ),
)
async def register(body: RegisterRequest, service: AuthServiceDep) -> RegisterResponse:
    await service.register(body.email, body.password.get_secret_value())
    return RegisterResponse(
        status="accepted",
        message="If the email can be registered, the account is ready: log in with it.",
    )


@router.post(
    "/login",
    summary="Log in",
    description="Returns a short-lived access token and a rotating refresh token.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Unknown email or wrong password (indistinguishable).",
        }
    },
)
async def login(body: LoginRequest, response: Response, service: AuthServiceDep) -> TokenResponse:
    try:
        pair = await service.login(body.email, body.password.get_secret_value())
    except InvalidCredentialsError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return token_response(pair, response)


@router.post(
    "/refresh",
    summary="Refresh the session",
    description=(
        "Exchanges a refresh token for a new access token and a new refresh token. Refresh "
        "tokens are single use: the one sent here stops working. Sending an already-used token "
        "ends the whole session, because it means the token was copied. A session ends a fixed "
        "time after login whatever the refreshes."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "The refresh token is unknown, expired, used or revoked.",
        }
    },
)
async def refresh(
    body: RefreshRequest, response: Response, service: AuthServiceDep
) -> TokenResponse:
    try:
        pair = await service.refresh(body.refresh_token.get_secret_value())
    except InvalidRefreshTokenError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_refresh_token",
            "The refresh token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return token_response(pair, response)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
    description=(
        "Ends the session the refresh token belongs to. Always answers `204`, including for "
        "unknown or already-used tokens, so it reveals nothing about which tokens exist. Access "
        "tokens already issued remain valid until they expire (at most 15 minutes)."
    ),
)
async def logout(body: RefreshRequest, service: AuthServiceDep) -> Response:
    await service.logout(body.refresh_token.get_secret_value())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
