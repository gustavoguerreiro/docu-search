from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure

from docusearch.core.clock import utc_now
from docusearch.db import collections
from docusearch.db.indexes import INDEXES, IndexInitializer, ensure_indexes, verify_indexes
from docusearch.db.mongo import MongoDatabase


def plan_stages(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Walk an explain() winning plan, yielding every stage."""
    yield plan
    for child in [plan.get("inputStage"), *plan.get("inputStages", [])]:
        if child:
            yield from plan_stages(child)


async def winning_stages(cursor: Any) -> list[dict[str, Any]]:
    explained = await cursor.explain()
    return list(plan_stages(explained["queryPlanner"]["winningPlan"]))


def stage_names(stages: list[dict[str, Any]]) -> set[str]:
    return {stage["stage"] for stage in stages}


def index_names(stages: list[dict[str, Any]]) -> set[str]:
    return {stage["indexName"] for stage in stages if "indexName" in stage}


async def test_ensure_creates_every_required_index(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)

    assert await verify_indexes(mongo_db) == []
    for collection_name, models in INDEXES.items():
        existing = await mongo_db[collection_name].index_information()
        assert {model.document["name"] for model in models} <= set(existing)


async def test_ensure_is_idempotent(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    before = {name: await mongo_db[name].index_information() for name in collections.ALL}

    await ensure_indexes(mongo_db)

    after = {name: await mongo_db[name].index_information() for name in collections.ALL}
    assert after == before


async def test_verify_reports_missing_indexes(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    await mongo_db[collections.USERS].drop_index("email_unique")

    assert await verify_indexes(mongo_db) == ["users.email_unique: missing"]


async def test_verify_reports_a_definition_that_drifted(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    users = mongo_db[collections.USERS]
    await users.drop_index("email_unique")
    await users.create_index([("email", ASCENDING)], name="email_unique")  # no longer unique

    assert await verify_indexes(mongo_db) == ["users.email_unique: different uniqueness"]


async def test_ensure_refuses_to_silently_replace_a_conflicting_index(
    mongo_db: MongoDatabase,
) -> None:
    users = mongo_db[collections.USERS]
    await users.create_index([("email", ASCENDING)], name="email_unique")  # not unique

    with pytest.raises(OperationFailure):
        await ensure_indexes(mongo_db)


async def test_initializer_succeeds_and_stays_done(mongo_db: MongoDatabase) -> None:
    initializer = IndexInitializer(mongo_db)

    done_before = initializer.done
    assert await initializer.ensure() is True
    done_after = initializer.done

    assert done_before is False
    assert done_after is True
    assert await initializer.ensure() is True


async def test_users_email_is_unique(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    users = mongo_db[collections.USERS]
    await users.insert_one({"email": "ana@example.com"})

    with pytest.raises(DuplicateKeyError):
        await users.insert_one({"email": "ana@example.com"})
    await users.insert_one({"email": "bia@example.com"})


async def test_document_hash_is_unique_per_owner_only(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    documents = mongo_db[collections.DOCUMENTS]
    ana, bia = ObjectId(), ObjectId()
    await documents.insert_one({"owner_id": ana, "sha256": "abc"})

    with pytest.raises(DuplicateKeyError):
        await documents.insert_one({"owner_id": ana, "sha256": "abc"})
    await documents.insert_one({"owner_id": bia, "sha256": "abc"})  # another user: allowed
    await documents.insert_one({"owner_id": ana, "sha256": "def"})  # another file: allowed


async def test_a_chunk_position_cannot_be_inserted_twice(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    chunks = mongo_db[collections.CHUNKS]
    document_id = ObjectId()
    await chunks.insert_one({"document_id": document_id, "chunk_index": 0, "text": "a"})

    with pytest.raises(DuplicateKeyError):
        await chunks.insert_one({"document_id": document_id, "chunk_index": 0, "text": "again"})
    await chunks.insert_one({"document_id": document_id, "chunk_index": 1, "text": "b"})
    await chunks.insert_one({"document_id": ObjectId(), "chunk_index": 0, "text": "other"})


async def test_refresh_token_hash_is_unique_and_expiry_is_a_ttl_index(
    mongo_db: MongoDatabase,
) -> None:
    await ensure_indexes(mongo_db)
    tokens = mongo_db[collections.REFRESH_TOKENS]
    expires_at = utc_now() + timedelta(days=14)
    await tokens.insert_one({"token_hash": "h1", "user_id": ObjectId(), "expires_at": expires_at})

    with pytest.raises(DuplicateKeyError):
        await tokens.insert_one(
            {"token_hash": "h1", "user_id": ObjectId(), "expires_at": expires_at}
        )
    ttl = (await tokens.index_information())["expires_ttl"]
    assert ttl["expireAfterSeconds"] == 0


async def test_listing_a_users_documents_uses_the_owner_index_without_sorting(
    mongo_db: MongoDatabase,
) -> None:
    await ensure_indexes(mongo_db)
    documents = mongo_db[collections.DOCUMENTS]
    owners = [ObjectId() for _ in range(3)]
    now = utc_now()
    await documents.insert_many(
        [
            {
                "owner_id": owners[i % 3],
                "sha256": f"hash-{i}",
                "status": "completed",
                "created_at": now + timedelta(seconds=i),
                "updated_at": now,
            }
            for i in range(60)
        ]
    )

    cursor = documents.find({"owner_id": owners[0]}).sort("created_at", -1).limit(20)
    stages = await winning_stages(cursor)

    assert "owner_created" in index_names(stages)
    assert "COLLSCAN" not in stage_names(stages)
    assert "SORT" not in stage_names(stages)  # order comes from the index


async def test_finding_stuck_documents_uses_the_status_index(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    documents = mongo_db[collections.DOCUMENTS]
    now = utc_now()
    await documents.insert_many(
        [
            {
                "owner_id": ObjectId(),
                "sha256": f"hash-{i}",
                "status": ["pending", "processing", "completed", "failed"][i % 4],
                "updated_at": now - timedelta(minutes=i),
            }
            for i in range(80)
        ]
    )

    cursor = documents.find(
        {"status": {"$in": ["pending", "processing"]}, "updated_at": {"$lt": now}}
    )
    stages = await winning_stages(cursor)

    assert "status_updated" in index_names(stages)
    assert "COLLSCAN" not in stage_names(stages)


async def test_reading_a_documents_chunks_in_order_needs_no_sort(mongo_db: MongoDatabase) -> None:
    await ensure_indexes(mongo_db)
    chunks = mongo_db[collections.CHUNKS]
    target = ObjectId()
    await chunks.insert_many(
        [
            {"document_id": target if i < 30 else ObjectId(), "chunk_index": i, "text": "t"}
            for i in range(60)
        ]
    )

    stages = await winning_stages(chunks.find({"document_id": target}).sort("chunk_index", 1))

    assert "document_chunk_unique" in index_names(stages)
    assert "SORT" not in stage_names(stages)
    assert "COLLSCAN" not in stage_names(stages)
