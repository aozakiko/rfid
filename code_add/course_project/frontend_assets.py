"""Serve the built Vue application using the existing Python HTTP service."""
from pathlib import Path
from urllib.parse import unquote

from course_project.http_support import send_bytes

DIST_ROOT = Path(__file__).resolve().parent / "frontend" / "dist"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ico": "image/x-icon",
}


def serve_frontend(handler, route, *, root=None):
    root = (Path(root) if root else DIST_ROOT).resolve()
    if route not in {"/", "/ui", "/ui/", "/index.html"} and not route.startswith("/ui/"):
        return False
    if not (root / "index.html").is_file():
        if route.startswith("/ui"):
            raise FileNotFoundError("Vue 前端尚未构建，请在 course_project/frontend 中运行 npm ci 和 npm run build")
        return False
    relative = "index.html" if route in {"/", "/ui", "/ui/", "/index.html"} else unquote(route[4:])
    target = (root / relative).resolve()
    if root not in target.parents or target.suffix not in CONTENT_TYPES or not target.is_file():
        raise FileNotFoundError("前端资源不存在")
    send_bytes(handler, target.read_bytes(), CONTENT_TYPES[target.suffix])
    return True
