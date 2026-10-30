from __future__ import annotations

import unittest
from dataclasses import replace

from ai_protocol_optimizer import _profile_metrics
from benchmark.experiments import pareto_front
from benchmark.runner import evaluate_run, summarize_reports
from stat_protocols.contracts import RunResult


def result(name, task, truth, estimate, bits, *, seed=0, **metadata):
    return RunResult(
        protocol=name,
        task=task,
        truth=truth,
        estimate=estimate,
        reader_bits=bits,
        tag_bits=bits,
        slots=1,
        frames=1,
        rounds=1,
        seed=seed,
        metadata=metadata,
    )


class GenericBenchmarkRegressionTests(unittest.TestCase):
    def test_summary_separates_execution_and_quality_pass_rates(self):
        ok = evaluate_run(result("mixed", "mean", 1.0, 1.0, 10))
        failed = evaluate_run(replace(result("mixed", "mean", 1.0, 1.0, 10), failure_reason="boom"))
        row = summarize_reports([ok, failed])[0]
        self.assertEqual(row["runs"], 2)
        self.assertEqual(row["execution_success_rate"], 0.5)
        self.assertEqual(row["quality_pass_rate_successes"], 1.0)
        self.assertEqual(row["overall_pass_rate"], 0.5)

    def test_legacy_mode_hit_requires_complete_set(self):
        hit, precision, recall = _profile_metrics([699], [299, 699], "mode_query")
        self.assertFalse(hit)
        self.assertEqual(precision, 1.0)
        self.assertEqual(recall, 0.5)

    def test_legacy_set_task_uses_set_metrics(self):
        report = evaluate_run(result("legacy", "legacy_mode_query", [299, 699], [699], 10, metric_kind="set"))
        self.assertLess(report.f1, 1.0)
        self.assertEqual(report.recall, 0.5)

    def test_pareto_does_not_mix_scenarios(self):
        scenario_a_strong = result("a_strong", "mean", 1.0, 1.0, 100, distribution="single_peak", num_tags=100, missing_rate=0.0, noise_rate=0.0, extreme_rate=0.0, split="evaluation")
        scenario_b_fast = result("b_fast", "mean", 1.0, 1.0, 1, distribution="long_tail", num_tags=10000, missing_rate=0.0, noise_rate=0.0, extreme_rate=0.0, split="evaluation")
        front = pareto_front([evaluate_run(scenario_a_strong), evaluate_run(scenario_b_fast)])
        self.assertEqual({row["protocol"] for row in front}, {"a_strong", "b_fast"})
        self.assertEqual({row["distribution"] for row in front}, {"single_peak", "long_tail"})

    def test_missing_cost_is_not_pareto_candidate(self):
        missing = result("legacy", "legacy_mode_query", [1], [1], 0, cost_status="missing", metric_kind="set")
        self.assertEqual(pareto_front([evaluate_run(missing)]), [])


if __name__ == "__main__":
    unittest.main()
