from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_protocol_optimizer import TASK_GUIDANCE, task_guidance
from ai_protocol_optimizer import _parent_evidence
from benchmark.provenance import audit_manifest
from benchmark.run_config import run_config
from course_project import web_app
from course_project.candidate_evaluation import evaluate_protocol, select_population
from course_project.test_run_config import config_payload
from course_project.jobs import JobManager
from prompt_generation import get_protocol_code


def protocol_source(name, value="price_min", time=100):
    return ("from base import IProtocol\n"
            f"class {name}(IProtocol):\n"
            "    def execute(self, tags, price_min, price_max):\n"
            f"        return [{value}], 0, {time}, {{'frame_size': 16, 'bits_per_slot': 1}}\n")


class PlatformIterationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scene = self.root / "fixture"
        self.folder = scene / "generation_0"
        self.folder.mkdir(parents=True)
        (scene / "Task.py").write_text(
            "from base import Tag\ndef generate_tags(n, low, high):\n"
            "    return [Tag(i, low) for i in range(n)], {}\n", encoding="utf-8")
        for name in ("PoorA", "PoorB"):
            (self.folder / f"{name}.py").write_text(protocol_source(name, "price_max"), encoding="utf-8")
        for target, value in (("PROJECT_ROOT", self.root), ("selfopt_state", lambda: {}), ("store_ai_job", lambda **kwargs: 1)):
            p = patch.object(web_app, target, value)
            p.start()
            self.addCleanup(p.stop)

    def payload(self, **updates):
        return {"scene": "fixture", "parents": ["PoorA", "PoorB"],
                "prefix": "child", "iterations": 2, "dry_run": False,
                "task_profile": "mode_query", "validate_num_tags": 20,
                "evaluation_runs": 2, "evaluation_price_max": 8,
                "base_url": "https://fixture.invalid", "model": "offline-fixture",
                "api_key_value": "fixture-secret", **updates}

    def generate(self, **kwargs):
        name = kwargs["protocol_name"]
        path = self.folder / f"{name}.py"
        # A useful first child followed by a bad child claiming zero cost.
        path.write_text(protocol_source(name, "price_min" if name.endswith("01") else "price_max",
                                        time=9999 if name.endswith("01") else 0), encoding="utf-8")
        return {"status": "success", "target_file": str(path), "protocol_exists": True}

    def test_real_sandbox_evaluation_drives_next_parent_and_rejects_bad_child(self):
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=self.generate) as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload())
        self.assertIn("child_01", {workflow.call_args_list[1].kwargs["parent_a"], workflow.call_args_list[1].kwargs["parent_b"]})
        self.assertIn('"quality_score": 1.0', workflow.call_args_list[1].kwargs["requirement_text"])
        self.assertTrue(result["iterations"][0]["selection"]["retained"])
        self.assertFalse(result["iterations"][1]["selection"]["retained"])
        self.assertIn("child_01", result["population"])
        record_path = self.root / result["evidence_path"]
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "success")
        self.assertNotIn("fixture-secret", record_path.read_text(encoding="utf-8"))
        for name, digest in record["artifact_hashes"].items():
            self.assertEqual(hashlib.sha256((record_path.parent / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(record["rounds"][0]["evaluation"]["dataset_hashes"], record["rounds"][1]["evaluation"]["dataset_hashes"])

    def test_model_failure_retains_failed_run_record(self):
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                web_app.run_selfopt_auto_iterate(self.payload())
        path = next((self.root / "fixture/outputs/optimization_runs").glob("*/run.json"))
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["rounds"], [])

    def test_custom_task_does_not_receive_fake_quality_ranking(self):
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=self.generate), patch.object(web_app, "evaluate_protocol") as evaluate:
            result = web_app.run_selfopt_auto_iterate(self.payload(task_profile="custom_task", custom_task_text="Return the requested custom business summary"))
        evaluate.assert_not_called()
        self.assertEqual(result["selection_mode"], "generation_only")

    def test_existing_iteration_prefix_is_not_overwritten(self):
        path = self.folder / "child_01.py"
        path.write_text("existing", encoding="utf-8")
        with self.assertRaises(ValueError):
            web_app.run_selfopt_auto_iterate(self.payload())
        self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    def test_batch_uses_shared_validator_and_keeps_partial_mode_answers_wrong(self):
        (self.folder.parent / "Task.py").write_text(
            "from base import Tag\ndef generate_tags(n, low, high):\n"
            "    return [Tag(i, low + i % 2) for i in range(n)], {}\n", encoding="utf-8")
        (self.folder / "Partial.py").write_text(protocol_source("Partial"), encoding="utf-8")
        result = web_app.run_selfopt_batch_test({"scene": "fixture", "protocols": ["Partial"],
            "total_runs": 2, "num_tags": 20, "price_max": 8})
        row = result["results"][0]
        self.assertEqual(row["pass_rate"], 0)
        self.assertAlmostEqual(row["quality_score"], 2 / 3)
        record = json.loads((self.root / result["evidence_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(record["evaluations"]["Partial"]["validation"]["runs"]), 2)

    def test_old_cli_also_rejects_partial_modes_and_reuses_data(self):
        from base import Tag
        from Test import run_performance_test
        from contextlib import redirect_stdout
        from io import StringIO
        import random
        observed = []
        def generator(n, low, high):
            marker = random.random()
            observed.append(marker)
            return [Tag(i, low + i % 2) for i in range(n)], {}
        class Partial:
            def execute(self, tags, low, high):
                return [low], 0, 1, {}
        config = {"num_tags": 20, "price_min": 1, "price_max": 8, "total_runs": 2, "scene": "fixture"}
        with redirect_stdout(StringIO()):
            result = run_performance_test("Partial", Partial(), config, generator)
            run_performance_test("Partial", Partial(), config, generator)
        self.assertEqual(result["pass_rate"], 0)
        self.assertEqual(observed[:2], observed[2:])

    def test_strict_gate_excludes_direct_attribute_access(self):
        path = self.folder / "Cheat.py"
        path.write_text(protocol_source("Cheat", "tags[0].price"), encoding="utf-8")
        generator, _ = web_app._load_scene_task("fixture")
        item = evaluate_protocol(path, "Cheat", generator, {"task_profile": "mode_query",
            "security_mode": "strict", "total_runs": 1, "num_tags": 20})
        self.assertFalse(item["summary"]["selection_eligible"])

    def test_batch_keeps_evidence_but_disables_ranking_when_generator_changes_data(self):
        (self.folder.parent / "Task.py").write_text(
            "from base import Tag\nstate = 0\ndef generate_tags(n, low, high):\n"
            "    global state\n    state += 1\n"
            "    return [Tag(i, state) for i in range(n)], {}\n", encoding="utf-8")
        result = web_app.run_selfopt_batch_test({"scene": "fixture", "protocols": ["Poor*"],
            "num_tags": 20, "total_runs": 1, "price_max": 8})
        self.assertFalse(result["datasets_comparable"])
        self.assertTrue(all(row["rank"] is None for row in result["results"]))
        self.assertTrue((self.root / result["evidence_path"]).exists())

    def test_selection_refuses_mismatched_data_and_retains_incumbent_on_tie(self):
        def item(h):
            return {"summary": {"selection_eligible": True, "quality_pass_rate": 1, "quality_score": 1, "dataset_hashes": [h]}}
        items = {"parent": item("same"), "child": item("same")}
        self.assertEqual(select_population(list(items), items, 1), ["parent"])
        items["child"] = item("different")
        with self.assertRaises(ValueError):
            select_population(list(items), items, 1)

    def test_top_k_has_executable_prompt_guidance(self):
        self.assertIn("3", task_guidance("top_k")["output"])
        self.assertIn("top_k", TASK_GUIDANCE)

    def test_http_comparison_runs_in_background_and_returns_evidence(self):
        from course_project.test_infrastructure import QuietHandler
        server = web_app.CourseHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        manager = JobManager(max_workers=1)
        def request(method, path, payload=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=10)
            try:
                connection.request(method, path, json.dumps(payload) if payload is not None else None,
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            with patch.object(web_app, "JOB_MANAGER", manager):
                status, submitted = request("POST", "/api/selfopt/batch-test-async", {
                    "scene": "fixture", "protocols": ["PoorA"], "total_runs": 1, "num_tags": 20, "price_max": 8})
                self.assertEqual(status, 202)
                deadline = time.monotonic() + 10
                while True:
                    _, response = request("GET", "/api/selfopt/job?id=" + submitted["job"]["id"])
                    job = response["job"]
                    if job["status"] in {"success", "failed"}:
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                self.assertEqual(job["status"], "success")
                self.assertTrue((self.root / job["result"]["evidence_path"]).exists())
                self.assertEqual(job["result"]["results"][0]["pass_rate"], 0)
        finally:
            manager.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_parent_prompt_and_evidence_use_same_latest_generation(self):
        scene = self.folder.parent
        for generation in (9, 10):
            folder = scene / f"generation_{generation}"
            folder.mkdir()
            (folder / "Same.py").write_text(f"# generation {generation}\n", encoding="utf-8")
        latest = web_app._find_protocol_file("fixture", "Same")
        self.assertEqual(get_protocol_code("Same", scene), latest.read_text(encoding="utf-8"))
        evidence = _parent_evidence(scene, ("Same",))[0]
        self.assertEqual(evidence["source_sha256"], hashlib.sha256(latest.read_bytes()).hexdigest())

    def test_concurrent_source_change_aborts_selection_with_failed_record(self):
        def generate_and_edit(**kwargs):
            result = self.generate(**kwargs)
            (self.folder / "PoorA.py").write_text(protocol_source("PoorA", "price_min"), encoding="utf-8")
            return result
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate_and_edit):
            with self.assertRaises(ValueError):
                web_app.run_selfopt_auto_iterate(self.payload())
        path = next((self.root / "fixture/outputs/optimization_runs").glob("*/run.json"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "failed")

    def test_config_runner_consumes_and_freezes_llm_candidate(self):
        candidate = {"candidate_id": "offline-fixture", "model": "offline-fixture",
            "prompt_hash": "fixture-prompt", "response_hash": "fixture-response",
            "genotype": {"operator": "anonymous_histogram", "rounds": 1, "slots_per_value": 32, "threshold": None}}
        payload = {**config_payload(), "profiles": ["mean_estimation"],
                   "protocols": ["llm_random"], "llm_candidate": candidate}
        config = self.root / "config.json"
        config.write_text(json.dumps(payload), encoding="utf-8")
        output = self.root / "benchmark"
        manifest = run_config(config, output)
        reports = json.loads((output / "results.json").read_text(encoding="utf-8"))["reports"]
        self.assertEqual(reports[0]["status"], "success")
        self.assertTrue(manifest["llm_candidate_sha256"])
        self.assertEqual(json.loads((output / "llm_candidate.json").read_text(encoding="utf-8")), candidate)
        self.assertTrue(audit_manifest(output)["passed"])
        payload.pop("llm_candidate")
        config.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            run_config(config, self.root / "missing-candidate")


if __name__ == "__main__":
    unittest.main()
