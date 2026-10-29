from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend_tools.openai_compatible import LLMConfig
from course_project import statistics_service as service
from course_project.test_infrastructure import HTTPTestServer
from stat_protocols.contracts import RunResult
from benchmark.runner import evaluate_run, write_results


class StatisticsServiceTests(unittest.TestCase):
    def test_details_keep_failed_rows_and_filter_float_scenario(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [evaluate_run(RunResult("fixture", "mean", 1, 1, 1, 1, 1, 1, 1, seed, metadata={"distribution": "uniform", "num_tags": 1000, "missing_rate": .1, "noise_rate": 0.0, "extreme_rate": 0.0})) for seed in range(25)]
            rows.append(evaluate_run(RunResult("fixture", "mean", None, None, 0, 0, 0, 0, 0, 25, failure_reason="real_failure", metadata={"distribution": "uniform", "num_tags": 1000, "missing_rate": .1, "noise_rate": 0.0, "extreme_rate": 0.0})))
            write_results(rows, root / "main" / "main_reports.json")
            with patch.object(service, "current_root", return_value=root):
                result = service.details({"mode": ["main"], "scenario": ["0.1|0|0"], "page": ["2"]})
                self.assertEqual(result["total"], 26)
                self.assertEqual(len(result["rows"]), 6)
                self.assertEqual(result["rows"][-1]["failure_reason"], "real_failure")

    def test_one_shot_llm_records_provenance_and_retains_invalid_response(self):
        config = LLMConfig(base_url="https://example.invalid/v1", api_key="never-save-this-key", model="fixture-model")
        for response, valid in ((json.dumps({"operator": "anonymous_histogram", "rounds": 2, "slots_per_value": 64, "threshold": None}), True), ("print('not a genotype')", False)):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as folder:
                with patch.object(service, "ROOT", Path(folder)), patch.object(service, "EXPERIMENT_ROOT", Path(folder) / "Experiments"), patch("backend_tools.openai_compatible.OpenAICompatibleClient._request_json", return_value={"choices": [{"message": {"content": response}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}) as request:
                    result = service.generate_baseline(config)
                self.assertEqual(result["valid"], valid)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(len(result["record"]["prompt_hash"]), 64)
                for path in Path(folder).rglob("*"):
                    if path.is_file():
                        self.assertNotIn(config.api_key, path.read_text(encoding="utf-8"))
                self.assertEqual(result["record"]["usage"]["completion_tokens"], 20)

    def test_import_rejects_python_and_unknown_operators(self):
        with self.assertRaises(ValueError):
            service.save_candidate({"source": "import os"})


class StatisticsHTTPTests(HTTPTestServer):
    def test_statistics_endpoints_and_input_validation(self):
        self.assertEqual(self.request("/static/statistics.js")[0], 200)
        status, _, body = self.request("/api/statistics/benchmark")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("hypotheses", data)
        self.assertEqual(self.request("/api/statistics/details?mode=../private")[0], 400)
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/statistics/run", b'{"seed_count":999}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/run", b'{"candidate_id":"../../secret"}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/import-candidate", b'{"record":{"code":"import os"}}', headers)[0], 400)
        self.assertEqual(self.request("/api/statistics/generate-baseline", b'{}', headers)[0], 400)


if __name__ == "__main__":
    unittest.main()
