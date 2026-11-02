"""Acceptance regressions for recorded repair, held-out data and automation."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_protocol_optimizer as optimizer
from backend_tools.openai_compatible import LLMConfig
from benchmark.evidence import comparison_rows
from benchmark.pipeline import audit_pipeline, run_pipeline, seed_groups
from benchmark.provenance import reproducibility_key
from benchmark.run_config import RunConfig, run_config
from benchmark.runner import evaluate_run
from benchmark.splits import audit_splits
from course_project import engineering_service as service
from course_project.test_engineering import SAFE_SOURCE, generator
from course_project.test_run_config import config_payload
from generic_protocol.journal import audit_journal
from generic_protocol.validation import semantic_result, valid_physical, validate_candidate
from stat_protocols.contracts import RunResult


def data_row(digest, split, seed=0):
    return {"seed": seed, "run_result": {"metadata": {"dataset_hash": digest, "split": split}}}


class ValidationClosureTests(unittest.TestCase):
    def test_bad_encoding_and_missing_file_return_failed_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.py"
            path.write_bytes(b"class Fixture:\n    pass\n#\xff")
            report = validate_candidate(path, "Fixture", generator)
            self.assertEqual(report["final_decision"], "failed")
            self.assertEqual(report["information_isolation"]["violations"][0]["rule"], "source_encoding_error")
            path.unlink()
            self.assertEqual(validate_candidate(path, "Fixture", generator)["final_decision"], "failed")

    def test_scalar_custom_output_cannot_be_disguised_by_legacy_adapter(self):
        source = SAFE_SOURCE.replace("[self._simulate_tag_response(tags[0])]", "self._simulate_tag_response(tags[0])")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scalar.py"
            path.write_bytes(source.encode())
            report = validate_candidate(path, "Fixture", generator, task_profile="custom_task", total_runs=1)
        self.assertEqual(report["final_decision"], "failed")
        self.assertFalse(report["runs"][0]["output_contract_passed"])

    def test_fractional_physical_parameters_and_malformed_sets_are_rejected(self):
        self.assertFalse(valid_physical({"frame_size": 1.5, "bits_per_slot": 1}))
        self.assertFalse(valid_physical({"frame_size": 1, "bits_per_slot": True}))
        for value in (True, float("nan"), {"price": 1}, [1]):
            with self.subTest(value=value):
                self.assertFalse(semantic_result([value], generator(1, 1, 2)[0], {}, "mode_query")[0])

    def test_generator_failure_keeps_a_report_without_leaking_exception_text(self):
        def failed_generator(*args):
            raise RuntimeError("sensitive fixture text")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.py"
            path.write_bytes(SAFE_SOURCE.encode())
            report = validate_candidate(path, "Fixture", failed_generator)
        self.assertEqual(report["final_decision"], "failed")
        self.assertIn("dataset_generation_failed:RuntimeError", report["runtime_error"])
        self.assertNotIn("sensitive fixture text", json.dumps(report))

    def test_one_success_among_failures_has_no_confidence_interval(self):
        reports = [evaluate_run(RunResult("fixture", "mean", 1, 1, 1, 1, 1, 1, 1, seed,
            failure_reason="fixture failure" if seed else "", metadata={"dataset_hash": f"data-{seed}"})) for seed in range(3)]
        row = comparison_rows(reports)[0]
        self.assertEqual(row["ci_sample_size"], 1)
        self.assertIsNone(row["error_ci95_low"])
        self.assertFalse(row["pareto_eligible"])


class SplitClosureTests(unittest.TestCase):
    def test_data_identity_catches_overlap_even_when_seeds_differ(self):
        result = audit_splits([data_row("same", "blind", 900)], [data_row("same", "development", 1)])
        self.assertFalse(result["passed"])
        self.assertEqual(result["overlap_count"], 1)
        self.assertEqual(audit_splits([data_row("unique", "blind")])["status"], "not_available")
        self.assertTrue(audit_splits([data_row("unique", "blind")], [data_row("other", "development")])["passed"])
        self.assertFalse(audit_splits([data_row("x", "blind")], [data_row(None, "development")])["passed"])

    def test_validation_and_blind_are_separated_from_each_other(self):
        result = audit_splits([data_row("dev", "development"), data_row("held", "validation"), data_row("held", "blind")])
        self.assertFalse(result["passed"])
        self.assertEqual(result["overlaps"][0]["split"], "blind")

    def test_reference_locations_do_not_change_experiment_identity(self):
        first = {"config": {"seeds": [0]}, "reference_artifact_hashes": {"C:/old/results.json": "abc"}}
        second = {**first, "reference_artifact_hashes": {"E:/new/results.json": "abc"}}
        self.assertEqual(reproducibility_key(first), reproducibility_key(second))

    def test_config_runner_refuses_overwrite_and_actual_blind_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({**config_payload(), "split": "development"}), encoding="utf-8")
            run_config(config, root / "development")
            with self.assertRaises(ValueError):
                run_config(config, root / "development")
            config.write_text(json.dumps({**config_payload(), "split": "blind"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_config(config, root / "blind", reference_paths=[root / "development/results.json"])
            manifest = json.loads((root / "blind/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertGreater(manifest["split_audit"]["overlap_count"], 0)


class RepairWorkflowClosureTests(unittest.TestCase):
    def test_failed_repair_then_next_generation_keeps_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scene = root / "fixture"
            scene.mkdir()
            (scene / "Task.py").write_text("# Offline engineering fixture", encoding="utf-8")
            (scene / "Parent.py").write_bytes(SAFE_SOURCE.encode())
            kwargs = dict(scene_name="fixture", parent_a="Parent", parent_b="Parent", protocol_name="Repaired",
                target_generation="generation_1", temperature=.1, max_tokens=1000, timeout_seconds=10,
                validate_num_tags=8, max_repair_rounds=1, dry_run=False, logger=lambda message: None,
                llm_config=LLMConfig(base_url="http://offline.invalid", api_key="offline-fixture", model="offline-fixture"))
            with patch.object(optimizer, "PROJECT_ROOT", root), patch.object(optimizer, "_build_optimizer_prompt", return_value="offline fixture prompt"), \
                 patch.object(optimizer, "_load_scene_task", return_value=(generator, {"PRICE_MIN": 1, "PRICE_MAX": 2})), \
                 patch.object(optimizer.OpenAICompatibleClient, "generate_text", side_effect=["import os\n" + SAFE_SOURCE, SAFE_SOURCE, SAFE_SOURCE]) as llm:
                repaired = optimizer.run_ai_optimization_workflow(**kwargs)
                child = optimizer.run_ai_optimization_workflow(**{**kwargs, "protocol_name": "Child", "parent_a": "Repaired", "target_generation": "generation_2"})
            self.assertEqual(llm.call_count, 3)
            self.assertEqual(repaired["status"], "success")
            self.assertEqual(repaired["attempts"], 2)
            self.assertEqual(child["status"], "success")
            journal = Path(repaired["workflow_path"])
            self.assertTrue(audit_journal(journal)["passed"])
            early = journal.parent / "attempt_001" / "protocol.py"
            self.assertIn("import os", early.read_text(encoding="utf-8"))
            candidate = json.loads((journal.parent / "attempt_002/candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(candidate["benchmark_result"]["scope"], "validation_diagnostics")
            self.assertFalse(candidate["benchmark_result"]["pareto_eligible"])
            with patch.object(service, "ROOT", root), patch.object(service, "EXPERIMENT_ROOT", root / "Experiments"):
                lineage = service.candidate_lineage()
                self.assertEqual(len(lineage["nodes"]), 3)
                edge = next(e for e in lineage["edges"] if e["parent_reference"] == "Repaired")
                self.assertEqual(edge["parent_ids"], [candidate["candidate_id"]])
                self.assertTrue(all(n["source_hash_match"] for n in lineage["nodes"]))
                self.assertEqual(next(n for n in lineage["nodes"] if n["candidate_id"] == candidate["candidate_id"])["workflow"]["repair_count"], 1)
            early.write_text("tampered", encoding="utf-8")
            self.assertFalse(audit_journal(journal)["passed"])


class PipelineClosureTests(unittest.TestCase):
    def test_pipeline_rejects_validation_data_used_by_an_earlier_experiment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            earlier = root / "earlier.json"
            earlier.write_text(json.dumps({**config_payload(), "seeds": [1000], "split": "development"}), encoding="utf-8")
            run_config(earlier, root / "earlier")
            config = root / "config.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_pipeline(config, root / "pipeline", validation_seeds=[1000], blind_seeds=[2000],
                    reference_paths=[root / "earlier/results.json"], progress=lambda message: None)
            manifest = json.loads((root / "pipeline/pipeline_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            heldout = json.loads((root / "pipeline/validation/raw/manifest.json").read_text(encoding="utf-8"))
            self.assertGreater(heldout["split_audit"]["overlap_count"], 0)

    def test_pipeline_failure_is_recorded_and_cannot_pass_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            output = root / "failed"
            with patch("benchmark.pipeline.run_config", side_effect=RuntimeError("fixture failure")):
                with self.assertRaises(RuntimeError):
                    run_pipeline(config, output, progress=lambda message: None)
            manifest = json.loads((output / "pipeline_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["stages"][0]["status"], "failed")
            self.assertFalse(audit_pipeline(output)["passed"])

    def test_seed_groups_reject_overlap_before_running(self):
        with self.assertRaises(ValueError):
            seed_groups(RunConfig(**config_payload()), [0], [100])

    def test_all_stages_blind_replay_and_nested_manifest_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({**config_payload(), "profiles": ["mean_estimation", "variance_estimation", "cardinality_estimation", "top_k"], "seeds": [0, 1]}), encoding="utf-8")
            output = root / "pipeline"
            result = run_pipeline(config, output, progress=lambda message: None)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["stages"]), 6)
            self.assertTrue(result["replay"]["passed"])
            self.assertTrue(audit_pipeline(output)["passed"])
            self.assertTrue(all(s["split_audit"]["passed"] for s in result["stages"]))
            nested = output / "blind/raw/manifest.json"
            nested.write_text("{}", encoding="utf-8")
            self.assertIn("blind/raw/manifest.json", audit_pipeline(output)["mismatched"])
            with self.assertRaises(ValueError):
                run_pipeline(config, output, progress=lambda message: None)


if __name__ == "__main__":
    unittest.main()
