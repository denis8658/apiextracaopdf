import os
import uuid
from pathlib import Path

if __name__ == "__main__":
    from app.core.runtime import reexec_with_project_python

    reexec_with_project_python("app.main")

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="API de Extração Documental",
    description="Upload assíncrono e extração de PDFs em texto, Markdown e JSON por página.",
    version=settings.app_version,
    openapi_tags=[
        {"name": "health", "description": "Vivacidade e prontidão"},
        {"name": "documents", "description": "Documentos, trabalhos, páginas e resultados"},
    ],
)


def error_payload(request: Request, code: str, message: str, details=None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request.state.request_id,
        }
    }


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id[:255]
    try:
        response = await call_next(request)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "unhandled_request_error", extra={"request_id": request.state.request_id}
        )
        response = JSONResponse(
            error_payload(request, "internal_error", "Ocorreu um erro interno."),
            status_code=500,
        )
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        error_payload(request, exc.code, exc.message, exc.details),
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(
            error_payload(
                request, "validation_error", "Dados da requisição inválidos.", exc.errors()
            )
        ),
        status_code=422,
    )


app.include_router(router)


@app.get("/", include_in_schema=False)
async def interface_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


app.mount(
    "/ui",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="test-interface",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allowed_methods,
    allow_headers=settings.cors_allowed_headers,
    expose_headers=settings.cors_expose_headers,
    max_age=settings.cors_max_age,
)


def run() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    run()
