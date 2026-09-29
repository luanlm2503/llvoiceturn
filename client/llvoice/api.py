"""Mọi yêu cầu tới proxy đi qua đây. Token và HWID sẽ được gắn thêm ở task 1.61 / 1.62."""

import httpx
from llvoice_shared.errors import HEADER_APP_VERSION, ErrorCode

from llvoice import __version__
from llvoice.config import API_BASE_URL


class ApiError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class ApiClient:
    def __init__(self, base_url: str = API_BASE_URL) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(10.0, read=120.0),
            headers={HEADER_APP_VERSION: __version__},
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            res = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError("network_error", str(exc)) from exc
        if res.is_success:
            return res.json()
        try:
            body = res.json()
            raise ApiError(body["code"], body.get("message", ""), body.get("data"))
        except (ValueError, KeyError):
            raise ApiError(ErrorCode.INTERNAL, res.text) from None

    def health(self) -> dict:
        return self._request("GET", "/health")

    def close(self) -> None:
        self._http.close()
