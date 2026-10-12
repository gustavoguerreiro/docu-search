from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from pydantic import TypeAdapter, ValidationError

from docusearch.db.ids import InvalidIdError, ObjectIdStr, object_id_to_str, parse_object_id

VALID = "507f1f77bcf86cd799439011"


def test_parse_returns_an_object_id() -> None:
    assert parse_object_id(VALID) == ObjectId(VALID)


def test_round_trip_through_string() -> None:
    oid = ObjectId()

    assert parse_object_id(object_id_to_str(oid)) == oid


def test_uppercase_hex_is_accepted_and_normalized() -> None:
    assert object_id_to_str(parse_object_id(VALID.upper())) == VALID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        VALID[:-1],  # 23 chars
        VALID + "0",  # 25 chars
        "g" * 24,  # right length, not hex
        f" {VALID}",
        f"{VALID}\n",  # trailing newline must not slip through a `$`-style match
        "abcdefghijkl",  # 12 characters: the driver treats 12 bytes as an id
        "../../etc/passwd",
        "507f1f77bcf86cd79943901é",
    ],
)
def test_malformed_strings_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdError):
        parse_object_id(value)


@pytest.mark.parametrize("value", [None, 123, b"abcdefghijkl", ObjectId(VALID), ["x"]])
def test_non_string_values_are_rejected(value: Any) -> None:
    with pytest.raises(InvalidIdError):
        parse_object_id(value)


def test_invalid_id_error_is_a_value_error() -> None:
    assert issubclass(InvalidIdError, ValueError)


def test_pydantic_type_normalizes_valid_values() -> None:
    assert TypeAdapter(ObjectIdStr).validate_python(VALID.upper()) == VALID


def test_pydantic_type_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ObjectIdStr).validate_python("nope")


async def test_malformed_path_parameter_is_a_422() -> None:
    app = FastAPI()

    @app.get("/things/{thing_id}")
    async def get_thing(thing_id: ObjectIdStr) -> dict[str, str]:
        return {"id": thing_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        ok = await client.get(f"/things/{VALID.upper()}")
        bad = await client.get("/things/not-an-id")

    assert ok.status_code == 200
    assert ok.json() == {"id": VALID}
    assert bad.status_code == 422
