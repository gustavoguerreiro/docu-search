"""Liveness and readiness endpoints.

- Liveness: the process is up and serving requests. It never touches dependencies, so a database
  outage cannot cause an orchestrator to restart a healthy process.
- Readiness: every registered dependency check passes. Checks are registered on
  `app.state.readiness_checks` (name -> async callable returning True when healthy). There are none
  yet; MongoDB and Redis register theirs in later milestones.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

type ReadinessCheck = Callable[[], Awaitable[bool]]

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    # Check name -> result. Deliberately carries no error details.
    checks: dict[str, Literal["ok", "fail"]]


@router.get(
    "/live",
    summary="Liveness probe",
    description="Returns 200 while the process is running. Does not check dependencies.",
)
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Returns 200 when all dependency checks pass, otherwise 503. "
        "The body lists check names and results only."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadyResponse,
            "description": "At least one dependency check failed.",
        }
    },
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    checks: Mapping[str, ReadinessCheck] = request.app.state.readiness_checks
    # Checks are independent, so run them concurrently: the probe takes as long as the slowest one.
    outcomes = await asyncio.gather(*(_run_check(name, check) for name, check in checks.items()))
    results = dict(outcomes)

    if "fail" in results.values():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="unavailable", checks=results)
    return ReadyResponse(status="ok", checks=results)


async def _run_check(name: str, check: ReadinessCheck) -> tuple[str, Literal["ok", "fail"]]:
    try:
        healthy = await check()
    except Exception:
        # A crashing check means "not ready"; it must not turn the probe into a 500.
        logger.warning("readiness check raised", extra={"check": name}, exc_info=True)
        healthy = False
    return name, "ok" if healthy else "fail"
