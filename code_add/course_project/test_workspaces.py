"""Offline integration of revisioned workspaces with real sandbox jobs."""
import json
import threading
import time
import unittest
from unittest.mock import patch
import ai_protocol_optimizer
import prompt_generation

from course_project import workspace_service as service, research_service as research
from course_project import web_app, statistics_service
from course_project import test_platform_iteration as fixtures
from course_project import test_infrastructure as infrastructure
from course_project.http_support import APIError
from course_project.jobs import JobManager, TERMINAL_STATES
from course_project.test_optimization_loop import measured_source


def setup_workspace(case):
    fixtures.PlatformIterationTests.setUp(case)
    for target, key, value in ((service, "ROOT", case.root),
            (ai_protocol_optimizer, "PROJECT_ROOT", case.root),
            (prompt_generation, "PROJECT_ROOT", case.root),
            (web_app, "COURSE_ROOT", case.root / "course_project"),
            (statistics_service, "ROOT", case.root),
            (statistics_service, "EXPERIMENT_ROOT", case.root / "Experiments/stat_protocols")):
        context = patch.object(target, key, value)
        context.start()
        case.addCleanup(context.stop)
    for name in ("PoorA", "PoorB"):
        (case.folder / (name + ".py")).write_text(measured_source(name, slots=256, repeats=2), encoding="utf-8")
    case.manager = JobManager(max_workers=1, history_limit=2)
    case.addCleanup(case.manager.shutdown)


def configuration(**updates):
    return {"title": "Offline workspace", "scene": "fixture", "task_profile": "mean_estimation",
        "optimization": {"parents": ["PoorA", "PoorB"], "baseline_protocol": "PoorB",
            "iterations": 1, "evaluation_runs": 1, "validate_num_tags": 20,
            "evaluation_price_max": 8, "independent_validation": False},
        "evaluation": {"sizes": [20], "distributions": ["uniform"], "validation_seeds": [71001],
            "blind_seeds": [81001], "perturbations": [{"missing_rate": 0, "noise_rate": 0, "extreme_rate": 0}]},
        "model_config": {"base_url": "https://offline.invalid", "model": "offline"}, **updates}


def wait_for(manager, response):
    job_id = response["job"]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        snapshot = manager.get(job_id)
        if snapshot["status"] in TERMINAL_STATES:
            return snapshot
        time.sleep(.02)
    raise AssertionError("offline workspace job timed out")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        setup_workspace(self)

    def run_action(self, task, action, **extra):
        response = service.start(self.manager, {"id": task["id"], "revision": task["revision"], "action": action, **extra})
        result = wait_for(self.manager, response)
        self.assertEqual(result["status"], "success", result.get("error_info"))
        return result

    def test_revision_history_conflict_and_input_freeze(self):
        first = service.save(configuration())
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["baseline_candidate_id"], first["initial_candidate_ids"][1])
        self.assertEqual(service.listing()["workspaces"][0]["id"], first["id"])
        second = service.save(configuration(revision=1, title="Revised"), workspace_id=first["id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual([row["revision"] for row in second["revisions"]], [2, 1])
        with self.assertRaises(APIError) as conflict:
            service.save(configuration(revision=1), workspace_id=first["id"])
        self.assertEqual(conflict.exception.status, 409)
        service.verify_inputs(second)
        source = self.folder / "PoorA.py"
        source.write_text(source.read_text(encoding="utf-8") + "\n# edited", encoding="utf-8")
        with self.assertRaises(APIError) as changed:
            self.run_action(second, "optimization", preview=True)
        self.assertEqual(changed.exception.code, "workspace_source_changed")
        third = service.save(configuration(revision=2), workspace_id=first["id"])
        service.verify_inputs(third)
        self.assertEqual(len(third["candidates"]), 3)

    def test_changed_generator_and_tampered_configuration_are_rejected(self):
        task = service.save(configuration())
        source = self.folder.parent / "Task.py"
        original = source.read_bytes()
        source.write_bytes(original + b"\n# changed")
        with self.assertRaises(APIError):
            service.verify_inputs(task)
        source.write_bytes(original)
        (self.root / task["configuration_path"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(APIError):
            service.verify_inputs(task)

    def test_secrets_and_invalid_fields_never_enter_saved_configuration(self):
        for model in ({"api_key_value": "never-store-this"}, {"base_url": "https://user:secret@example.test"},
                {"base_url": "https://example.test?api_key=secret"}):
            with self.subTest(model=model), self.assertRaises(ValueError):
                service.save(configuration(model_config=model))
        bad = configuration()
        bad["evaluation"]["blind_seeds"] = bad["evaluation"]["validation_seeds"]
        with self.assertRaises(ValueError):
            service.save(bad)
        task = service.save(configuration())
        with self.assertRaises(ValueError):
            service.save(configuration(revision=1, task_profile="mode_query"), workspace_id=task["id"])
        with self.assertRaises(APIError) as changed:
            service.start(self.manager, {"id": task["id"], "revision": 1, "action": "optimization",
                "model_config": {"model": "different", "api_key_value": "never-store-this"}})
        self.assertEqual(changed.exception.code, "workspace_model_changed")
        self.assertNotIn("never-store-this", json.dumps(service.detail(task["id"])))

    def test_preview_validation_and_matrix_have_persistent_links(self):
        task = service.save(configuration())
        preview = self.run_action(task, "optimization", preview=True)
        self.assertTrue(preview["result"]["dry_run"])
        validation = self.run_action(task, "validation")
        self.assertEqual(validation["result"]["candidate_id"], task["baseline_candidate_id"])
        evaluation = self.run_action(task, "evaluation")
        evidence = research.runs({"id": [evaluation["result"]["run_id"]]})
        self.assertTrue(evidence["audit"]["passed"])
        self.assertEqual(evidence["manifest"]["config"]["candidate_ids"], [task["baseline_candidate_id"]])
        for _ in range(3):
            wait_for(self.manager, {"job": self.manager.submit("prune", {}, lambda update: {})})
        with self.assertRaises(APIError):
            self.manager.get(preview["id"])
        durable = service.detail(task["id"])
        self.assertEqual(len(durable["runs"]), 3)
        self.assertTrue(all(row["status"] == "success" for row in durable["runs"]))
        self.assertTrue(any(row["job"]["id"] == preview["id"] for row in durable["runs"]))

    def test_offline_pareto_winner_flows_into_validation_and_evaluation(self):
        task = service.save(configuration())
        def generate(**kwargs):
            name = kwargs["protocol_name"]
            path = self.folder / (name + ".py")
            path.write_text(measured_source(name, slots=256, repeats=1), encoding="utf-8")
            return {"status": "success", "target_file": str(path)}
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate):
            optimized = self.run_action(task, "optimization", model_config={"api_key_value": "offline-test-secret"})
        result = optimized["result"]
        self.assertEqual(result["final_selection"]["objective"], "pareto_diversity")
        winner = result["winner_candidate_id"]
        self.assertNotEqual(winner, task["baseline_candidate_id"])
        self.assertIn("winner", next(row for row in service.detail(task["id"])["candidates"] if row["id"] == winner)["roles"])
        self.assertEqual(self.run_action(task, "validation")["result"]["candidate_id"], winner)
        evaluated = self.run_action(task, "evaluation")
        config = research.runs({"id": [evaluated["result"]["run_id"]]})["manifest"]["config"]
        self.assertEqual(set(config["candidate_ids"]), {winner, task["baseline_candidate_id"]})
        self.assertEqual(config["baseline_id"], task["baseline_candidate_id"])
        self.assertNotIn("offline-test-secret", json.dumps(service.detail(task["id"])))
        # A new configuration revision must not silently reuse an old winner.
        revised = service.save(configuration(revision=1), workspace_id=task["id"])
        self.assertEqual(self.run_action(revised, "validation")["result"]["candidate_id"], task["baseline_candidate_id"])

    def test_busy_guard_and_queued_cancellation_are_durable(self):
        task = service.save(configuration())
        release = threading.Event()
        self.addCleanup(release.set)
        blocker = self.manager.submit("blocker", {}, lambda update: release.wait(10))
        queued = service.start(self.manager, {"id": task["id"], "revision": 1, "action": "optimization", "preview": True})
        with self.assertRaises(APIError) as busy:
            service.start(self.manager, {"id": task["id"], "revision": 1, "action": "validation"})
        self.assertEqual(busy.exception.code, "workspace_busy")
        with self.assertRaises(APIError):
            service.save(configuration(revision=1), workspace_id=task["id"])
        self.manager.cancel(queued["job"]["id"])
        self.assertEqual(service.detail(task["id"])["runs"][0]["status"], "cancelled")
        release.set()
        wait_for(self.manager, {"job": blocker})

    def test_queued_source_is_rechecked_when_execution_starts(self):
        task = service.save(configuration())
        release = threading.Event()
        self.addCleanup(release.set)
        self.manager.submit("blocker", {}, lambda update: release.wait(10))
        queued = service.start(self.manager, {"id": task["id"], "revision": 1, "action": "optimization", "preview": True})
        (self.folder.parent / "Task.py").write_text("# edited while queued", encoding="utf-8")
        with self.assertLogs("course_project.jobs", level="ERROR"):
            release.set()
            self.assertEqual(wait_for(self.manager, queued)["status"], "failed")
        self.assertEqual(service.detail(task["id"])["runs"][0]["status"], "failed")

    def test_archive_and_restart_reconcile_without_reissuing_jobs(self):
        task = service.save(configuration(status="archived"))
        with self.assertRaises(APIError):
            self.run_action(task, "validation")
        with service.database() as db:
            db.execute("INSERT INTO workspace_runs VALUES(?,?,?,?,?,?,?,?,?)",
                ("a" * 32, task["id"], 1, "validation", service.now(), "missing-job", "running", "{}", "{}"))
        service.recover(self.manager)
        self.assertEqual(service.detail(task["id"])["runs"][0]["status"], "interrupted")
        self.assertEqual(self.manager.list(), [])

    def test_json_attachment_uses_native_dispatch_and_rejects_mixed_family(self):
        task = service.save(configuration())
        saved = statistics_service.save_candidate({"candidate_id": "offline", "model": "offline",
            "prompt_hash": "offline-prompt", "response_hash": "offline-response", "genotype": {
            "operator": "anonymous_histogram", "rounds": 2, "slots_per_value": 32, "threshold": None}})
        candidate = research.register({"family": "statistics", "path": saved["path"]})
        service.attach({"id": task["id"], "revision": 1, "candidate_ids": [candidate["id"]]})
        with patch("course_project.engineering_service.start_pipeline", return_value={"id": "native-job"}) as dispatch:
            response = service.start(self.manager, {"id": task["id"], "revision": 1, "action": "evaluation", "candidate_ids": [candidate["id"]]})
        self.assertEqual(response["job"]["id"], "native-job")
        self.assertIsInstance(dispatch.call_args.args[0], service.TrackingManager)
        # Reset the mocked run that deliberately did not submit an actual job.
        service.recover(self.manager)
        with self.assertRaises(APIError) as mixed:
            service.start(self.manager, {"id": task["id"], "revision": 1, "action": "evaluation",
                "candidate_ids": [candidate["id"], task["baseline_candidate_id"]]})
        self.assertEqual(mixed.exception.code, "incompatible_candidate_families")

    def test_observers_receive_sanitized_copies_and_errors_do_not_fail_work(self):
        received = []
        def observe(job):
            received.append(job)
            job["payload"]["mutated"] = True
        response = {"job": self.manager.submit("observer", {"api_key_value": "secret-fixture"},
            lambda update: {"echo": "secret-fixture"}, on_update=observe)}
        result = wait_for(self.manager, response)
        self.assertEqual(result["result"]["echo"], "[redacted]")
        self.assertNotIn("mutated", result["payload"])
        self.assertGreaterEqual(len(received), 3)
        def broken(job):
            raise RuntimeError("offline-observer")
        with self.assertLogs("course_project.jobs", level="ERROR"):
            other = wait_for(self.manager, {"job": self.manager.submit("observer-error", {}, lambda update: {}, on_update=broken)})
        self.assertEqual(other["status"], "success")


class WorkspaceHTTPTests(infrastructure.HTTPTestServer):
    def setUp(self):
        setup_workspace(self)
        context = patch.object(web_app, "JOB_MANAGER", self.manager)
        context.start()
        self.addCleanup(context.stop)

    def test_create_preview_export_and_revision_errors(self):
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("/api/workspaces", json.dumps(configuration()).encode(), headers)
        self.assertEqual(status, 201, body)
        task = json.loads(body)["workspace"]
        status, _, body = self.request("/api/workspaces/run", json.dumps({"id": task["id"], "revision": 1,
            "action": "optimization", "preview": True}).encode(), headers)
        self.assertEqual(status, 202, body)
        self.assertEqual(wait_for(self.manager, json.loads(body))["status"], "success")
        status, response_headers, body = self.request("/api/workspaces/export?id=" + task["id"])
        self.assertEqual(status, 200)
        self.assertIn("attachment", response_headers["Content-Disposition"])
        exported = json.loads(body)
        self.assertEqual(len(exported["runs"]), 1)
        self.assertEqual(len(exported["revisions"]), 1)
        self.assertEqual(self.request("/api/workspaces?id=" + "a" * 32)[0], 404)
        self.assertEqual(self.request("/api/workspaces/update", b"{}", headers)[0], 400)
        status, _, _ = self.request("/api/workspaces/run", json.dumps({"id": task["id"], "revision": 0,
            "action": "validation"}).encode(), headers)
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
