import httpx
import pytest
from fastapi import FastAPI, Request

from docusearch.api.errors import install_error_handlers
from docusearch.api.limits import MULTIPART_OVERHEAD_BYTES, RequestSizeLimitMiddleware

UPLOAD_PATH = "/api/v1/documents"
MAX_UPLOAD = 1000
DEFAULT_LIMIT = 200


class Probe:
    """Records whether, and how much of, the request body the application read."""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.reached = False


@pytest.fixture
def probe() -> Probe:
    return Probe()


@pytest.fixture
def app(probe: Probe) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        upload_path=UPLOAD_PATH,
        max_upload_bytes=MAX_UPLOAD,
        default_limit_bytes=DEFAULT_LIMIT,
    )

    async def consume(request: Request) -> dict[str, int]:
        probe.reached = True
        async for chunk in request.stream():
            probe.bytes_read += len(chunk)
        return {"read": probe.bytes_read}

    app.post(UPLOAD_PATH)(consume)
    app.post("/api/v1/other")(consume)
    app.get("/api/v1/other")(consume)
    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def chunks(total: int, size: int = 100):  # type: ignore[no-untyped-def]
    sent = 0
    while sent < total:
        piece = min(size, total - sent)
        yield b"x" * piece
        sent += piece


UPLOAD_LIMIT = MAX_UPLOAD + MULTIPART_OVERHEAD_BYTES


async def test_a_declared_oversized_body_is_refused_before_the_app_reads_it(
    app: FastAPI, probe: Probe
) -> None:
    async with client(app) as http:
        response = await http.post(UPLOAD_PATH, content=b"x" * (UPLOAD_LIMIT + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert probe.reached is False


async def test_an_upload_within_the_limit_passes(app: FastAPI, probe: Probe) -> None:
    async with client(app) as http:
        response = await http.post(UPLOAD_PATH, content=b"x" * UPLOAD_LIMIT)

    assert response.status_code == 200
    assert probe.bytes_read == UPLOAD_LIMIT


async def test_a_chunked_body_without_content_length_is_cut_off_at_the_limit(
    app: FastAPI, probe: Probe
) -> None:
    async with client(app) as http:
        response = await http.post(UPLOAD_PATH, content=chunks(UPLOAD_LIMIT * 20, size=4096))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert probe.bytes_read <= UPLOAD_LIMIT  # it never got more than the limit


async def test_other_routes_get_the_small_default_not_the_upload_limit(
    app: FastAPI, probe: Probe
) -> None:
    async with client(app) as http:
        too_big = await http.post("/api/v1/other", content=b"x" * (DEFAULT_LIMIT + 1))
        fits = await http.post("/api/v1/other", content=b"x" * DEFAULT_LIMIT)

    assert too_big.status_code == 413
    assert fits.status_code == 200


async def test_the_upload_limit_applies_only_to_post_on_the_upload_path(app: FastAPI) -> None:
    async with client(app) as http:
        response = await http.get("/api/v1/other")  # no body: unaffected

    assert response.status_code == 200


async def test_a_trailing_slash_does_not_dodge_the_upload_route_rules(app: FastAPI) -> None:
    async with client(app) as http:
        response = await http.post(UPLOAD_PATH + "/", content=b"x" * (UPLOAD_LIMIT + 1))

    assert response.status_code == 413


async def test_a_malformed_content_length_is_left_to_the_server(app: FastAPI) -> None:
    async with client(app) as http:
        request = http.build_request("POST", "/api/v1/other", content=b"ok")
        request.headers["content-length"] = "not-a-number"
        # The transport rewrites nothing: the guard must not crash on it.
        response = await http.send(request)

    assert response.status_code in {200, 400, 413}


async def test_the_error_body_is_the_common_one(app: FastAPI) -> None:
    async with client(app) as http:
        response = await http.post(UPLOAD_PATH, content=b"x" * (UPLOAD_LIMIT + 1))

    assert response.json() == {
        "error": {"code": "payload_too_large", "message": "The request body is too large."}
    }
