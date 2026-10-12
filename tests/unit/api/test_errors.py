import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, SecretStr

from docusearch.api.errors import ApiError, install_error_handlers


class Login(BaseModel):
    email: str
    password: SecretStr


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api-error")
    async def api_error() -> None:
        raise ApiError(418, "teapot", "Short and stout.", headers={"X-Hint": "handle"})

    @app.get("/http-error")
    async def http_error() -> None:
        raise HTTPException(status_code=404, detail="Nothing here.")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail: password=hunter2")

    @app.post("/login")
    async def login(body: Login) -> None:
        return None

    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_api_error_uses_the_common_body_and_headers(app: FastAPI) -> None:
    async with client(app) as http:
        response = await http.get("/api-error")

    assert response.status_code == 418
    assert response.json() == {"error": {"code": "teapot", "message": "Short and stout."}}
    assert response.headers["x-hint"] == "handle"


async def test_validation_errors_list_problems_but_never_echo_the_submitted_values(
    app: FastAPI,
) -> None:
    async with client(app) as http:
        response = await http.post("/login", json={"email": 12345, "password": ["hunter2-secret"]})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert {tuple(d["loc"]) for d in body["error"]["details"]} >= {("body", "email")}
    assert all(set(d) == {"loc", "msg", "type"} for d in body["error"]["details"])
    assert "hunter2-secret" not in response.text
    assert "12345" not in response.text


async def test_unknown_routes_and_wrong_methods_use_the_common_body(app: FastAPI) -> None:
    async with client(app) as http:
        missing = await http.get("/does-not-exist")
        wrong_method = await http.delete("/api-error")
        explicit = await http.get("/http-error")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"
    assert explicit.json() == {"error": {"code": "not_found", "message": "Nothing here."}}


async def test_unexpected_errors_return_a_generic_500_and_are_logged_not_leaked(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        async with client(app) as http:
            response = await http.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An internal error occurred."}
    }
    assert "hunter2" not in response.text
    assert any(r.exc_info for r in caplog.records)  # the operator still gets the traceback
