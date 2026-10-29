from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import replace

from benchmark.experiments import ablation_experiment, pareto_front
from benchmark.runner import run_benchmark, summarize_reports
from benchmark.scenarios import generate_tags
from stat_protocols.contracts import CostModel, TaskSpec
from stat_protocols.protocols import PollingProtocol, SharedSketchProtocol, SerialTaskProtocol, LLMRandomProtocol, protocol_registry
from stat_protocols.channel import HistogramChannel
from stat_protocols.genotype import compile_candidate
from benchmark.runner import evaluate_run
from stat_protocols.contracts import RunResult
from course_project.web_app import _statistical_benchmark_state


class StatisticalProtocolContractTests(unittest.TestCase):
    def test_same_seed_generates_same_distribution(self) -> None:
        spec = TaskSpec(seed=17, num_tags=100, distribution="multi_peak")
        first = [(tag.id, tag.price) for tag in generate_tags(spec)]
        second = [(tag.id, tag.price) for tag in generate_tags(spec)]
        self.assertEqual(first, second)

    def test_hash_is_stable_across_processes(self) -> None:
        code = "from base import simple_hash; print(simple_hash('tag', 17))"
        first = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
        second = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
        self.assertEqual(first, second)

    def test_three_plus_tasks_share_one_run_result_contract(self) -> None:
        spec = TaskSpec(seed=2, num_tags=120, tasks=("mean", "variance", "cardinality", "top_k"))
        tags = generate_tags(spec)
        protocol = SharedSketchProtocol()
        results = [protocol.run(tags, task, spec) for task in spec.tasks]
        self.assertEqual({result.task for result in results}, set(spec.tasks))
        self.assertTrue(all(result.seed == 2 and result.communication_bits > 0 for result in results))
        self.assertTrue(all(result.failure_reason == "" for result in results))

    def test_cost_model_uses_communication_counters(self) -> None:
        result = PollingProtocol().run(generate_tags(TaskSpec(seed=1, num_tags=10)), "mean", TaskSpec(seed=1, num_tags=10))
        cost = CostModel().estimate_ms(reader_bits=result.reader_bits, tag_bits=result.tag_bits, slots=result.slots, frames=result.frames, rounds=result.rounds)
        self.assertGreater(cost, 0)
        self.assertEqual(result.communication_bits, result.reader_bits + result.tag_bits)

    def test_benchmark_keeps_negative_rows_and_pareto_front(self) -> None:
        spec = TaskSpec(seed=0, num_tags=50, tasks=("mean", "variance", "cardinality", "top_k"))
        reports = run_benchmark(spec, seeds=[0, 1], protocol_names=("shared_sketch", "llm_random"))
        self.assertEqual(len(reports), 16)
        self.assertTrue(any(report.protocol == "llm_random" for report in reports))
        self.assertTrue(summarize_reports(reports))
        self.assertTrue(pareto_front(reports))

    def test_ablation_has_shared_and_single_task_rows(self) -> None:
        reports = ablation_experiment(TaskSpec(seed=3, num_tags=20), [3])
        names = {report.protocol for report in reports}
        self.assertIn("shared_sketch", names)
        self.assertIn("single_mean", names)

    def test_benchmark_rows_keep_fair_comparison_dimensions(self) -> None:
        reports = run_benchmark(
            TaskSpec(seed=4, num_tags=33, distribution="long_tail"),
            seeds=[4],
            sizes=[33],
            distributions=["long_tail"],
            protocol_names=("shared_sketch",),
        )
        metadata = reports[0].run_result.metadata
        self.assertEqual(metadata["distribution"], "long_tail")
        self.assertEqual(metadata["num_tags"], 33)
        self.assertEqual(metadata["enabled_tasks"], ["mean", "variance", "cardinality", "top_k"])

    def test_web_benchmark_state_covers_liu_requirements(self) -> None:
        state = _statistical_benchmark_state()
        self.assertTrue(state["available"])
        self.assertEqual(state["dimensions"]["scales"], [1000, 10000, 100000])
        self.assertEqual(len(state["dimensions"]["seeds"]), 20)
        coverage = {item["key"]: item for item in state["coverage"]}
        self.assertEqual(coverage["recheck"]["status"], "complete")
        self.assertEqual(coverage["integration"]["status"], "other_owner")
        expected_llm = "measured" if state["manifest"]["llm_status"] == "record_supplied" else "needs_model"
        self.assertEqual(coverage["llm"]["status"], expected_llm)

    def test_shared_collection_is_charged_once_and_serial_is_not(self):
        spec = TaskSpec(num_tags=300, tasks=("mean", "variance", "cardinality"))
        tags = generate_tags(spec)
        shared = SharedSketchProtocol().run_many(tags, spec)
        serial = SerialTaskProtocol().run_many(tags, spec)
        self.assertEqual(len({r.metadata["collection_id"] for r in shared.values()}), 1)
        for task in spec.tasks:
            self.assertEqual(serial[task].communication_bits, shared[task].communication_bits * 3)
            self.assertEqual(serial[task].slots, shared[task].slots * 3)
            self.assertEqual(serial[task].estimate, shared[task].estimate)

    def test_channel_counts_transmissions_and_idle_slots(self):
        spec = TaskSpec(num_tags=19, price_min=1, price_max=8)
        observation = HistogramChannel(generate_tags(spec), spec).observe(0, 32)
        self.assertEqual(observation.tag_bits, 19)
        self.assertEqual(observation.reader_bits, 160)
        self.assertEqual(observation.slots, 8 * 32)
        self.assertEqual(observation.frames, 1)

    def test_fixed_and_adaptive_rounds_derive_cost_from_trace(self):
        spec = TaskSpec(num_tags=600, distribution="long_tail", seed=13)
        tags = generate_tags(spec)
        registry = protocol_registry(spec)
        fixed = registry["fixed_4"].run_many(tags, spec)["mean"]
        adaptive = registry["adaptive"].run_many(tags, spec)["mean"]
        self.assertEqual(fixed.rounds, 4)
        self.assertTrue(1 <= adaptive.rounds <= 4)
        self.assertEqual(adaptive.rounds, len(adaptive.metadata["trace"]))
        self.assertEqual(adaptive.tag_bits, len(tags) * adaptive.rounds)
        self.assertEqual(adaptive.reader_bits, 160 * adaptive.rounds)

    def test_absent_llm_cannot_become_zero_cost_pareto_winner(self):
        spec = TaskSpec(num_tags=20)
        reports = run_benchmark(spec, protocol_names=("shared_sketch", "llm_random"))
        missing = [r for r in reports if r.protocol == "llm_random"]
        self.assertTrue(all(r.status == "failed" for r in missing))
        self.assertTrue(all("missing_real_llm_candidate" in r.failure_reason for r in missing))
        self.assertNotIn("llm_random", {r["protocol"] for r in pareto_front(reports)})

    def test_pareto_compares_same_task_only(self):
        mean_run = RunResult("mean_only", "mean", 1, 1, 1, 1, 1, 1, 1, 0)
        variance_run = RunResult("variance_only", "variance", 1, 1, 100, 100, 1, 1, 1, 0)
        front = pareto_front([evaluate_run(mean_run), evaluate_run(variance_run)])
        self.assertEqual({r["task"] for r in front}, {"mean", "variance"})

    def test_strict_topk_does_not_award_subset_full_credit(self):
        report = evaluate_run(RunResult("test", "top_k", [1, 2, 3], [1], 1, 1, 1, 1, 1, 0))
        self.assertEqual(report.precision, 1)
        self.assertEqual(report.recall, 1 / 3)
        self.assertLess(report.f1, 1)

    def test_genotype_whitelist_never_executes_arbitrary_code(self):
        record = dict(candidate_id="fixture", model="fixture", prompt_hash="fixture", response_hash="fixture", genotype={"operator": "anonymous_histogram", "rounds": 2, "slots_per_value": 128, "threshold": None})
        self.assertEqual(compile_candidate(record).max_rounds, 2)
        for field, value in (("rounds", 0), ("slots_per_value", 1000000), ("operator", "exec"), ("threshold", float("nan"))):
            invalid = {**record, "genotype": {**record["genotype"], field: value}}
            with self.assertRaises(ValueError):
                compile_candidate(invalid)

    def test_scenarios_keep_seed_and_ids_with_missing_tags(self):
        spec = TaskSpec(num_tags=100, missing_rate=.3, noise_rate=.1, extreme_rate=.1, seed=7)
        first = generate_tags(spec)
        self.assertEqual([(t.id, t.price) for t in first], [(t.id, t.price) for t in generate_tags(spec)])
        self.assertLess(len(first), 100)
        self.assertTrue(all(0 <= t.id < 100 for t in first))

    def test_unsupported_single_tasks_are_not_mislabelled_as_failures(self):
        reports = run_benchmark(TaskSpec(num_tags=20), protocol_names=("single_mean",))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].task, "mean")

    def test_bad_task_spec_is_rejected(self):
        for settings in ({"tasks": ()}, {"missing_rate": 1.1}, {"price_min": 9, "price_max": 1}, {"distribution": "typo"}):
            with self.assertRaises(ValueError):
                TaskSpec(**settings)


if __name__ == "__main__":
    unittest.main()
