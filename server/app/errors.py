"""Mọi lỗi trả về app đều có dạng {code, message, data}. App dựa vào `code` để hiện thông báo."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from llvoice_shared.errors import ErrorCode
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status: int = 400,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.data = data or {}


def error_response(
    code: ErrorCode, message: str, status: int, data: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, "data": data or {}}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status, exc.data)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            ErrorCode.VALIDATION,
            "Dữ liệu gửi lên không hợp lệ.",
            422,
            {"errors": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            code = ErrorCode.NOT_FOUND
        elif exc.status_code < 500:
            code = ErrorCode.VALIDATION
        else:
            code = ErrorCode.INTERNAL
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Lỗi không xử lý được", exc_info=exc)
        return error_response(ErrorCode.INTERNAL, "Lỗi hệ thống, vui lòng thử lại sau.", 500)
