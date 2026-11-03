"""HTTP boundaries shared by the console's routes, without domain dependencies."""
from __future__ import annotations

import json
import logging
from uuid import uuid4
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 2_000_000


class APIError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST, *, code=None, stage=None, suggestion=None):
        super().__init__(message)
        self.status = status
        self.code, self.stage, self.suggestion = code, stage, suggestion


def error_details(exc, *, stage="request", request_id=None):
    """Public, credential-free error contract shared by HTTP and background jobs."""
    if isinstance(exc, TimeoutError):
        code, message, hint, retryable = "timeout", "运行超过时间预算。", "减少场景或重复次数，或增加时间预算后重试。", True
    elif isinstance(exc, APIError):
        code = exc.code or ("conflict" if exc.status == 409 else "invalid_request" if exc.status < 500 else "service_unavailable")
        message, hint, retryable = str(exc), exc.suggestion or "核对配置和当前运行状态后重试。", exc.status >= 500 or exc.status == 409
    elif isinstance(exc, ValueError):
        code, message, hint, retryable = "invalid_configuration", str(exc), "核对任务、协议能力和参数范围。", False
    elif isinstance(exc, (FileNotFoundError, KeyError)):
        code, message, hint, retryable = "resource_not_found", "所需候选或实验文件不存在。", "刷新候选列表，核对版本和运行记录。", False
    elif isinstance(exc, PermissionError):
        code, message, hint, retryable = "access_denied", "文件不可访问。", "使用项目内允许的资产路径。", False
    else:
        code, message, hint, retryable = "execution_failed", "任务执行失败。", "查看任务阶段和日志，凭错误编号定位服务日志。", True
    return {"code": code, "stage": getattr(exc, "stage", None) or stage, "message": message,
            "suggestion": hint, "retryable": retryable, "request_id": request_id or uuid4().hex[:12]}


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
    detail = error_details(exc)
    LOGGER.warning("Request %s failed: %s", detail["request_id"], detail["code"])
    json_response(handler, {"error": message, "error_info": detail, "schema_version": "1.1"}, status)
