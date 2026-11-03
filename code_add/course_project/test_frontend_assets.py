from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from course_project.frontend_assets import serve_frontend


class FakeHandler:
    def __init__(self):
        self.wfile = BytesIO()
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class FrontendAssetsTests(unittest.TestCase):
    def test_home_assets_and_legacy_api_boundaries(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "index.html").write_text('<div id="app"></div>', encoding="utf-8")
            (root / "assets" / "entry.js").write_text("export default 1", encoding="utf-8")
            for path, content_type in (("/", "text/html"), ("/ui/", "text/html"), ("/ui/assets/entry.js", "text/javascript")):
                handler = FakeHandler()
                self.assertTrue(serve_frontend(handler, path, root=root))
                self.assertEqual(handler.status, 200)
                self.assertIn(content_type, handler.headers["Content-Type"])
                self.assertEqual(handler.headers["X-Content-Type-Options"], "nosniff")
            for path in ("/api/selfopt/state", "/legacy", "/static/app.js"):
                self.assertFalse(serve_frontend(FakeHandler(), path, root=root))

    def test_traversal_missing_files_and_sourcemaps_are_not_served(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "dist"
            root.mkdir()
            (root / "index.html").write_text("vue", encoding="utf-8")
            (root.parent / "private.txt").write_text("private", encoding="utf-8")
            for path in ("/ui/../private.txt", "/ui/%2e%2e/private.txt", "/ui/%2f../private.txt", "/ui/C:/Windows/win.ini", "/ui/assets/missing.js", "/ui/entry.js.map"):
                with self.subTest(path=path), self.assertRaises(FileNotFoundError):
                    serve_frontend(FakeHandler(), path, root=root)

    def test_unbuilt_frontend_preserves_legacy_home(self):
        with TemporaryDirectory() as temporary:
            self.assertFalse(serve_frontend(FakeHandler(), "/", root=temporary))
            with self.assertRaises(FileNotFoundError):
                serve_frontend(FakeHandler(), "/ui/", root=temporary)
