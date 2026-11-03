"""Selection tradeoffs, feasibility, reproducibility and real loop integration."""
import json
import unittest
from unittest.mock import patch

from course_project.evolution_selection import select, structural_hash
from course_project import web_app
from course_project import test_platform_iteration as fixtures
from course_project.test_optimization_loop import measured_source


def evaluated(name, score, cost, passed=1, *, fingerprint=None, valid=True, measured=True, dataset="same"):
    return {"summary": {"quality_pass_rate": passed, "quality_score": score,
        "mean_communication_cost_ms": cost, "selection_eligible": valid,
        "communication_eligible": measured, "channel_model_id": "channel" if measured else None,
        "dataset_hashes": [dataset], "structural_sha256": fingerprint or name}}


class ParetoSelectionTests(unittest.TestCase):
    def test_fronts_keep_quality_cost_tradeoffs_and_remove_dominated_point(self):
        rows = {"accurate": evaluated("a", 1, 100), "cheap": evaluated("b", .9, 30),
            "dominated": evaluated("c", .8, 120), "middle": evaluated("m", .95, 60)}
        report = select(list(rows), rows, 2, objective="pareto_diversity")
        self.assertEqual(set(report["population"]), {"accurate", "cheap"})
        self.assertEqual(set(report["fronts"][0]), {"accurate", "cheap", "middle"})
        self.assertEqual(report["fronts"][1], ["dominated"])
        self.assertEqual(next(row["reason"] for row in report["decisions"] if row["name"] == "middle"), "crowding_capacity")
        json.dumps(report, allow_nan=False)

    def test_precision_and_measured_origin_are_hard_gates_for_front(self):
        rows = {"valid": evaluated("a", .98, 100), "wrong": evaluated("b", 1, 1, passed=.5),
            "reported": evaluated("c", 1, .001, measured=False), "broken": evaluated("d", 1, .001, valid=False)}
        report = select(list(rows), rows, 1, objective="pareto_diversity", incumbents=["valid"])
        self.assertEqual(report["population"], ["valid"])
        self.assertEqual(report["fronts"], [["valid"]])

    def test_class_rename_comment_and_docstring_do_not_fill_population(self):
        a = 'class A:\n    """first"""\n    def execute(self):\n        return [1]\n'
        b = '# comment\nclass B:\n    """second"""\n    def execute(self):\n        return [1]\n'
        self.assertEqual(structural_hash(a, "A"), structural_hash(b, "B"))
        self.assertNotEqual(structural_hash(a, "A"), structural_hash(a.replace('[1]', '[2]'), "A"))
        rows = {"incumbent": evaluated("a", .9, 30, fingerprint="same"), "copy": evaluated("b", .9, 30, fingerprint="same")}
        report = select(list(rows), rows, 2, objective="pareto_diversity", incumbents=["incumbent"])
        self.assertEqual(report["population"], ["incumbent"])
        self.assertEqual(report["decisions"][1]["duplicate_of"], "incumbent")

    def test_representative_is_fixed_by_cost_before_holdout_and_ties_are_stable(self):
        rows = {"accurate": evaluated("a", 1, 100), "cheap": evaluated("b", .9, 30), "tie": evaluated("c", .9, 30)}
        self.assertEqual(select(list(rows), rows, 1, objective="pareto_diversity")["population"], ["cheap"])
        first = select(list(rows), rows, 2, objective="pareto_diversity", incumbents=["accurate", "cheap"])
        self.assertEqual(first, select(list(rows), rows, 2, objective="pareto_diversity", incumbents=["accurate", "cheap"]))

    def test_incompatible_datasets_and_models_refuse_selection(self):
        rows = {"a": evaluated("a", 1, 100), "b": evaluated("b", 1, 50, dataset="other")}
        with self.assertRaises(ValueError):
            select(list(rows), rows, 2, objective="pareto_diversity")
        rows["b"]["summary"]["dataset_hashes"] = ["same"]
        rows["b"]["summary"]["channel_model_id"] = "another"
        with self.assertRaises(ValueError):
            select(list(rows), rows, 2, objective="pareto_diversity")


class ParetoLoopTests(unittest.TestCase):
    setUp = fixtures.PlatformIterationTests.setUp
    payload = fixtures.PlatformIterationTests.payload

    def test_real_loop_survives_one_unique_parent_and_records_every_decision(self):
        for name in ("PoorA", "PoorB"):
            (self.folder / (name + ".py")).write_text(measured_source(name, slots=256), encoding="utf-8")
        def generate(**kwargs):
            name = kwargs["protocol_name"]
            path = self.folder / (name + ".py")
            path.write_text(measured_source(name, slots=256), encoding="utf-8")
            return {"status": "success", "target_file": str(path)}
        with patch.object(web_app, "run_ai_optimization_workflow", side_effect=generate):
            result = web_app.run_selfopt_auto_iterate(self.payload(task_profile="mean_estimation", selection_objective="pareto_diversity", independent_validation=False))
        self.assertEqual(len(result["population"]), 1)
        self.assertEqual(result["iterations"][0]["parent_a"], result["iterations"][0]["parent_b"])
        self.assertTrue(all(row["selection"]["reason"] == "duplicate_structure" for row in result["iterations"]))
        self.assertTrue(result["winner_candidate_id"])
        self.assertTrue(result["candidate_ids"])
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
