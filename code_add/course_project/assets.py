"""Bounded asset previews and explicit, non-overwriting local file imports."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from course_project.http_support import APIError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TEXT_SUFFIXES = {".py", ".txt", ".log", ".md", ".json", ".csv", ".html"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"}


def _reject_constant(value: str):
    raise ValueError(f"无效的 JSON 数字: {value}")


def import_file(handler, root: Path, scenes: list[str], query: dict) -> dict:
    def value(key, default=""):
        return query.get(key, [default])[0]

    if handler.headers.get("Transfer-Encoding"):
        raise APIError("不支持分块上传")
    if handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/octet-stream":
        raise APIError("上传文件需要 application/octet-stream", 415)
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise APIError("文件长度无效") from exc
    if not 0 < length <= MAX_UPLOAD_BYTES:
        raise APIError("请选择非空文件，每个文件最多 10 MB", 413)
    body = handler.rfile.read(length)
    if len(body) != length:
        raise APIError("上传内容不完整")
    name = value("name")
    if (not name or len(name) > 160 or name != Path(name).name
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " ")) or name.startswith(".")
            or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I)):
        raise APIError("文件名无效，请使用普通文件名，不能包含路径")
    destination = value("destination")
    suffix = Path(name).suffix.lower()
    if destination in {"protocol", "prompt", "scene_output"}:
        scene = value("scene")
        if scene not in scenes:
            raise APIError("请选择已有场景")
        if destination == "protocol":
            generation = value("generation", "generation_0")
            if not re.fullmatch(r"generation_?\d{1,4}", generation):
                raise APIError("世代目录格式应为 generation_0")
            if suffix != ".py" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", Path(name).stem) or name == "__init__.py":
                raise APIError("协议需为 .py 文件，名称使用英文字母、数字和下划线，不能以数字开头")
            folder = root / scene / generation
        else:
            folder = root / scene / ("prompts" if destination == "prompt" else "outputs/uploads")
        allowed = {".py"} if destination == "protocol" else {".txt"} if destination == "prompt" else TEXT_SUFFIXES | MEDIA_SUFFIXES
    elif destination in {"experiment", "output"}:
        folder = root / ("Experiments/uploads" if destination == "experiment" else "course_project/outputs/uploads")
        allowed = (TEXT_SUFFIXES - {".py"}) | MEDIA_SUFFIXES
    else:
        raise APIError("未知的上传位置")
    if suffix not in allowed:
        raise APIError("此位置不支持该文件类型")
    target = (folder / name).resolve()
    if root.resolve() not in target.parents or folder.resolve() != target.parent:
        raise APIError("上传路径越界")
    if target.exists():
        raise APIError("已存在同名文件，请重命名后上传", 409)
    if suffix in TEXT_SUFFIXES or suffix == ".svg":
        try:
            content = body.decode("utf-8-sig")
            if "\x00" in content:
                raise ValueError("binary text")
            if suffix == ".json":
                json.loads(content, parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise APIError("文本需为 UTF-8 编码，JSON 文件需符合 JSON 格式") from exc
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as handle:
            handle.write(body)
    except FileExistsError as exc:
        raise APIError("已存在同名文件，请重命名后上传", 409) from exc
    return {"path": target.relative_to(root.resolve()).as_posix(), "name": name, "size": length}


def preview_file(path: Path, relative: str, page: int = 1) -> dict:
    suffix = path.suffix.lower()
    size = path.stat().st_size
    result = {"path": relative, "name": path.name, "size": size, "kind": suffix.lstrip("."), "page": page}
    if suffix not in TEXT_SUFFIXES or suffix == ".html":
        return result
    if size > MAX_UPLOAD_BYTES:
        return result | {"kind": "binary", "hint": "文件超过 10 MB，请下载查看完整内容"}
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise APIError("该文本不是 UTF-8 编码，请下载后查看") from exc
    if suffix == ".csv":
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        try:
            # Raise the per-field limit to match the bounded file preview limit.
            csv.field_size_limit(MAX_UPLOAD_BYTES)
            reader = csv.reader(io.StringIO(content), dialect)
            headers = next(reader, [])
            rows, total = [], 0
            start = (page - 1) * 20
            for row in reader:
                if start <= total < start + 20:
                    rows.append([cell[:4000] for cell in row[:60]])
                total += 1
            width = min(60, max([len(headers)] + [len(row) for row in rows]))
            headers = (headers + [f"列 {i + 1}" for i in range(len(headers), width)])[:60]
            return result | {"headers": [h[:500] for h in headers], "rows": rows, "total": total,
                             "page_size": 20, "columns_limited": width == 60}
        except csv.Error as exc:
            raise APIError("CSV 格式无法解析，请下载原始文件") from exc
    if suffix == ".json" and size <= 1_000_000:
        try:
            return result | {"data": json.loads(content, parse_constant=_reject_constant), "total": 1, "page_size": 1}
        except (ValueError, RecursionError):
            pass  # An existing malformed JSON is still inspectable as text.
    lines = content.splitlines()
    start = (page - 1) * 100
    selected = "\n".join(lines[start:start + 100])
    return result | {"kind": "text", "format": suffix.lstrip("."), "content": selected[:100_000],
                     "total": len(lines), "page_size": 100, "truncated": len(selected) > 100_000}
