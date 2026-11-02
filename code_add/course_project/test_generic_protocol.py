from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from dataclasses import replace

from base import Tag
from generic_protocol import CandidateRecord, LegacyProtocolAdapter, StatisticsProtocolAdapter, candidate_from_mapping, run_protocol_file
from generic_protocol.security import analyze_source
from stat_protocols.contracts import RunResult, TaskSpec


class FakeStatisticsProtocol:
    name = "fake_statistics"

    def __init__(self, tasks=("mean",)):
        self.tasks = tuple(tasks)
        self.seen_channel = None

    def run_many(self, tags, spec, channel=None):
        self.seen_channel = channel
        return {
            task: RunResult(
                protocol=self.name,
                task=task,
                truth=1.0,
                estimate=1.0,
                reader_bits=7,
                tag_bits=5,
                slots=2,
                frames=1,
                rounds=1,
                seed=spec.seed,
                metadata={"kept": "yes"},
            )
            for task in spec.tasks
            if task in self.tasks
        }


class LegacyFixture:
    def __init__(self, values, metric, execution_time, verification_data):
        self.values = values
        self.metric = metric
        self.execution_time = execution_time
        self.verification_data = verification_data
        self.seen = None

    def execute(self, tags, price_min, price_max):
        self.seen = (tags, price_min, price_max)
        return self.values, self.metric, self.execution_time, self.verification_data


class GenericProtocolTests(unittest.TestCase):
    def test_strict_ast_report_blocks_reader_access_and_dangerous_calls(self):
        unsafe = """
import os
class Unsafe:
    def execute(self, tags, price_min, price_max):
        return [tags[0].price], 1, 0.0, {}
"""
        report = analyze_source(unsafe, strict=True)
        rules = {item["rule"] for item in report["violations"]}
        self.assertFalse(report["passed"])
        self.assertIn("blocked_import", rules)
        self.assertIn("direct_tag_attribute", rules)

    def test_strict_ast_allows_explicit_tag_response_helper(self):
        safe = """
class Safe:
    def _simulate_tag_response(self, tag):
        return tag.price
    def execute(self, tags, price_min, price_max):
        return [self._simulate_tag_response(tags[0])], 1, 0.0, {}
"""
        report = analyze_source(safe, strict=True)
        self.assertTrue(report["passed"])

    def test_strict_ast_blocks_tag_alias_and_builtin_escape(self):
        unsafe = """
class Unsafe:
    def execute(self, tags, price_min, price_max):
        loader = open
        for candidate in tags:
            value = candidate.price
        return [loader(\"x\")], 1, 0.0, {}
"""
        report = analyze_source(unsafe, strict=True)
        rules = {item["rule"] for item in report["violations"]}
        self.assertIn("direct_tag_attribute", rules)
        self.assertIn("blocked_call", rules)

    def test_strict_ast_does_not_trust_arbitrary_tag_named_helper(self):
        report = analyze_source(
            """
class Unsafe:
    def tag_reader(self, tag):
        return tag.price
""",
            strict=True,
        )
        self.assertFalse(report["passed"])
        self.assertIn("direct_tag_attribute", {item["rule"] for item in report["violations"]})

    def test_statistics_adapter_preserves_counters_and_metadata_and_forwards_channel(self):
        protocol = FakeStatisticsProtocol()
        adapter = StatisticsProtocolAdapter(protocol)
        spec = TaskSpec(seed=4, num_tags=4, tasks=("mean",))
        channel = object()
        result = adapter.run_many([Tag(0, 1)], spec, {"channel": channel})["mean"]
        self.assertIs(protocol.seen_channel, channel)
        self.assertEqual((result.reader_bits, result.tag_bits), (7, 5))
        self.assertEqual(result.metadata["kept"], "yes")

    def test_statistics_adapter_marks_unsupported_task_without_executing_it(self):
        protocol = FakeStatisticsProtocol(tasks=("mean",))
        adapter = StatisticsProtocolAdapter(protocol)
        spec = TaskSpec(seed=2, tasks=("mean", "cardinality"))
        results = adapter.run_many([Tag(0, 1)], spec)
        self.assertEqual(set(results), {"mean", "cardinality"})
        self.assertEqual(results["cardinality"].failure_reason, "unsupported_task")

    def test_legacy_adapter_maps_execute_fields_and_missing_cost_status(self):
        legacy = LegacyFixture([2, 1], 2, 12.5, {"frame_size": 8, "bits_per_slot": 2})
        adapter = LegacyProtocolAdapter(legacy, name="legacy_fixture")
        spec = replace(TaskSpec(seed=7, price_min=1, price_max=3), task="legacy_mode_query")
        tags = [Tag(1, 1), Tag(2, 2), Tag(3, 2), Tag(4, 1)]
        result = adapter.run_many(tags, spec)["legacy_mode_query"]
        self.assertEqual(result.protocol, "legacy_fixture")
        self.assertEqual(result.estimate, [2, 1])
        self.assertEqual(result.truth, [1, 2])
        self.assertEqual(result.cpu_time_ms, 12.5)
        self.assertEqual(result.metadata["metric"], 2)
        self.assertEqual(result.metadata["metric_kind"], "set")
        self.assertEqual(result.metadata["cost_status"], "missing")
        self.assertEqual(result.metadata["verification_data"], {"frame_size": 8, "bits_per_slot": 2})
        self.assertEqual(legacy.seen[1:], (1, 3))

    def test_legacy_adapter_uses_reported_communication_counters(self):
        legacy = LegacyFixture([1], 1, 0.5, {
            "reader_bits": 4,
            "tag_bits": 3,
            "slots": 2,
            "frames": 1,
            "rounds": 1,
        })
        result = LegacyProtocolAdapter(legacy).run_many(
            [Tag(0, 1)],
            replace(TaskSpec(seed=1, price_min=1, price_max=1, top_k=1), task="legacy_mode_query"),
        )["legacy_mode_query"]
        self.assertEqual(result.metadata["cost_status"], "measured")
        self.assertEqual(
            (result.reader_bits, result.tag_bits, result.slots, result.frames, result.rounds),
            (4, 3, 2, 1, 1),
        )

    def test_subprocess_runner_executes_protocol_file_with_json_boundary(self):
        source = '''
class DemoProtocol:
    def execute(self, tags, price_min, price_max):
        print("candidate stdout must not pollute JSON")
        return [price_max], 1, 2.5, {"reader_bits": 5, "tag_bits": 4, "slots": 2, "frames": 1, "rounds": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "demo.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=5, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(protocol_file, "DemoProtocol", [Tag(0, 2), Tag(1, 3)], spec)["legacy_mode_query"]
        self.assertEqual(result.estimate, [3])
        self.assertEqual(result.cpu_time_ms, 2.5)
        self.assertEqual(result.metadata["cost_status"], "measured")
        self.assertEqual(result.reader_bits, 5)

    def test_subprocess_timeout_returns_failure_row(self):
        source = '''
import time
class SlowProtocol:
    def execute(self, tags, price_min, price_max):
        time.sleep(2)
        return [1], 1, 0.0, {}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "slow.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=0, price_min=1, price_max=1, top_k=1), task="legacy_mode_query")
            result = run_protocol_file(protocol_file, "SlowProtocol", [Tag(0, 1)], spec, timeout_seconds=0.2)["legacy_mode_query"]
        self.assertIn("subprocess_timeout", result.failure_reason)
        self.assertEqual(result.metadata["cost_status"], "missing")

    def test_strict_runtime_proxy_blocks_direct_tag_access(self):
        source = '''
class StrictProtocol:
    def execute(self, tags, price_min, price_max):
        return [tags[0].price], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "strict.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=1, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(
                protocol_file,
                "StrictProtocol",
                [Tag(0, 2)],
                spec,
                security_mode="strict",
            )["legacy_mode_query"]
        self.assertTrue(
            "strict_ast_violation" in result.failure_reason
            or "InformationIsolationViolation" in result.failure_reason
        )

    def test_strict_runtime_proxy_blocks_proxy_internals(self):
        source = '''
class StrictInternalProtocol:
    def execute(self, tags, price_min, price_max):
        field = "_tag"
        return [getattr(tags[0], field).price], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "strict_internal.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=1, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(
                protocol_file,
                "StrictInternalProtocol",
                [Tag(0, 2)],
                spec,
                security_mode="strict",
            )["legacy_mode_query"]
        self.assertIn("InformationIsolationViolation", result.failure_reason)

    def test_strict_runtime_proxy_blocks_dynamic_sensitive_read(self):
        source = '''
class StrictDynamicProtocol:
    def execute(self, tags, price_min, price_max):
        field = "price"
        return [getattr(tags[0], field)], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "strict_dynamic.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=1, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(
                protocol_file,
                "StrictDynamicProtocol",
                [Tag(0, 2)],
                spec,
                security_mode="strict",
            )["legacy_mode_query"]
        self.assertIn("InformationIsolationViolation", result.failure_reason)

    def test_strict_runtime_proxy_blocks_reflection_and_repr_leaks(self):
        source = '''
class StrictReflectProtocol:
    def execute(self, tags, price_min, price_max):
        return [vars(tags[0])], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "strict_reflect.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=1, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(protocol_file, "StrictReflectProtocol", [Tag(0, 2)], spec, security_mode="strict")["legacy_mode_query"]
        self.assertTrue("strict_ast_violation" in result.failure_reason or "InformationIsolationViolation" in result.failure_reason)

    def test_strict_runtime_proxy_allows_tag_response_helper(self):
        source = '''
class StrictSafeProtocol:
    def _simulate_tag_response(self, tag):
        return tag.price
    def execute(self, tags, price_min, price_max):
        return [self._simulate_tag_response(tags[0])], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "strict_safe.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=1, price_min=1, price_max=3), task="legacy_mode_query")
            result = run_protocol_file(
                protocol_file,
                "StrictSafeProtocol",
                [Tag(0, 2)],
                spec,
                security_mode="strict",
            )["legacy_mode_query"]
        self.assertFalse(result.failure_reason)
        self.assertEqual(result.estimate, [2])

    def test_bad_file_and_protocol_exception_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_file = Path(directory) / "bad.py"
            bad_file.write_text("def broken(:\n", encoding="utf-8")
            bad_spec = replace(TaskSpec(seed=0, price_min=1, price_max=1, top_k=1), task="legacy_mode_query")
            bad = run_protocol_file(bad_file, "Missing", [Tag(0, 1)], bad_spec)["legacy_mode_query"]
            self.assertIn("SyntaxError", bad.failure_reason)

            error_file = Path(directory) / "error.py"
            error_file.write_text(
                "import os\n"
                "class ErrorProtocol:\n"
                "    def execute(self, tags, price_min, price_max):\n"
                "        os.environ['GENERIC_PROTOCOL_POLLUTION'] = '1'\n"
                "        raise RuntimeError('candidate failure')\n",
                encoding="utf-8",
            )
            os.environ.pop("GENERIC_PROTOCOL_POLLUTION", None)
            failed = run_protocol_file(error_file, "ErrorProtocol", [Tag(0, 1)], bad_spec)["legacy_mode_query"]
            self.assertIn("RuntimeError", failed.failure_reason)
            self.assertNotIn("GENERIC_PROTOCOL_POLLUTION", os.environ)

    def test_strict_runner_rejects_dangerous_import_before_execution(self):
        source = '''
import os
os.environ["GENERIC_PROTOCOL_POLLUTION"] = "1"
class UnsafeImport:
    def execute(self, tags, price_min, price_max):
        return [1], 1, 0.0, {"frame_size": 1, "bits_per_slot": 1}
'''
        with tempfile.TemporaryDirectory() as directory:
            protocol_file = Path(directory) / "unsafe_import.py"
            protocol_file.write_text(source, encoding="utf-8")
            spec = replace(TaskSpec(seed=0, price_min=1, price_max=1, top_k=1), task="legacy_mode_query")
            os.environ.pop("GENERIC_PROTOCOL_POLLUTION", None)
            result = run_protocol_file(protocol_file, "UnsafeImport", [Tag(0, 1)], spec, security_mode="strict")["legacy_mode_query"]
        self.assertIn("strict_ast_violation", result.failure_reason)
        self.assertNotIn("GENERIC_PROTOCOL_POLLUTION", os.environ)


    def test_candidate_record_round_trips_without_api_key_field(self):
        record = CandidateRecord(
            candidate_id="a" * 32,
            protocol_name="GeneratedProtocol",
            candidate_type="legacy_python",
            parents=("A", "B"),
            task_profile="mode_query",
            genotype={"class_name": "GeneratedProtocol"},
            prompt_hash="b" * 64,
            response_hash="c" * 64,
            model="test-model",
            source_hash="d" * 64,
            validation_result={"is_valid": True},
            benchmark_result=None,
            status="validated",
        )
        payload = record.to_dict()
        self.assertNotIn("api_key", payload)
        self.assertEqual(candidate_from_mapping(payload), record)


if __name__ == "__main__":
    unittest.main()

