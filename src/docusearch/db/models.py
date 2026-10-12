"""Records read from MongoDB.

Frozen pydantic models that validate what comes out of the database. Identifiers are `ObjectId`
here; they become strings only at the API boundary (see `docusearch.db.ids.ObjectIdStr`). Secret
fields are excluded from `repr`, so a stray `logger.info("%s", user)` cannot leak them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class _Record(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, populate_by_name=True)

    id: ObjectId = Field(alias="_id")


class User(_Record):
    email: str  # stored normalized (see repositories.users.normalize_email)
    password_hash: str = Field(repr=False)
    created_at: datetime
    updated_at: datetime


class Document(_Record):
    owner_id: ObjectId
    filename: str  # display name only; never used to build a storage path
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str  # server-generated; never exposed through the API
    status: DocumentStatus
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class Chunk(_Record):
    document_id: ObjectId
    owner_id: ObjectId
    chunk_index: int
    text: str
    page: int | None = None


@dataclass(frozen=True)
class NewChunk:
    """A chunk to be stored; the repository supplies the document and owner."""

    chunk_index: int
    text: str
    page: int | None = None


class RefreshToken(_Record):
    user_id: ObjectId
    token_hash: str = Field(repr=False)
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
