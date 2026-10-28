"""HTTP boundaries shared by the console's routes, without domain dependencies."""
from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 2_000_000


class APIError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def send_bytes(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    content_type: str,
    status: int = HTTPStatus.OK,
    *,
    sandbox: bool = False,
    download_name: str | None = None,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "same-origin")
    if download_name:
        from urllib.parse import quote
        handler.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download_name))
    if sandbox:
        # Generated HTML/SVG must not gain access to the console's API or DOM.
        handler.send_header("Content-Security-Policy", "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'")
    handler.end_headers()
    handler.wfile.write(body)


def json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = HTTPStatus.OK) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    send_bytes(handler, body, "application/json; charset=utf-8", status)


def text_response(
    handler: BaseHTTPRequestHandler,
    text: str,
    content_type: str = "text/html; charset=utf-8",
    status: int = HTTPStatus.OK,
) -> None:
    send_bytes(handler, text.encode("utf-8"), content_type, status)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if handler.headers.get("Transfer-Encoding"):
        raise APIError("不支持分块请求体", HTTPStatus.BAD_REQUEST)
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except (TypeError, ValueError) as exc:
        raise APIError("Content-Length 必须是合法整数") from exc
    if length < 0:
        raise APIError("Content-Length 不能为负数")
    if length > MAX_REQUEST_BYTES:
        raise APIError("请求体超过 2 MB 限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    if not length:
        return {}
    media_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise APIError("Content-Type 必须是 application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise APIError("请求体不完整")
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise APIError("请求体必须是 UTF-8 编码的合法 JSON") from exc
    if not isinstance(payload, dict):
        raise APIError("请求体必须是 JSON 对象")
    return payload


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def request_error(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
    if isinstance(exc, APIError):
        status, message = exc.status, str(exc)
    elif isinstance(exc, ValueError):
        status, message = HTTPStatus.BAD_REQUEST, str(exc)
    elif isinstance(exc, (FileNotFoundError, KeyError)):
        status, message = HTTPStatus.NOT_FOUND, str(exc)
    elif isinstance(exc, PermissionError):
        status, message = HTTPStatus.FORBIDDEN, str(exc)
    else:
        status, message = HTTPStatus.INTERNAL_SERVER_ERROR, "服务器内部错误，请查看服务日志"
        LOGGER.error("Unhandled request error", exc_info=(type(exc), exc, exc.__traceback__))
    json_response(handler, {"error": message}, status)
