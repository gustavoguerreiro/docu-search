"""Document endpoints."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from docusearch.api import API_PREFIX
from docusearch.api.dependencies import CurrentUser
from docusearch.api.errors import ApiError, ErrorResponse
from docusearch.db.ids import ObjectIdStr, parse_object_id
from docusearch.db.models import Document, DocumentStatus
from docusearch.documents.intake import (
    EmptyUploadError,
    UnsupportedFileTypeError,
    UploadTooLargeError,
)
from docusearch.documents.service import DocumentAlreadyExistsError, DocumentService
from docusearch.storage import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


def get_document_service(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.document_service
    return service


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


class DocumentResponse(BaseModel):
    """What the API says about a document. The storage key is deliberately absent."""

    id: ObjectIdStr
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int = Field(description="All of the caller's documents, regardless of the page.")
    limit: int
    offset: int


def to_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=str(document.id),
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        status=document.status,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document",
    description=(
        "Accepts a PDF, DOCX or UTF-8 text file as multipart form data (field `file`). The type is "
        "determined from the content; the file name and the declared content type are not trusted. "
        "The document is stored and recorded as `pending`; processing happens later. Uploading "
        "content you already uploaded returns `409` with a `Location` header for the existing "
        "document."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "The file is empty."},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Not authenticated."},
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "This content was already uploaded by the same user.",
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "model": ErrorResponse,
            "description": "The file exceeds the maximum upload size.",
        },
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorResponse,
            "description": "The content is not a PDF, DOCX or UTF-8 text file.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The file store is unavailable.",
        },
    },
)
async def upload_document(
    user: CurrentUser,
    service: DocumentServiceDep,
    file: Annotated[UploadFile, File(description="The document to upload.")],
) -> DocumentResponse:
    try:
        # The owner is the authenticated user; nothing in the request can change that.
        document = await service.upload(user.id, file, file.filename)
    except UploadTooLargeError as exc:
        raise ApiError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "payload_too_large",
            "The file exceeds the maximum upload size.",
        ) from exc
    except EmptyUploadError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "empty_file", "The file is empty.") from exc
    except UnsupportedFileTypeError as exc:
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_file_type",
            "Only PDF, DOCX and UTF-8 text files are accepted.",
        ) from exc
    except DocumentAlreadyExistsError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "duplicate_document",
            "You already uploaded this file.",
            headers={"Location": f"{API_PREFIX}/documents/{exc.document_id}"},
        ) from exc
    except StorageError as exc:
        logger.error("file store failed during upload", extra={"error_type": type(exc).__name__})
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "The file could not be stored. Try again later.",
        ) from exc
    return to_response(document)


def _not_found() -> ApiError:
    # One shape and one message for "does not exist" and "belongs to someone else".
    return ApiError(status.HTTP_404_NOT_FOUND, "document_not_found", "Document not found.")


@router.get(
    "",
    summary="List my documents",
    description="The caller's documents, newest first. Other users' documents are never included.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Not authenticated."}
    },
)
async def list_documents(
    user: CurrentUser,
    service: DocumentServiceDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000, description="Documents to skip.")] = 0,
) -> DocumentListResponse:
    response.headers["Cache-Control"] = "no-store"
    documents, total = await service.list(user.id, limit=limit, offset=offset)
    return DocumentListResponse(
        items=[to_response(d) for d in documents], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{document_id}",
    summary="Get a document",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Not authenticated."},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No such document, or it belongs to another user (indistinguishable).",
        },
    },
)
async def get_document(
    document_id: ObjectIdStr, user: CurrentUser, service: DocumentServiceDep, response: Response
) -> DocumentResponse:
    response.headers["Cache-Control"] = "no-store"
    document = await service.get(user.id, parse_object_id(document_id))
    if document is None:
        raise _not_found()
    return to_response(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description=(
        "Deletes the document, its extracted text and its stored file. The content hash becomes "
        "free again, so the same file can be uploaded afterwards."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Not authenticated."},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No such document, or it belongs to another user (indistinguishable).",
        },
    },
)
async def delete_document(
    document_id: ObjectIdStr, user: CurrentUser, service: DocumentServiceDep
) -> Response:
    if not await service.delete(user.id, parse_object_id(document_id)):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
