from datetime import UTC, datetime

import pytest
from bson import ObjectId

from docusearch.db.models import Document, DocumentStatus, RefreshToken, User
from docusearch.db.repositories import MAX_PAGE_SIZE, check_page
from docusearch.db.repositories.users import normalize_email

NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ana@example.com", "ana@example.com"),
        ("  Ana@Example.COM\t", "ana@example.com"),
        ("ANA+tag@Example.com", "ana+tag@example.com"),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_normalize_email_is_idempotent() -> None:
    once = normalize_email("  MiXeD@Case.IO ")

    assert normalize_email(once) == once


@pytest.mark.parametrize(("limit", "offset"), [(1, 0), (MAX_PAGE_SIZE, 0), (20, 1000)])
def test_check_page_accepts_valid_arguments(limit: int, offset: int) -> None:
    check_page(limit, offset)


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (-5, 0), (MAX_PAGE_SIZE + 1, 0), (10, -1)])
def test_check_page_rejects_invalid_arguments(limit: int, offset: int) -> None:
    with pytest.raises(ValueError):
        check_page(limit, offset)


def test_models_map_the_mongo_id_and_hide_secrets_from_repr() -> None:
    user_id = ObjectId()
    user = User.model_validate(
        {
            "_id": user_id,
            "email": "ana@example.com",
            "password_hash": "$argon2id$secret-material",
            "created_at": NOW,
            "updated_at": NOW,
            "unexpected_field": "ignored",
        }
    )
    token = RefreshToken.model_validate(
        {
            "_id": ObjectId(),
            "user_id": user_id,
            "family_id": ObjectId(),
            "token_hash": "deadbeef-token-hash",
            "expires_at": NOW,
            "created_at": NOW,
        }
    )

    assert user.id == user_id
    assert "secret-material" not in repr(user)
    assert "secret-material" not in str(user)
    assert "deadbeef" not in repr(token)
    assert token.revoked_at is None


def test_document_status_values_match_the_documented_lifecycle() -> None:
    assert [s.value for s in DocumentStatus] == ["pending", "processing", "completed", "failed"]


def test_document_model_validates_status_and_is_immutable() -> None:
    document = Document.model_validate(
        {
            "_id": ObjectId(),
            "owner_id": ObjectId(),
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1,
            "sha256": "0" * 64,
            "storage_key": "k",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    assert document.status is DocumentStatus.PENDING
    with pytest.raises(ValueError):
        document.filename = "other.pdf"  # type: ignore[misc]
    with pytest.raises(ValueError):
        Document.model_validate({**document.model_dump(by_alias=True), "status": "weird"})
