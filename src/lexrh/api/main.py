import logging
from collections.abc import Callable
from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field

from lexrh import __version__
from lexrh.config import get_settings
from lexrh.db.session import database_is_ready
from lexrh.rag.answer import Assistant, build_assistant
from lexrh.rag.http import ApiError

logger = logging.getLogger("lexrh")


def get_readiness_check() -> Callable[[], bool]:
    """Dependency so tests can replace the database probe."""
    return database_is_ready


@lru_cache
def get_assistant() -> Assistant:
    """Built on the first request (loads the reranker model); tests replace it."""
    return build_assistant()


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    as_of: date | None = Field(
        default=None,
        alias="date",
        description="Law as in force on this day (default: today)",
    )


class SourceOut(BaseModel):
    num: str
    legal_id: str
    valid_from: date
    valid_to: date | None
    status: str
    hierarchy: list[str]


class AskResponse(BaseModel):
    answer: str
    refused: bool
    reason: str | None
    as_of: date
    sources: list[SourceOut]
    retrieved: list[str]


def create_app() -> FastAPI:
    logging.basicConfig(level=get_settings().log_level)
    app = FastAPI(title="LexRH", version=__version__)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    def ready(
        response: Response,
        check: Annotated[Callable[[], bool], Depends(get_readiness_check)],
    ) -> dict[str, str]:
        """Readiness: the database answers."""
        if check():
            return {"status": "ready"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unavailable"}

    @app.post("/ask", tags=["rag"], response_model=AskResponse)
    def ask(
        body: AskRequest, assistant: Annotated[Assistant, Depends(get_assistant)]
    ) -> AskResponse:
        """Answer from the articles in force on `date`, with checked citations, or refuse."""
        as_of = body.as_of or date.today()
        try:
            result = assistant.ask(body.question, as_of)
        except ApiError as exc:
            logger.error("ask failed: %s", exc)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "A model is unavailable"
            ) from exc
        return AskResponse(
            answer=result.answer,
            refused=result.refused,
            reason=result.reason,
            as_of=result.as_of,
            sources=[SourceOut(**vars(source)) for source in result.sources],
            retrieved=result.retrieved,
        )

    return app


app = create_app()
