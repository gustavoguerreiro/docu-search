"""Document endpoints."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from pydantic import BaseModel

from docusearch.api import API_PREFIX
from docusearch.api.dependencies import CurrentUser
from docusearch.api.errors import ApiError, ErrorResponse
from docusearch.db.ids import ObjectIdStr
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
