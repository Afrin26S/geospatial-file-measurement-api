"""One consistent error shape for the whole API:

    {"error": {"code": "bad_zip", "message": "...", ...extra fields}}
"""
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    body = {"error": {"code": exc.code, "message": exc.message, **exc.extra}}
    return JSONResponse(status_code=exc.status_code, content=body)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    body = {
        "error": {
            "code": "validation_error",
            "message": "The request is invalid",
            "details": jsonable_encoder(exc.errors()),
        }
    }
    return JSONResponse(status_code=422, content=body)
