"""Offline integration tests for frozen candidates and post-search evaluation."""
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from benchmark.scenarios import generate_tags
from course_project import research_service as service, web_app, statistics_service
from course_project.http_support import APIError
from course_project.jobs import JobCancelled, JobManager
from course_project.task_process import run_process
from course_project.test_infrastructure import HTTPTestServer
from course_project.test_optimization_loop import measured_source
from course_project.optimization_control import OptimizationStopped
from stat_protocols.contracts import TaskSpec


class Progress:
    def __init__(self, cancel_after=None):
        self.changes = []
        self.cancel_after = cancel_after

    def __call__(self, **changes):
        self.changes.append(changes)

    def check_cancelled(self):
        if self.cancel_after is not None and len(self.changes) >= self.cancel_after:
            raise JobCancelled("offline_cancel")


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, name, value in ((service, "ROOT", self.root), (statistics_service, "ROOT", self.root),
                (statistics_service, "EXPERIMENT_ROOT", self.root / "Experiments/stat_protocols")):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)
        (self.root / "fixture/generation_0").mkdir(parents=True)
        (self.root / "fixture/Task.py").write_text("# fixture data generator", encoding="utf-8")
        (self.root / "base_protocol").mkdir()
        self.base = self.make_candidate("Baseline", baseline=True, repeats=2)
        self.other = self.make_candidate("Improved", repeats=1)

    def make_candidate(self, name, *, baseline=False, repeats=1, source=None):
        path = self.root / ("base_protocol" if baseline else "fixture/generation_0") / (name + ".py")
        path.write_text(source or measured_source(name, slots=256, repeats=repeats), encoding="utf-8")
        return service.register({"path": str(path), "scene": "fixture", "task_profile": "mean_estimation"},
            search_hashes=None if baseline else [])

    def payload(self, **extra):
        return {"candidate_ids": [self.base["id"], self.other["id"]], "baseline_id": self.base["id"],
            "task_profile": "mean_estimation", "sizes": [32], "distributions": ["uniform"],
            "validation_seeds": [71301], "blind_seeds": [81501],
            "perturbations": [{"missing_rate": 0, "noise_rate": 0, "extreme_rate": 0}],
            "price_min": 1, "price_max": 8, **extra}

    def run_evaluation(self, payload=None, progress=None):
        records, config = service.normalize(payload or self.payload())
        folder = service._store() / "runs" / uuid4().hex
        result = service.run_evaluation(folder, records, config, progress or Progress())
        return folder, result, service.runs({"id": [folder.name]})

    def test_registry_freezes_versions_and_reports_tampering(self):
        source = self.root / self.other["source_path"]
        self.assertEqual(service.register({"path": str(source), "scene": "fixture", "task_profile": "mean_estimation"})["id"], self.other["id"])
        source.write_text(source.read_text(encoding="utf-8") + "\n# new version", encoding="utf-8")
        newer = service.register({"path": str(source), "scene": "fixture", "task_profile": "mean_estimation"})
        self.assertNotEqual(newer["id"], self.other["id"])
        self.assertFalse(newer["search_history_known"])
        frozen = self.root / self.other["snapshot_path"]
        self.assertNotEqual(frozen.read_bytes(), source.read_bytes())
        frozen.write_text("tampered", encoding="utf-8")
        with self.assertRaises(APIError):
            service.candidate(self.other["id"])
        listed = service.list_candidates()
        self.assertIn(self.other["id"], [row["id"] for row in listed["problems"]])

    def test_registration_rejects_outside_assets_and_empty_validation_has_unknown_history(self):
        with self.assertRaises(ValueError):
            service.register({"path": "../outside.py", "scene": "fixture"})
        path = self.root / "fixture/generation_0/Unseen.py"
        path.write_text(measured_source("Unseen"), encoding="utf-8")
        record = service.register_generated(path, "fixture", "mean_estimation", report={"runs": []})
        self.assertFalse(record["search_history_known"])
        with self.assertRaises(APIError):
            service.register({"path": str(path), "scene": "fixture", "expected_sha256": "stale"})

    def test_validation_exposure_is_recorded_without_inventing_search_history(self):
        source = self.root / "fixture/generation_0/Imported.py"
        source.write_text(measured_source("Imported"), encoding="utf-8")
        record = service.register({"path": str(source), "scene": "fixture", "task_profile": "mean_estimation"})
        service.record_validation_usage(record["id"], {"source_sha256": record["source_sha256"], "runs": [{"dataset_hash": "input-hash"}]})
        updated = service.candidate(record["id"])
        self.assertFalse(updated["search_history_known"])
        self.assertEqual(updated["validation_dataset_hashes"], ["input-hash"])
        with self.assertRaises(APIError):
            service.record_validation_usage(record["id"], {"source_sha256": "different-source"})

    def test_parameter_errors_rejected_before_submission(self):
        for extra in ({"candidate_ids": [{}]}, {"candidate_ids": [True]}, {"sizes": [True]},
                {"blind_seeds": [71301]}, {"perturbations": [{"noise_rate": float("nan")}]},
                {"task_profile": "mode_query"}, {"baseline_id": "not-selected"},
                {"sizes": [20, 50, 80], "validation_seeds": list(range(10)), "blind_seeds": list(range(100, 110)), "distributions": ["uniform", "gaussian", "multi_peak"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                service.normalize(self.payload(**extra))

    def test_real_matrix_uses_shared_frozen_inputs_and_audited_costs(self):
        # Change the editable file: execution must still use the registered bytes.
        (self.root / self.other["source_path"]).write_text("invalid edited file", encoding="utf-8")
        folder, result, detail = self.run_evaluation()
        self.assertEqual(result["completed"], 4)
        self.assertEqual(result["independence_status"], "verified_against_recorded_history")
        self.assertTrue(detail["audit"]["passed"])
        self.assertTrue(all(row["eligible"] for row in detail["rows"]))
        improvements = [row for row in detail["rows"] if row["candidate_id"] == self.other["id"]]
        self.assertTrue(all(abs(row["cost_reduction_fraction"] - .5) < .00001 for row in improvements))
        for row in improvements:
            baseline = next(other for other in detail["rows"] if other["split"] == row["split"] and other["candidate_id"] == self.base["id"])
            self.assertEqual(row["dataset_hashes"], baseline["dataset_hashes"])
            self.assertLess(row["pass_ci95"][0], row["pass_rate"])
        rows = json.loads((folder / "results.json").read_text(encoding="utf-8"))["rows"]
        self.assertEqual(rows[0]["data_spec"]["tasks"], ["mean"])
        self.assertTrue(all(row["report"]["protocol_path"].startswith(str(folder)) for row in rows))
        (folder / "summary.csv").write_text("tampered", encoding="utf-8")
        detail = service.runs({"id": [folder.name]})
        self.assertFalse(detail["audit"]["passed"])
        self.assertTrue(all(not row["eligible"] and row["cost_reduction_fraction"] is None for row in detail["rows"]))

    def test_overlap_excludes_result_and_never_claims_independence(self):
        tags = generate_tags(TaskSpec(tasks=("mean",), num_tags=32, distribution="uniform", price_max=8, seed=81501))
        digest = hashlib.sha256(json.dumps([(tag.id, tag.price) for tag in tags], separators=(",", ":")).encode()).hexdigest()
        service.register({"path": self.other["source_path"], "scene": "fixture", "task_profile": "mean_estimation"}, search_hashes=[digest])
        _, _, detail = self.run_evaluation()
        self.assertEqual(detail["manifest"]["independence_status"], "overlap_detected")
        self.assertTrue(all(not row["eligible"] for row in detail["rows"] if row["split"] == "blind"))

    def test_static_failure_does_not_establish_independence(self):
        bad = self.make_candidate("Blocked", source="import os\nclass Blocked: pass\n")
        _, result, detail = self.run_evaluation(self.payload(candidate_ids=[bad["id"]], baseline_id=bad["id"]))
        self.assertEqual(result["independence_status"], "not_established")
        self.assertTrue(all(not row["eligible"] for row in detail["rows"]))
        self.assertTrue(all("missing_or_repeated_datasets" in row["exclusion_reasons"] for row in detail["rows"]))

    def test_cancellation_preserves_completed_cases_and_excludes_ranking(self):
        records, config = service.normalize(self.payload())
        folder = service._store() / "runs" / uuid4().hex
        with self.assertRaises(JobCancelled):
            service.run_evaluation(folder, records, config, Progress(cancel_after=1))
        detail = service.runs({"id": [folder.name]})
        self.assertEqual(detail["manifest"]["status"], "cancelled")
        self.assertEqual(detail["manifest"]["completed"], 1)
        self.assertEqual(detail["manifest"]["independence_status"], "not_established")
        self.assertTrue(detail["audit"]["passed"])
        self.assertFalse(detail["rows"][0]["eligible"])

    def test_native_json_dispatch_keeps_engine_and_frozen_record_separate(self):
        saved = statistics_service.save_candidate({"candidate_id": "fixture", "model": "offline-fixture",
            "prompt_hash": "fixture-prompt", "response_hash": "fixture-response", "genotype": {
            "operator": "anonymous_histogram", "rounds": 2, "slots_per_value": 32, "threshold": None}})
        record = service.register({"family": "statistics", "path": saved["path"]})
        with self.assertRaises(APIError) as mixed:
            service.normalize(self.payload(candidate_ids=[record["id"], self.base["id"]]))
        self.assertEqual(mixed.exception.code, "incompatible_candidate_families")
        (self.root / saved["path"]).write_text("edited", encoding="utf-8")
        with patch("course_project.engineering_service.start_pipeline", return_value={"id": "fixture-job"}) as pipeline:
            result = service.start_evaluation(object(), self.payload(candidate_ids=[record["id"]], baseline_id=record["id"]))
        self.assertEqual(result["id"], "fixture-job")
        payload = pipeline.call_args.args[1]
        self.assertEqual(payload["config"]["llm_candidate"]["genotype"]["rounds"], 2)
        self.assertEqual(payload["config"]["protocols"], ["polling", "shared_sketch", "llm_random"])
        self.assertEqual(payload["max_seconds"], 1800)


class TaskExecutionTests(unittest.TestCase):
    def test_cancelling_queued_suite_releases_its_reserved_lock(self):
        manager = JobManager(max_workers=1)
        release, started = threading.Event(), threading.Event()
        def occupy(update):
            started.set()
            release.wait(5)
        manager.submit("occupied", {}, occupy)
        self.assertTrue(started.wait(2))
        lock = threading.Lock()
        try:
            with patch.object(statistics_service, "SUITE_LOCK", lock):
                job = statistics_service.start_suite(manager, {"seed_count": 2, "sizes": [1000]})
            self.assertTrue(lock.locked())
            manager.cancel(job["id"])
        finally:
            release.set()
            manager.shutdown()
        self.assertEqual(manager.get(job["id"])["status"], "cancelled")
        self.assertFalse(lock.locked())

    def test_silent_process_cancellation_does_not_wait_for_output(self):
        manager = JobManager(max_workers=1)
        started = threading.Event()
        def work(update):
            started.set()
            return run_process([sys.executable, "-u", "-c", "import time; time.sleep(30)"],
                cwd=Path.cwd(), timeout=40, update=update)
        job = manager.submit("silent_fixture", {}, work)
        self.assertTrue(started.wait(2))
        time.sleep(.3)
        begin = time.monotonic()
        manager.cancel(job["id"])
        manager.shutdown()
        self.assertLess(time.monotonic() - begin, 8)
        result = manager.get(job["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["result"]["partial"])

    def test_timeout_has_structured_stage_error_and_partial_log(self):
        manager = JobManager()
        with self.assertLogs("course_project.jobs", level="ERROR"):
            job = manager.submit("timeout_fixture", {"api_key": "offline-test-secret"}, lambda update:
                run_process([sys.executable, "-u", "-c", "import time; print('offline-test-secret'); time.sleep(30)"],
                    cwd=Path.cwd(), timeout=.5, update=update, stage="blind"))
            manager.shutdown()
        result = manager.get(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_info"]["code"], "timeout")
        self.assertEqual(result["error_info"]["stage"], "blind")
        self.assertTrue(result["result"]["partial"])
        self.assertNotIn("offline-test-secret", json.dumps(result))

    def test_single_generation_control_cancellation_is_cancelled_job(self):
        manager = JobManager()
        with patch.object(web_app, "JOB_MANAGER", manager), patch.object(web_app, "run_ai_generation", side_effect=OptimizationStopped("cancelled")):
            job = web_app.start_background_task("ai_generation", {"run_mode": "preview"})
            manager.shutdown()
        self.assertEqual(manager.get(job["id"])["status"], "cancelled")


class ResearchHTTPTests(HTTPTestServer):
    def test_capabilities_and_structured_rejection_contract(self):
        status, _, body = self.request("/api/research/capabilities")
        self.assertEqual(status, 200)
        caps = json.loads(body)
        self.assertNotEqual(caps["families"]["python"]["engine"], caps["families"]["statistics"]["engine"])
        self.assertEqual(self.request("/api/research/candidates")[0], 200)
        status, _, body = self.request("/api/research/evaluate-async", b'{"candidate_ids":[{}]}', {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        error = json.loads(body)["error_info"]
        self.assertEqual(error["code"], "invalid_configuration")
        self.assertTrue(error["suggestion"])
        self.assertTrue(error["request_id"])

    def test_validation_and_scene_experiment_have_background_routes(self):
        manager = JobManager()
        with patch.object(web_app, "JOB_MANAGER", manager), patch.object(web_app, "validate_selfopt_protocol", return_value={"validated": True}), patch.object(web_app, "run_experiment_task", return_value={"plotted": True}):
            for path, kind in (("/api/selfopt/validate-async", "protocol_validation"), ("/api/selfopt/experiments-async", "scene_experiment")):
                status, _, body = self.request(path, b'{}', {"Content-Type": "application/json"})
                self.assertEqual(status, 202)
                self.assertEqual(json.loads(body)["job"]["kind"], kind)
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
