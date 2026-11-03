import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from course_project import sensitivity, web_app
from course_project.test_optimization_loop import measured_source


class ExperimentMigrationTests(unittest.TestCase):
    def test_sensitivity_compares_same_seeded_data_and_retains_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            config = {"num_tags": 20, "price_min": 1, "price_max": 8, "total_runs": 2,
                      "task_profile": "mean_estimation", "seed": 9}
            results = []
            for name in ("CandidateA", "CandidateB"):
                path = folder / (name + ".py")
                path.write_text(measured_source(name), encoding="utf-8")
                source = sensitivity.ProtocolSource(path, name, "fixture")
                results.append(sensitivity.run_sensitivity_test_for_protocol(source, name, "dominance", [.2], config)[0])
            a, b = results
            self.assertEqual(a["evidence"]["summary"]["dataset_hashes"], b["evidence"]["summary"]["dataset_hashes"])
            self.assertEqual(a["timing_scope"], "worker_measured_wall_excluding_startup_not_RF")
            self.assertIsNotNone(a["communication_cost_ms"])
            self.assertLessEqual(a["pass_rate_ci_low"], a["semantic_pass_rate"])
            self.assertGreaterEqual(a["pass_rate_ci_high"], a["semantic_pass_rate"])
            bad = folder / "Bad.py"
            bad.write_text("raise RuntimeError('candidate import failed')", encoding="utf-8")
            row = sensitivity.run_sensitivity_test_for_protocol(sensitivity.ProtocolSource(bad, "Bad", "fixture"), "Bad", "dominance", [.2], config)[0]
            self.assertEqual(row["completed_runs"], 0)
            self.assertEqual(row["final_decision"], "failed")
            self.assertIsNone(row["avg_time_s"])

    def test_evolution_uses_exact_generation_file_and_verified_lineage_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for number in (2, 10):
                directory = root / "fixture" / ("generation_" + str(number))
                directory.mkdir(parents=True)
                path = directory / "SameName.py"
                path.write_text(measured_source("SameName", slots=number), encoding="utf-8")
                if number == 10:
                    path.with_suffix(".candidate.json").write_text(json.dumps({"source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "parents": ["ActualA", "ActualB"], "validation_result": {"attempt": 3}}), encoding="utf-8")
            metrics = {"accuracy": .75, "quality_score": .8, "time": 12., "validator_passed": True,
                "information_isolation_passed": True, "timing_scope": "worker_measured_wall_excluding_startup_not_RF"}
            def evaluate(scene, protocol, **kwargs):
                return {**metrics, "source_sha256": hashlib.sha256(kwargs["protocol_file"].read_bytes()).hexdigest()}
            with patch.object(web_app, "PROJECT_ROOT", root), patch.object(web_app, "discover_scenes", return_value=["fixture"]), patch.object(web_app, "_protocol_metrics_for_scene", side_effect=evaluate) as measurement:
                path, _ = web_app._scene_evolution_data_path("fixture", {"output_dir": "Experiments"})
            data = json.loads(path.read_text(encoding="utf-8"))["fixture"]
            self.assertEqual([row["generation"] for row in data["generations"]], [2, 10])
            old, new = [row["individuals"][0] for row in data["generations"]]
            self.assertIsNone(old["parent_A"])
            self.assertIsNone(old["correction_count"])
            self.assertEqual(new["parent_A"], "ActualA")
            self.assertEqual(new["correction_count"], 2)
            self.assertEqual(new["fitness"], .8)
            self.assertNotEqual(measurement.call_args_list[0].kwargs["protocol_file"], measurement.call_args_list[1].kwargs["protocol_file"])
