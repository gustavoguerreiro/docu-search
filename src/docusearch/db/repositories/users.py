from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from docusearch.core.clock import utc_now
from docusearch.db import collections
from docusearch.db.models import User
from docusearch.db.mongo import MongoDatabase


class EmailAlreadyExistsError(Exception):
    """A user with this (normalized) email already exists."""


def normalize_email(email: str) -> str:
    """The single definition of "the same email": surrounding whitespace and case are ignored."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, db: MongoDatabase) -> None:
        self._users = db[collections.USERS]

    async def create(self, *, email: str, password_hash: str) -> User:
        now = utc_now()
        document = {
            "email": normalize_email(email),
            "password_hash": password_hash,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self._users.insert_one(document)
        except DuplicateKeyError as exc:
            raise EmailAlreadyExistsError from exc
        return User.model_validate({**document, "_id": result.inserted_id})

    async def get_by_email(self, email: str) -> User | None:
        document = await self._users.find_one({"email": normalize_email(email)})
        return User.model_validate(document) if document else None

    async def get_by_id(self, user_id: ObjectId) -> User | None:
        document = await self._users.find_one({"_id": user_id})
        return User.model_validate(document) if document else None
