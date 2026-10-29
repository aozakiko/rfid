from __future__ import annotations

import subprocess
import sys
import unittest

from benchmark.experiments import ablation_experiment, pareto_front
from benchmark.runner import run_benchmark, summarize_reports
from benchmark.scenarios import generate_tags
from stat_protocols.contracts import CostModel, TaskSpec
from stat_protocols.protocols import PollingProtocol, SharedSketchProtocol


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


if __name__ == "__main__":
    unittest.main()
