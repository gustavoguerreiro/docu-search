"""MongoDB identifier conversion and validation.

Identifiers cross the API boundary as 24-character hexadecimal strings and are stored as
`ObjectId`. Conversion happens in exactly one place so every caller gets the same strictness and the
same exception.
"""

import re
from typing import Annotated

from bson import ObjectId
from pydantic import AfterValidator

_OBJECT_ID_PATTERN = re.compile(r"[0-9a-fA-F]{24}")


class InvalidIdError(ValueError):
    """The value is not a valid object identifier."""


def parse_object_id(value: str) -> ObjectId:
    """Convert a 24-character hex string to an ObjectId.

    Stricter than the driver on purpose: only `str` input, no surrounding whitespace, no 12-byte raw
    values. Raises `InvalidIdError` for anything else.
    """
    if not isinstance(value, str) or not _OBJECT_ID_PATTERN.fullmatch(value):
        raise InvalidIdError("invalid object id")
    return ObjectId(value)


def object_id_to_str(value: ObjectId) -> str:
    return str(value)


def _validate_and_normalize(value: str) -> str:
    # pydantic turns ValueError (InvalidIdError is one) into a validation error -> HTTP 422.
    return object_id_to_str(parse_object_id(value))


# Use for path/query parameters and response fields that carry a document identifier.
# Valid values are normalized to lowercase hex.
ObjectIdStr = Annotated[str, AfterValidator(_validate_and_normalize)]
