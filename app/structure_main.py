import os
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.health import router as health_router
from app.api.v1.order_structuring import router as order_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(
    title="API de Estruturação de Pedidos",
    description="Serviço separado para estruturar dados de negócio a partir de extrações.",
    version=settings.app_version,
)


def payload(request: Request, code: str, message: str, details=None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request.state.request_id,
        }
    }


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = (request.headers.get("X-Request-ID") or str(uuid.uuid4()))[:255]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    return JSONResponse(
        payload(request, exc.code, exc.message, exc.details), status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        jsonable_encoder(
            payload(request, "validation_error", "Dados da requisição inválidos.", exc.errors())
        ),
        status_code=422,
    )


app.include_router(health_router)
app.include_router(order_router)


def run() -> None:
    uvicorn.run("app.structure_main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8001")))


if __name__ == "__main__":
    run()
