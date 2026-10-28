from __future__ import annotations

import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from course_project.http_support import APIError, MAX_REQUEST_BYTES
from course_project.jobs import JobManager
from course_project.validation import bounded_int, bounded_float, boolean_value
from course_project import web_app


class QuietHandler(web_app.CourseRequestHandler):
    def log_message(self, *args):
        pass


class HTTPTestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_app.CourseHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method or ("POST" if body is not None else "GET"), path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


class HTTPBoundaryTests(HTTPTestServer):
    def test_home_and_static_assets(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.css", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        for path, content_type in (("app.css", "text/css"), ("app.js", "text/javascript")):
            status, headers, body = self.request(f"/static/{path}")
            self.assertEqual(status, 200)
            self.assertIn(content_type, headers["Content-Type"])
            self.assertGreater(len(body), 100)
        self.assertEqual(self.request("/static/../web_app.py")[0], 404)

    def test_json_contract_and_limits(self):
        for raw in (b"{bad", b"[]", b"null", b'{"num_tags":NaN}', b"\xff"):
            with self.subTest(raw=raw):
                status, _, body = self.request("/api/selfopt/validate", raw, {"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(body))
                self.assertNotIn("traceback", json.loads(body))
        self.assertEqual(self.request("/api/selfopt/validate", b"{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/selfopt/validate", b"", {"Content-Length": str(MAX_REQUEST_BYTES + 1)})[0], 413)
        self.assertEqual(self.request("/api/selfopt/validate", b"", {"Content-Length": "-1"})[0], 400)

    def test_missing_and_invalid_paths_have_stable_statuses(self):
        for path in ("/does-not-exist", "/api/selfopt/file?path=test/missing.py", "/api/selfopt/job?id=missing"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/api/selfopt/file?path=../.git/config")[0], 400)
        self.assertEqual(self.request("/unknown", b"{}", {"Content-Type": "application/json"})[0], 404)

    def test_internal_errors_are_logged_and_not_returned(self):
        with patch.object(web_app, "selfopt_state", side_effect=RuntimeError("private-stack-detail")):
            with self.assertLogs("course_project.http_support", level="ERROR"):
                status, _, body = self.request("/api/selfopt/state")
        self.assertEqual(status, 500)
        self.assertNotIn(b"private-stack-detail", body)
        self.assertNotIn(b"traceback", body)

    def test_scene_output_preview_and_html_isolation(self):
        root = web_app.PROJECT_ROOT / "test" / "outputs"
        with TemporaryDirectory(dir=root) as temporary:
            path = Path(temporary) / "preview.html"
            path.write_text("<h1>Preview</h1><script>parent.fetch('/api/database')</script>", encoding="utf-8")
            relative = path.relative_to(web_app.PROJECT_ROOT).as_posix()
            status, headers, _ = self.request(f"/api/selfopt/raw?path={relative}")
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Security-Policy"].startswith("sandbox;"))


class AssetImportTests(HTTPTestServer):
    def upload(self, name, content, destination="output", **extra):
        query = urlencode({"name": name, "destination": destination, **extra})
        return self.request("/api/selfopt/upload?" + query, content, {"Content-Type": "application/octet-stream"})

    def test_import_preview_pagination_and_download(self):
        with TemporaryDirectory() as temporary, patch.object(web_app, "PROJECT_ROOT", Path(temporary)), patch.object(web_app, "COURSE_ROOT", Path(temporary) / "course_project"), patch.object(web_app, "discover_scenes", return_value=["scene"]):
            content = ("协议,accuracy,time\n" + "\n".join(f"p{i},{i/100},{i*2}" for i in range(45))).encode("utf-8")
            status, _, body = self.upload("实验数据.csv", content)
            self.assertEqual(status, 201, body)
            relative = json.loads(body)["path"]
            url = urlencode({"path": relative, "page": 2})
            status, _, body = self.request("/api/selfopt/preview?" + url)
            preview = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(preview["total"], 45)
            self.assertEqual(len(preview["rows"]), 20)
            self.assertEqual(preview["rows"][0][0], "p20")
            status, headers, downloaded = self.request("/api/selfopt/download?" + urlencode({"path": relative}))
            self.assertEqual(status, 200)
            self.assertIn("attachment; filename*=UTF-8''", headers["Content-Disposition"])
            self.assertEqual(downloaded, content)
            self.assertEqual(self.upload("实验数据.csv", b"replacement")[0], 409)
            self.assertEqual((Path(temporary) / relative).read_bytes(), content)

    def test_reject_invalid_imports_and_paths(self):
        from course_project.assets import MAX_UPLOAD_BYTES
        with TemporaryDirectory() as temporary, patch.object(web_app, "PROJECT_ROOT", Path(temporary)), patch.object(web_app, "discover_scenes", return_value=["scene"]):
            for name in ("../out.txt", "a\\b.txt", "CON.txt", "bad.txt.", "a.exe"):
                self.assertEqual(self.upload(name, b"text")[0], 400, name)
            self.assertEqual(self.upload("data.json", b"{bad")[0], 400)
            self.assertEqual(self.upload("data.json", b'{"x":NaN}')[0], 400)
            self.assertEqual(self.upload("data.json", b"[" * 5000 + b"]" * 5000)[0], 400)
            self.assertEqual(self.upload("bad.txt", b"\xff")[0], 400)
            self.assertEqual(self.upload("empty.txt", b"")[0], 413)
            self.assertEqual(self.upload("data.txt", b"x", destination="unknown")[0], 400)
            self.assertEqual(self.upload("candidate.py", b"raise RuntimeError('must not execute')", destination="protocol", scene="scene", generation="generation_0")[0], 201)
            self.assertEqual(self.upload("bad-name.py", b"pass", destination="protocol", scene="scene")[0], 400)
            self.assertEqual(self.upload("中文.py", b"pass", destination="protocol", scene="scene")[0], 400)
            self.assertEqual(self.upload("good.py", b"pass", destination="protocol", scene="scene", generation="../bad")[0], 400)
            self.assertEqual(self.upload("good.py", b"pass", destination="protocol", scene="missing")[0], 400)
            self.assertEqual(self.request("/api/selfopt/upload?name=large.txt&destination=output", b"", {"Content-Type": "application/octet-stream", "Content-Length": str(MAX_UPLOAD_BYTES + 1)})[0], 413)
            self.assertEqual(self.request("/api/selfopt/download?path=../private.txt")[0], 400)

    def test_large_text_and_json_previews(self):
        from course_project.assets import preview_file
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.json"
            path.write_text('{"nested":{"accuracy":0.99},"samples":[1,2]}', encoding="utf-8")
            self.assertEqual(preview_file(path, "data.json")["data"]["nested"]["accuracy"], .99)
            path = Path(temporary) / "run.log"
            path.write_text("\n".join(f"line {i}" for i in range(250)), encoding="utf-8")
            result = preview_file(path, "run.log", 3)
            self.assertEqual(result["total"], 250)
            self.assertTrue(result["content"].startswith("line 200"))
            path.write_text("a" * 200000, encoding="utf-8")
            self.assertTrue(preview_file(path, "run.log")["truncated"])

    def test_full_catalog_and_nested_unrecognized_artifacts(self):
        with TemporaryDirectory() as temporary, patch.object(web_app, "PROJECT_ROOT", Path(temporary)), patch.object(web_app, "COURSE_ROOT", Path(temporary) / "course_project"), patch.object(web_app, "discover_scenes", return_value=["scene"]):
            root = Path(temporary)
            outputs = root / "scene/outputs/run"
            outputs.mkdir(parents=True)
            for index in range(245):
                (outputs / f"result_{index}.csv").write_text("x,y\n1,2", encoding="utf-8")
            (outputs / "array.npz").write_bytes(b"binary artifact")
            experiments = root / "Experiments/nested/run"
            experiments.mkdir(parents=True)
            (experiments / "figure.eps").write_bytes(b"vector artifact")
            self.assertEqual(len(web_app._output_artifact_rows()), 246)
            self.assertEqual(web_app._experiment_artifact_rows()[0]["name"], "figure.eps")
            status, _, body = self.request("/api/selfopt/preview?path=scene/outputs/run/array.npz")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "npz")
            content = b"# " + b"x" * 950000
            status, _, body = self.upload("large.py", content, destination="protocol", scene="scene")
            self.assertEqual(status, 201)
            relative = json.loads(body)["path"]
            status, _, body = self.request("/api/selfopt/preview?" + urlencode({"path": relative}))
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["truncated"])
            self.assertEqual(self.request("/api/selfopt/download?" + urlencode({"path": relative}))[2], content)


class AssetEditingTests(unittest.TestCase):
    def test_atomic_save_rejects_stale_editor_and_keeps_newer_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "base_protocol").mkdir()
            path = root / "base_protocol" / "example.py"
            path.write_text("# original\n", encoding="utf-8")
            with patch.object(web_app, "PROJECT_ROOT", root):
                original = web_app.read_project_file("base_protocol/example.py")
                saved = web_app.save_project_file({"path": original["path"], "revision": original["revision"], "content": "# newer\n"})
                self.assertNotEqual(saved["revision"], original["revision"])
                with self.assertRaises(APIError) as conflict:
                    web_app.save_project_file({"path": original["path"], "revision": original["revision"], "content": "# stale\n"})
                self.assertEqual(conflict.exception.status, 409)
                self.assertEqual(path.read_text(encoding="utf-8"), "# newer\n")
                self.assertFalse(list(path.parent.glob("*.tmp")))


class ParameterValidationTests(unittest.TestCase):
    def test_invalid_numbers_are_rejected_instead_of_silently_changed(self):
        for raw in (True, None, [], "invalid", 1.5, float("inf"), -1, 101):
            with self.subTest(value=raw), self.assertRaises(ValueError):
                bounded_int({"count": raw}, "count", 10, 1, 100)
        self.assertEqual(bounded_int({"count": "12"}, "count", 10, 1, 100), 12)
        self.assertEqual(bounded_int({}, "count", 10, 1, 100), 10)

    def test_float_and_boolean_types_are_checked(self):
        for raw in (True, None, "nan", float("inf"), -0.1):
            with self.subTest(value=raw), self.assertRaises(ValueError):
                bounded_float({"ratio": raw}, "ratio", .5, 0, 1)
        self.assertFalse(boolean_value({"preview": False}, "preview", True))
        with self.assertRaises(ValueError):
            boolean_value({"preview": "false"}, "preview", True)


class ProtocolAccountingTests(unittest.TestCase):
    def test_failure_after_sample_limit_keeps_actual_completed_count(self):
        protocol = Mock()
        protocol.execute.side_effect = [([299, 699], 19, 10.0, {})] * 7 + [RuntimeError("interrupted")]
        with patch.object(web_app, "_load_protocol_instance", return_value=protocol), \
             patch.object(web_app.ProtocolValidator, "validate", return_value=(True, "passed")), \
             patch.object(web_app, "_profile_hit", return_value=True):
            result = web_app.validate_selfopt_protocol({"scene": "test", "protocol": "ModeQuerySEM", "num_tags": 60, "total_runs": 10})
        self.assertEqual(result["completed_runs"], 7)
        self.assertEqual(len(result["samples"]), 5)
        self.assertEqual(result["avg_time_ms"], 10.0)
        self.assertEqual(result["pass_rate"], 100.0)


class BackgroundJobTests(unittest.TestCase):
    def test_queue_bound_history_and_snapshot_isolation(self):
        manager = JobManager(max_workers=1, max_pending=2, history_limit=1)
        release = threading.Event()
        started = threading.Event()

        def work(update):
            started.set()
            release.wait(timeout=5)
            update(progress=40, log="working")
            update(progress=20)
            return {"value": [1]}

        try:
            first = manager.submit("test", {"api_key": "secret", "scene": "test"}, work)
            self.assertTrue(started.wait(timeout=3))
            second = manager.submit("test", {}, lambda update: {"ok": True})
            self.assertEqual(second["status"], "queued")
            self.assertEqual(manager.get(first["id"])["status"], "running")
            self.assertNotIn("api_key", first["payload"])
            with self.assertRaises(APIError) as full:
                manager.submit("test", {}, lambda update: {})
            self.assertEqual(full.exception.status, 503)
        finally:
            release.set()
            manager.shutdown()
        snapshot = manager.get(first["id"])
        self.assertEqual(snapshot["status"], "success")
        snapshot["result"]["value"].append(2)
        snapshot["logs"].append("client mutation")
        self.assertEqual(manager.get(first["id"])["result"]["value"], [1])
        self.assertNotIn("client mutation", manager.get(first["id"])["logs"])
        with patch("course_project.jobs.time.perf_counter", return_value=10**10):
            self.assertEqual(manager.get(first["id"])["elapsed_s"], snapshot["elapsed_s"])

    def test_failed_job_does_not_expose_exception_details(self):
        manager = JobManager()

        def fail(update):
            raise RuntimeError("private-exception-detail")

        with self.assertLogs("course_project.jobs", level="ERROR"):
            job = manager.submit("test", {}, fail)
            manager.shutdown()
        result = manager.get(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private-exception-detail", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
