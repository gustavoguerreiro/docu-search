from datetime import UTC, datetime

from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.db.ids import object_id_to_str, parse_object_id
from docusearch.db.mongo import MongoDatabase


async def test_utc_now_survives_a_round_trip_unchanged(mongo_db: MongoDatabase) -> None:
    created_at = utc_now()

    inserted = await mongo_db["things"].insert_one({"created_at": created_at})
    stored = await mongo_db["things"].find_one({"_id": inserted.inserted_id})

    assert stored is not None
    assert stored["created_at"] == created_at
    assert stored["created_at"].tzinfo is not None


async def test_naive_datetimes_are_stored_as_utc_but_never_come_back_naive(
    mongo_db: MongoDatabase,
) -> None:
    # The driver interprets naive values as UTC. We never write them on purpose (ruff DTZ forbids
    # it), but reads must always be aware regardless of how a document was written.
    naive = datetime(2026, 1, 2, 3, 4, 5)  # noqa: DTZ001 - deliberately naive

    inserted = await mongo_db["things"].insert_one({"at": naive})
    stored = await mongo_db["things"].find_one({"_id": inserted.inserted_id})

    assert stored is not None
    assert stored["at"] == naive.replace(tzinfo=UTC)


async def test_object_id_string_round_trip_finds_the_document(mongo_db: MongoDatabase) -> None:
    inserted = await mongo_db["things"].insert_one({"name": "a"})
    public_id = object_id_to_str(inserted.inserted_id)

    found = await mongo_db["things"].find_one({"_id": parse_object_id(public_id)})
    other = await mongo_db["things"].find_one({"_id": ObjectId()})

    assert found is not None
    assert found["name"] == "a"
    assert other is None
