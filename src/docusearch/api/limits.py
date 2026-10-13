"""Request body size limits, enforced before the application reads (or the framework spools) a body.

Without this, multipart parsing would copy an entire upload to disk before any size check in a route
could run, so an oversized request would be fully accepted first. The guard answers `413` as soon as
the declared `Content-Length` exceeds the limit, and for bodies without one (chunked transfer) it
counts bytes and aborts when the limit is crossed.

Two limits apply: the upload route may carry the configured maximum file size (plus room for the
multipart framing); every other route is JSON and gets a small default.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docusearch.api.errors import ApiError, error_response

# Boundaries, part headers and the file name around the file content in a multipart body.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
DEFAULT_BODY_LIMIT_BYTES = 1024 * 1024


def _too_large() -> ApiError:
    return ApiError(413, "payload_too_large", "The request body is too large.")


class RequestSizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        upload_path: str,
        max_upload_bytes: int,
        default_limit_bytes: int = DEFAULT_BODY_LIMIT_BYTES,
    ) -> None:
        self.app = app
        self._upload_path = upload_path
        self._upload_limit = max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        self._default_limit = default_limit_bytes

    def _limit_for(self, scope: Scope) -> int:
        is_upload = scope["method"] == "POST" and scope["path"].rstrip("/") == self._upload_path
        return self._upload_limit if is_upload else self._default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False  # a malformed header is the server's problem, not ours to guess at
            if too_big:
                error = _too_large()
                response = error_response(error.status_code, error.code, error.message)
                await response(scope, receive, send)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _too_large()
            return message

        await self.app(scope, counting_receive, send)
