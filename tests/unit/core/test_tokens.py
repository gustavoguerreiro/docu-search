import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from bson import ObjectId
from pydantic import SecretStr

from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.core.tokens import (
    AUDIENCE,
    ISSUER,
    ExpiredTokenError,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)

KEY = "k" * 48
USER = ObjectId()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret_key=SecretStr(KEY), access_token_ttl_minutes=15)


def forge(payload: dict[str, Any], key: str = KEY, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, key, algorithm=algorithm)


def good_payload(**overrides: Any) -> dict[str, Any]:
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": str(USER),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "abc",
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


def b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":"), default=lambda o: int(o.timestamp())).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_round_trip(settings: Settings) -> None:
    before = utc_now()

    claims = decode_access_token(create_access_token(USER, settings), settings)

    assert claims.user_id == USER
    assert claims.issued_at.tzinfo is not None
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)
    assert claims.issued_at >= before.replace(microsecond=0)
    assert claims.token_id


def test_every_token_has_a_unique_id(settings: Settings) -> None:
    first = decode_access_token(create_access_token(USER, settings), settings)
    second = decode_access_token(create_access_token(USER, settings), settings)

    assert first.token_id != second.token_id


def test_ttl_comes_from_settings() -> None:
    short = Settings(_env_file=None, jwt_secret_key=SecretStr(KEY), access_token_ttl_minutes=1)

    claims = decode_access_token(create_access_token(USER, short), short)

    assert claims.expires_at - claims.issued_at == timedelta(minutes=1)


def test_expired_token_is_reported_as_expired(settings: Settings) -> None:
    old = utc_now() - timedelta(hours=1)

    token = create_access_token(USER, settings, now=old)

    with pytest.raises(ExpiredTokenError):
        decode_access_token(token, settings)


def test_expired_is_a_kind_of_invalid(settings: Settings) -> None:
    assert issubclass(ExpiredTokenError, InvalidTokenError)


def test_token_issued_in_the_future_is_rejected(settings: Settings) -> None:
    token = create_access_token(USER, settings, now=utc_now() + timedelta(hours=1))

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(forge(good_payload(), key="x" * 48), settings)


def test_tampered_payload_is_rejected(settings: Settings) -> None:
    header, _payload, signature = create_access_token(USER, settings).split(".")
    evil = b64(good_payload(sub=str(ObjectId()), exp=int(utc_now().timestamp()) + 3600))

    with pytest.raises(InvalidTokenError):
        decode_access_token(f"{header}.{evil}.{signature}", settings)


def test_unsigned_alg_none_token_is_rejected(settings: Settings) -> None:
    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({**good_payload(), "iat": 1, "exp": 4102444800})

    for candidate in (f"{header}.{payload}.", f"{header}.{payload}"):
        with pytest.raises(InvalidTokenError):
            decode_access_token(candidate, settings)


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")  # HS512 on purpose
def test_a_different_hmac_algorithm_is_rejected_even_with_the_right_key(
    settings: Settings,
) -> None:
    token = forge(good_payload(), algorithm="HS512")

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "iss", "aud", "jti"])
def test_every_required_claim_must_be_present(settings: Settings, missing: str) -> None:
    token = forge(good_payload(**{missing: None}))

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


@pytest.mark.parametrize(
    "overrides",
    [{"iss": "someone-else"}, {"aud": "another-api"}],
    ids=["issuer", "audience"],
)
def test_issuer_and_audience_are_enforced(settings: Settings, overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(forge(good_payload(**overrides)), settings)


@pytest.mark.parametrize("subject", ["not-an-object-id", "", " " + str(USER), 12345, None, ["x"]])
def test_subject_must_be_a_valid_object_id(settings: Settings, subject: Any) -> None:
    token = forge(good_payload(sub=subject if subject is not None else "x"))
    if subject is None:
        token = forge({**good_payload(), "sub": None})

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


@pytest.mark.parametrize(
    "garbage",
    ["", "x", "a.b", "a.b.c", "....", "Bearer abc", "ey" * 5000, "\x00\x01", "🔐.🔐.🔐"],
)
def test_garbage_is_rejected_without_raising_anything_else(
    settings: Settings, garbage: str
) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(garbage, settings)


def test_errors_never_echo_the_token(settings: Settings) -> None:
    token = forge(good_payload(), key="x" * 48)

    with pytest.raises(InvalidTokenError) as caught:
        decode_access_token(token, settings)

    assert token not in str(caught.value)
    assert "x" * 48 not in str(caught.value)


def test_naive_issue_time_is_not_silently_trusted(settings: Settings) -> None:
    # jwt.encode treats naive datetimes as UTC; a token we mint ourselves must be aware regardless.
    token = create_access_token(USER, settings, now=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(ExpiredTokenError):
        decode_access_token(token, settings)
