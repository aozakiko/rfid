"""Offline integration tests for measured optimization, limits and recovery."""
import hashlib
import json
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from base import Tag
from backend_tools.openai_compatible import LLMConfig, OpenAICompatibleClient
from course_project import web_app
from course_project.candidate_evaluation import evaluate_protocol, select_population
from course_project.jobs import JobManager
from course_project.optimization_control import OptimizationControl, OptimizationStopped
from course_project import test_platform_iteration as fixtures


def measured_source(name, slots=16, repeats=1, answer="mean"):
    result = "sum((low + i) * value for i, value in enumerate(counts)) / sum(counts)" if answer == "mean" else "high"
    return (f"class {name}:\n"
            "    def execute_channel(self, channel, low, high):\n"
            f"        for _ in range({repeats}):\n"
            f"            counts = channel.observe(0, {slots})['counts']\n"
            f"        return [{result}]\n")


class MeasuredChannelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {"task_profile": "mean_estimation", "total_runs": 2,
                       "num_tags": 20, "price_min": 1, "price_max": 8}

    def evaluate(self, source):
        path = self.root / "Candidate.py"
        path.write_text(source, encoding="utf-8")
        return evaluate_protocol(path, "Candidate", lambda n, low, high: ([Tag(i, low) for i in range(n)], {}), self.config)

    def test_worker_charges_repeated_observations_and_excludes_reported_time(self):
        once = self.evaluate(measured_source("Candidate", repeats=1))
        twice = self.evaluate(measured_source("Candidate", repeats=2))
        self.assertTrue(once["summary"]["communication_eligible"])
        self.assertEqual(once["validation"]["final_decision"], "passed")
        self.assertAlmostEqual(twice["summary"]["mean_communication_cost_ms"], 2 * once["summary"]["mean_communication_cost_ms"])
        row = twice["validation"]["runs"][0]
        self.assertEqual(row["communication_counters"], {"reader_bits": 320, "tag_bits": 40, "slots": 256, "frames": 2, "rounds": 2})
        self.assertEqual(len(row["communication_events"]), 2)

    def test_self_report_cannot_spoof_measured_origin_or_cheap_cost(self):
        item = self.evaluate("class Candidate:\n    def execute(self, tags, low, high):\n"
            "        return [low], 0, 0, {'frame_size':16,'bits_per_slot':1,'cost_origin':'instrumented_channel','communication_cost_ms':0,'reader_bits':1,'tag_bits':1,'slots':1}\n")
        self.assertTrue(item["summary"]["selection_eligible"])
        self.assertFalse(item["summary"]["communication_eligible"])
        self.assertEqual(item["validation"]["runs"][0]["cost_origin"], "candidate_self_reported")

    def test_no_event_is_not_a_zero_cost_solution(self):
        item = self.evaluate("class Candidate:\n    def execute_channel(self, channel, low, high):\n        return [low]\n")
        self.assertFalse(item["summary"]["communication_eligible"])
        self.assertFalse(item["summary"]["selection_eligible"])

    def test_private_channel_state_and_reflection_rejected(self):
        for expr in ("channel._ids", "getattr(channel, '_values')", "'{0.__func__.__closure__}'.format(channel.observe)"):
            with self.subTest(expr=expr):
                item = self.evaluate("class Candidate:\n    def execute_channel(self, channel, low, high):\n"
                    f"        leaked = {expr}\n        return [low]\n")
                self.assertFalse(item["summary"]["selection_eligible"])

    def test_measured_reader_cannot_patch_numpy_to_capture_tag_ids(self):
        source = "import numpy as np\n" + measured_source("Candidate")
        item = self.evaluate(source)
        self.assertFalse(item["summary"]["selection_eligible"])
        self.assertIn("import_not_allowed", item["validation"]["runtime_error"])

    def test_precision_gate_beats_cheap_incorrect_candidate(self):
        good = self.evaluate(measured_source("Candidate", slots=32))
        bad = self.evaluate(measured_source("Candidate", slots=2, answer="wrong"))
        self.assertTrue(bad["summary"]["communication_eligible"])
        self.assertLess(bad["summary"]["mean_communication_cost_ms"], good["summary"]["mean_communication_cost_ms"])
        items = {"bad": bad, "good": good}
        self.assertEqual(select_population(list(items), items, 1, objective="precision_constrained_cost"), ["good"])
        cheap = self.evaluate(measured_source("Candidate", slots=8))
        self.assertEqual(select_population(["good", "cheap"], {"good": good, "cheap": cheap}, 1, objective="precision_constrained_cost"), ["cheap"])


class OptimizationLoopTests(unittest.TestCase):
    setUp = fixtures.PlatformIterationTests.setUp
    payload = fixtures.PlatformIterationTests.payload
    generate = fixtures.PlatformIterationTests.generate

    def test_no_improvement_stops(self):
        def generate(**kwargs):
            path = self.folder / (kwargs["protocol_name"] + ".py")
            path.write_text(measured_source(kwargs["protocol_name"], answer="wrong"), encoding="utf-8")
            return {"status": "failed", "target_file": str(path)}
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate) as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload(task_profile="mean_estimation", iterations=4,
                no_improvement_rounds=1, independent_validation=False))
        self.assertEqual(workflow.call_count, 1)
        self.assertEqual(result["stop_reason"], "no_improvement")


    def test_overlap_is_not_claimed_as_independent(self):
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=self.generate):
            result = web_app.run_selfopt_auto_iterate(self.payload(iterations=1))
        self.assertEqual(result["independent_validation"]["status"], "independence_not_established")
        evidence = json.loads((self.root / result["independent_validation"]["evidence_path"]).read_text(encoding="utf-8"))
        self.assertTrue(evidence["overlapping_dataset_hashes"])
        self.assertFalse(evidence["used_for_selection"])


    def test_frozen_sources_and_independent_data(self):
        (self.folder.parent / "Task.py").write_text(
            "from base import Tag\nimport random\ndef generate_tags(n, low, high):\n"
            "    return [Tag(random.randrange(2**48), random.randint(low,high)) for _ in range(n)], {}\n", encoding="utf-8")
        for name in ("PoorA", "PoorB"):
            (self.folder / (name + ".py")).write_text(measured_source(name, slots=128), encoding="utf-8")
        def generate(**kwargs):
            path = self.folder / (kwargs["protocol_name"] + ".py")
            path.write_text(measured_source(kwargs["protocol_name"], slots=32), encoding="utf-8")
            # Offline model fixtures still exercise the real HTTP accounting guard.
            kwargs["request_guard"]()
            return {"status": "success", "target_file": str(path)}
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate) as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload(iterations=1, task_profile="mean_estimation",
                selection_objective="precision_constrained_cost", evaluation_runs=3))
        held = result["independent_validation"]
        self.assertTrue(held["independent"])
        self.assertTrue(held["winner_passed"])
        self.assertGreater(held["communication_reduction_fraction"], 0)
        self.assertNotIn("100100", workflow.call_args.kwargs["requirement_text"])
        path = self.root / held["evidence_path"]
        record = json.loads(path.read_text(encoding="utf-8"))
        for name, digest in record["artifact_hashes"].items():
            self.assertEqual(hashlib.sha256((path.parent / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(result["budget"]["model_requests"], 1)


    def test_budget_stops_without_losing_completed_rounds(self):
        def generate(**kwargs):
            kwargs["request_guard"]()
            return self.generate(**kwargs)
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate):
            result = web_app.run_selfopt_auto_iterate(self.payload(max_model_requests=1, independent_validation=False))
        self.assertEqual(len(result["iterations"]), 1)
        self.assertEqual(result["stop_reason"], "model_request_budget")
        self.assertEqual(result["budget"]["model_requests"], 1)
        self.assertIn("child_01", result["population"])

    def test_cancel_stops_with_evidence(self):
        cancelled = threading.Event()
        def logger(*args, **kwargs):
            pass
        def check():
            from course_project.jobs import JobCancelled
            if cancelled.is_set():
                raise JobCancelled()
        logger.check_cancelled = check
        def generate(**kwargs):
            item = self.generate(**kwargs)
            cancelled.set()
            return item
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate) as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload(iterations=4), progress_logger=logger)
        self.assertEqual(workflow.call_count, 1)
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertEqual(len(result["iterations"]), 1)
        self.assertFalse(result["independent_validation"]["independent"])
        record = json.loads((self.root / result["evidence_path"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "cancelled")

    def test_target_cost_stops_after_precision_is_met(self):
        def generate(**kwargs):
            path = self.folder / (kwargs["protocol_name"] + ".py")
            path.write_text(measured_source(kwargs["protocol_name"]), encoding="utf-8")
            return {"status": "success", "target_file": str(path)}
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate) as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload(iterations=4, task_profile="mean_estimation",
                selection_objective="precision_constrained_cost", target_communication_cost_ms=1000,
                independent_validation=False))
        self.assertEqual(workflow.call_count, 1)
        self.assertEqual(result["stop_reason"], "target_communication_cost")

    def test_time_budget_exhausted_before_first_round(self):
        control = OptimizationControl(30, 1)
        control.started -= 2
        with patch.object(web_app, "OptimizationControl", return_value=control), patch.object(web_app, "run_ai_optimization_workflow") as workflow:
            result = web_app.run_selfopt_auto_iterate(self.payload())
        workflow.assert_not_called()
        self.assertEqual(result["stop_reason"], "time_budget")
        self.assertEqual(result["iterations"], [])



class ControlAndHistoryTests(unittest.TestCase):
    def test_retry_uses_fresh_names_and_current_credentials(self):
        old = {"id": "old", "kind": "auto_iterate", "status": "interrupted",
               "payload": {"prefix": "previous", "scene": "fixture", "max_model_requests": 7}}
        with patch.object(web_app.JOB_MANAGER, "get", return_value=old), patch.object(web_app, "start_background_task", return_value={"id": "new"}) as start:
            web_app.retry_background_task({"id": "old", "api_key_value": "new-secret", "model": "new-model"})
        payload = start.call_args.args[1]
        self.assertTrue(payload["prefix"].startswith("previous_retry_"))
        self.assertEqual(payload["api_key_value"], "new-secret")
        self.assertEqual(payload["max_model_requests"], 7)
        self.assertEqual(payload["retry_of"], "old")
    def test_http_retries_and_endpoint_fallback_share_actual_request_budget(self):
        config = LLMConfig("https://fixture.invalid", "secret", "offline", endpoint_path="auto", retry_attempts=3, retry_delay_seconds=0)
        for failure in (error.URLError("offline"), error.HTTPError("https://fixture.invalid", 404, "missing", {}, None)):
            control = OptimizationControl(1, 60)
            client = OpenAICompatibleClient(config)
            client.request_guard = control.before_request
            with patch("backend_tools.openai_compatible.request.urlopen", side_effect=failure) as request:
                with self.assertRaises(OptimizationStopped):
                    client.generate_text(system_prompt="x", user_prompt="y")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(control.requests, 1)

    def test_time_budget_checked_before_network(self):
        control = OptimizationControl(10, 10)
        control.started -= 11
        with self.assertRaises(OptimizationStopped) as stopped:
            control.before_request()
        self.assertEqual(stopped.exception.reason, "time_budget")
        self.assertEqual(control.requests, 0)

    def test_cancel_queued_work_and_persist_without_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            manager = JobManager(max_workers=1, history_path=path)
            release, started = threading.Event(), threading.Event()
            def work(update):
                started.set()
                release.wait(5)
                update(log="should not run after cancellation")
            try:
                first = manager.submit("test", {"api_key_value": "test-secret", "nested": {"api_key": "other"}}, work)
                self.assertTrue(started.wait(2))
                second = manager.submit("test", {}, lambda update: self.fail("cancelled queued job executed"))
                manager.cancel(second["id"])
                manager.cancel(first["id"])
            finally:
                release.set()
                manager.shutdown()
            self.assertEqual(manager.get(first["id"])["status"], "cancelled")
            self.assertEqual(manager.get(second["id"])["status"], "cancelled")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("test-secret", text)
            self.assertNotIn('"api_key"', text)
            restored = JobManager(history_path=path)
            try:
                self.assertEqual(restored.get(first["id"])["status"], "cancelled")
                self.assertEqual(len(restored.list()), 2)
            finally:
                restored.shutdown()

    def test_restart_marks_active_tasks_interrupted_without_replaying(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            path.write_text(json.dumps([{"id": "old", "status": "running", "stage": "model",
                "progress": 40, "payload": {}, "logs": [], "result": None, "elapsed_s": 12}]), encoding="utf-8")
            manager = JobManager(history_path=path)
            try:
                row = manager.get("old")
                self.assertEqual(row["status"], "interrupted")
                self.assertAlmostEqual(row["elapsed_s"], 12, places=1)
            finally:
                manager.shutdown()
