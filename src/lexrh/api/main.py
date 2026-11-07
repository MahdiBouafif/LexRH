import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status

from lexrh import __version__
from lexrh.config import get_settings
from lexrh.db.session import database_is_ready

logger = logging.getLogger("lexrh")


def get_readiness_check() -> Callable[[], bool]:
    """Dependency so tests can replace the database probe."""
    return database_is_ready


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

    return app


app = create_app()
