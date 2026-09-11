"""FastAPI uygulamasinin kurulumu: statik dosyalar, sayfalar, tek tip hata bicimi."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, paths
from .api import router
from .context import AppContext
from .jira_client import JiraError
from .mail import MailError
from .repository import RepositoryError


def create_app(context: AppContext) -> FastAPI:
    app = FastAPI(title="Holocron", version=__version__, docs_url=None, redoc_url=None)
    app.state.context = context

    @app.exception_handler(JiraError)
    async def _jira_error(_: Request, exc: JiraError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": exc.to_dict()})

    @app.exception_handler(RepositoryError)
    async def _repository_error(_: Request, exc: RepositoryError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(MailError)
    async def _mail_error(_: Request, exc: MailError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    # Starlette'in kendi 404'u da buradan gecsin diye taban sinifa baglanir.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": _first_message(exc)}},
        )

    app.include_router(router)

    static_dir = paths.static_dir()
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/settings", include_in_schema=False)
    def settings_page() -> FileResponse:
        return FileResponse(static_dir / "settings.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")

    return app


def _first_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "İstek doğrulanamadı."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "geçersiz değer")
    return f"{location}: {message}" if location else str(message)
