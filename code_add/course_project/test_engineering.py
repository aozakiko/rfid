from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from base import Tag
from benchmark.evidence import comparison_rows, read_reports, regenerate
from benchmark.provenance import artifact_hashes, audit_manifest, reproducibility_key
from benchmark.reproduce import replay
from benchmark.run_config import run_config
from benchmark.runner import evaluate_run, write_results
from course_project import engineering_service as service
from course_project.test_infrastructure import HTTPTestServer
from course_project.test_run_config import config_payload
from generic_protocol import run_protocol_file
from generic_protocol.security import TagIsolationProxy
from generic_protocol.validation import semantic_result, validate_candidate
from stat_protocols.contracts import RunResult


SAFE_SOURCE = '''# 中文源码验证 UTF-8 边界
class Fixture:
    def _simulate_tag_response(self, tag):
        return tag.price
    def execute(self, tags, price_min, price_max):
        return [self._simulate_tag_response(tags[0])], 1, 0, {"frame_size": 1, "bits_per_slot": 1, "reader_bits": 1, "tag_bits": 1, "slots": 1, "frames": 1, "rounds": 1}
'''


def generator(num_tags, price_min, price_max):
    return [Tag(i, 1) for i in range(num_tags)], {}


class ValidationEvidenceTests(unittest.TestCase):
    def test_ai_uses_real_shared_validation_with_resource_evidence(self):
        from ai_protocol_optimizer import _validate_protocol
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.py"
            path.write_bytes(SAFE_SOURCE.encode("utf-8"))
            with patch("ai_protocol_optimizer._load_scene_task", return_value=(generator, {"PRICE_MIN": 1, "PRICE_MAX": 2})):
                result = _validate_protocol(path, "Fixture", "fixture", 20, "mode_query")
        self.assertEqual(result["final_decision"], "passed")
        self.assertTrue(result["smoke_hit"])
        self.assertEqual(result["semantic_pass_rate"], 100)
        self.assertFalse(result["benchmark_eligible"])
        self.assertIn("communication_cost_not_independently_measured", result["exclusion_reasons"])
        self.assertTrue(result["runs"][0]["resource_limits"]["memory_limit_enforced"])
        self.assertIsNotNone(result["runs"][0]["measured_wall_time_ms"])

    def test_failed_static_gate_never_runs_and_stages_are_not_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.py"
            path.write_text("import os\n", encoding="utf-8")
            with patch("generic_protocol.validation.run_protocol_file") as runner:
                result = validate_candidate(path, "Fixture", generator)
        runner.assert_not_called()
        self.assertEqual(result["final_decision"], "failed")
        self.assertEqual(result["completed_runs"], 0)
        stages = {s["id"]: s["status"] for s in result["validation_stages"]}
        self.assertEqual(stages["runtime_isolation"], "not_run")
        self.assertEqual(stages["task_semantics"], "not_run")

    def test_semantics_require_complete_modes_and_missing_truth_is_unavailable(self):
        tags = [Tag(0, 1), Tag(1, 2)]
        self.assertFalse(semantic_result([1], tags, {}, "mode_query")[1])
        self.assertTrue(semantic_result([1, 2], tags, {}, "mode_query")[1])
        self.assertIsNone(semantic_result([1], tags, {}, "missing_stocktaking")[1])
        self.assertIsNone(semantic_result([1], tags, {}, "custom_task")[1])

    def test_repr_and_str_do_not_leak_tag_value(self):
        proxy = TagIsolationProxy(Tag(7, 987654321))
        self.assertNotIn("987654321", repr(proxy))
        self.assertNotIn("987654321", str(proxy))

    def run_source(self, source, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.py"
            path.write_bytes(source.encode("utf-8"))
            return run_protocol_file(path, "Fixture", [Tag(0, 1)],
                {"tasks": ["legacy_mode_query"], "price_min": 1, "price_max": 2},
                security_mode="strict", **kwargs)["legacy_mode_query"]

    def test_numpy_file_io_is_blocked_by_runtime_audit(self):
        source = '''import numpy as np
class Fixture:
    def execute(self, tags, price_min, price_max):
        np.savetxt("forbidden.txt", [1, 2])
        return [1], 1, 0, {}
'''
        result = self.run_source(source)
        self.assertIn("runtime_operation_blocked", result.failure_reason)

    def test_native_file_api_cannot_bypass_audit_through_dynamic_getattr(self):
        result = self.run_source('''import numpy as np
class Fixture:
    def execute(self, tags, price_min, price_max):
        reader = getattr(np, "from" + "file")
        return [reader("forbidden.txt")], 1, 0, {}
''')
        self.assertIn("runtime_operation_blocked", result.failure_reason)

    def test_worker_memory_limit_bounds_allocation(self):
        source = '''class Fixture:
    def execute(self, tags, price_min, price_max):
        allocation = bytearray(1024 * 1024 * 1024)
        return [len(allocation)], 1, 0, {}
'''
        result = self.run_source(source, memory_limit_mb=256)
        self.assertTrue(result.failure_reason)
        self.assertTrue("MemoryError" in result.failure_reason or "subprocess_exit" in result.failure_reason)

    def test_worker_bounds_output_and_handles_system_exit(self):
        result = self.run_source('''class Fixture:
    def execute(self, tags, price_min, price_max):
        print("x" * 2100000)
        return [1], 1, 0, {}
''')
        self.assertIn("subprocess_output_limit", result.failure_reason)
        result = self.run_source('''class Fixture:
    def execute(self, tags, price_min, price_max):
        raise SystemExit(4)
''')
        self.assertIn("subprocess_exit_4", result.failure_reason)

    def test_malformed_or_spoofed_counters_are_not_accepted(self):
        for value in (-1, True, 1.5, "9"):
            with self.subTest(value=value):
                source = SAFE_SOURCE.replace('"reader_bits": 1', f'"reader_bits": {value!r}')
                result = self.run_source(source)
                self.assertIn("invalid communication counter", result.failure_reason)


class ReproducibleEvidenceTests(unittest.TestCase):
    def write_config(self, folder):
        path = folder / "config.json"
        path.write_text(json.dumps({**config_payload(), "seeds": [0, 1]}), encoding="utf-8")
        return path

    def test_replay_compares_frozen_results_and_audits_both_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            result = replay(self.write_config(folder), folder / "replay")
        self.assertTrue(result["passed"])
        self.assertEqual(result["deterministic_hashes"][0], result["deterministic_hashes"][1])

    def test_derived_reports_keep_raw_manifest_immutable_and_cover_four_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = self.write_config(folder)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["profiles"] = ["mean_estimation", "variance_estimation", "cardinality_estimation", "top_k"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            run_config(path, folder / "raw")
            manifest_bytes = (folder / "raw" / "manifest.json").read_bytes()
            generated = regenerate(folder / "raw", folder / "derived")
            rows = json.loads((folder / "derived" / "results_evidence.json").read_text(encoding="utf-8"))
            self.assertEqual({r["task"] for r in rows}, {"mean", "variance", "cardinality", "top_k"})
            self.assertEqual(len(generated), 5)
            self.assertTrue(audit_manifest(folder / "raw")["passed"])
            self.assertEqual((folder / "raw" / "manifest.json").read_bytes(), manifest_bytes)
            with self.assertRaises(ValueError):
                regenerate(folder / "raw", folder / "raw" / "derived")

    def test_config_and_dashboard_mutation_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            dashboard = folder / "dashboard.json"
            dashboard.write_text("{}", encoding="utf-8")
            manifest = {"config": {"noise_rate": 0}, "source_hashes": {"source.py": "abc"}}
            manifest["artifact_hashes"] = artifact_hashes(folder)
            manifest["reproducibility_key"] = reproducibility_key(manifest)
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(audit_manifest(folder)["passed"])
            dashboard.write_text('{"changed":1}', encoding="utf-8")
            self.assertIn("dashboard.json", audit_manifest(folder)["mismatched"])
            manifest["config"]["noise_rate"] = .1
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(audit_manifest(folder)["reproducibility_key_match"])

    def test_legacy_manifests_are_unavailable_instead_of_passing_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(audit_manifest(folder)["status"], "not_available")

    def test_front_excludes_failures_and_does_not_mix_seeds_data_or_domains(self):
        def row(name, bits, seed=0, digest="data", **metadata):
            return evaluate_run(RunResult(name, "mean", 1, 1, bits, 1, 1, 1, 1, seed,
                metadata={"distribution": "uniform", "num_tags": 8, "dataset_hash": digest, **metadata}))
        rows = comparison_rows([row("fast", 1), row("slow", 9), row("other_seed", 9, seed=1),
                                row("other_data", 9, digest="different"), row("other_domain", 9, price_max=99),
                                row("self_reported", 1, cost_origin="candidate_self_reported"),
                                row("rejected", 1, validation_gate="rejected")])
        by_name = {r["protocol"]: r for r in rows}
        self.assertFalse(by_name["slow"]["is_pareto"])
        for name in ("fast", "other_seed", "other_data", "other_domain"):
            self.assertTrue(by_name[name]["is_pareto"])
        self.assertFalse(by_name["self_reported"]["pareto_eligible"])
        self.assertFalse(by_name["rejected"]["pareto_eligible"])
        self.assertIsNone(by_name["fast"]["error_ci95_low"])


class EngineeringServiceTests(unittest.TestCase):
    def test_lineage_only_uses_recorded_parents_and_detects_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "scene" / "generation_1"
            folder.mkdir(parents=True)
            (root / "scene" / "Task.py").write_text("", encoding="utf-8")
            source = folder / "child.py"
            source.write_bytes(SAFE_SOURCE.encode())
            record = {"candidate_id": "child-id", "protocol_name": "Fixture", "parents": ["historical-parent"],
                      "source_hash": hashlib.sha256(source.read_bytes()).hexdigest(), "validation_result": {"final_decision": "failed"}}
            (folder / "child.candidate.json").write_text(json.dumps(record), encoding="utf-8")
            with patch.object(service, "ROOT", root), patch.object(service, "EXPERIMENT_ROOT", root / "Experiments"):
                data = service.candidate_lineage({"scene": ["scene"]})
                self.assertTrue(data["nodes"][0]["source_hash_match"])
                self.assertEqual(data["edges"][0]["resolution"], "unrecorded")
                source.write_text("changed", encoding="utf-8")
                self.assertFalse(service.candidate_lineage()["nodes"][0]["source_hash_match"])

    def test_config_job_uses_existing_runner_and_preserves_raw_artifacts(self):
        class ImmediateManager:
            def submit(self, kind, payload, work):
                return {"kind": kind, "result": work(lambda **changes: None)}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(service, "EXPERIMENT_ROOT", root), patch.object(service, "_relative", side_effect=lambda p: str(p)):
                result = service.start_config(ImmediateManager(), {"config": config_payload()})
                data = service.config_run({"run_id": [result["result"]["run_id"]]})
                rebuilt = service.start_rebuild(ImmediateManager(), {"run_id": data["run_id"]})
            self.assertTrue(data["audit"]["passed"])
            self.assertTrue(rebuilt["result"]["audit"]["passed"])
            self.assertIn("task_specs", data["manifest"])
            self.assertTrue(any(a["kind"] == "pdf" for a in data["artifacts"]))


class EngineeringHTTPTests(HTTPTestServer):
    def test_routes_reject_invalid_configs_and_paths(self):
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/statistics/run-config", b'{"config":{"seeds":[0,0]}}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/config-runs?run_id=../secret")[0], 400)
        self.assertEqual(self.request("/api/statistics/rebuild", b'{"run_id":"../secret"}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/run-pipeline", b'{"config":{"seeds":[0,0]}}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/pipelines?run_id=../secret")[0], 400)
        self.assertEqual(self.request("/api/selfopt/candidates?scene=../secret")[0], 400)
        self.assertEqual(self.request("/api/selfopt/candidates")[0], 200)


if __name__ == "__main__":
    unittest.main()
