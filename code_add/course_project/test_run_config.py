from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmark.run_config import RunConfig, load_config, run_config
from tools.verify import verify


ROOT = Path(__file__).resolve().parents[1]


def config_payload() -> dict:
    return {
        "name": "unit-smoke",
        "profiles": ["mean_estimation", "top_k", "mode_query", "custom_task"],
        "protocols": ["shared_sketch"],
        "distributions": ["uniform"],
        "sizes": [8],
        "seeds": [0],
        "output_dir": "unused-output",
        "price_min": 1,
        "price_max": 8,
        "missing_rate": 0.0,
        "noise_rate": 0.0,
        "extreme_rate": 0.0,
        "split": "evaluation",
    }


class RunConfigTests(unittest.TestCase):
    def write_config(self, directory: Path, updates: dict | None = None) -> Path:
        payload = config_payload()
        payload.update(updates or {})
        path = directory / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_config_is_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config = load_config(self.write_config(directory))
            self.assertIsInstance(config, RunConfig)
            self.assertEqual(config.name, "unit-smoke")
            self.assertEqual(config.sizes, [8])

            with self.assertRaises(ValueError):
                load_config(self.write_config(directory, {"missing_rate": 1.5}))
            with self.assertRaises(ValueError):
                load_config(self.write_config(directory, {"unknown": True}))
            with self.assertRaises(ValueError):
                load_config(self.write_config(directory, {"profiles": ["unknown_profile"]}))
            with self.assertRaises(ValueError):
                load_config(self.write_config(directory, {"seeds": [0, 0]}))

    def test_run_config_writes_traceable_outputs_and_support(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = directory / "output"
            config_path = self.write_config(directory)
            manifest = run_config(
                config_path,
                output_override=output,
                command=["python", "-m", "benchmark.run_config"],
            )

            expected = {
                "results.json",
                "summary.csv",
                "profile_support.json",
                "manifest.json",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            self.assertEqual(manifest["status"], "success")
            for key in (
                "run_id",
                "config_hash",
                "command",
                "start_commit",
                "git_dirty",
                "python_version",
                "package_versions",
                "profiles",
                "protocols",
                "distributions",
                "sizes",
                "seeds",
                "status",
                "output_files",
                "experiment_generation_commit",
                "release_commit",
            ):
                self.assertIn(key, manifest)

            results = json.loads((output / "results.json").read_text(encoding="utf-8"))
            report_tasks = {report["task"] for report in results["reports"]}
            self.assertTrue(report_tasks.issubset({"mean", "variance", "cardinality", "top_k"}))
            self.assertNotIn("legacy_mode_query", report_tasks)
            self.assertNotIn("custom_task", report_tasks)

            support = json.loads((output / "profile_support.json").read_text(encoding="utf-8"))
            by_name = {item["profile"]: item for item in support["profiles"]}
            self.assertTrue(by_name["mean_estimation"]["benchmarkable"])
            self.assertFalse(by_name["mode_query"]["benchmarkable"])
            self.assertFalse(by_name["custom_task"]["benchmarkable"])
            self.assertIsNone(by_name["mode_query"]["accuracy"])
            self.assertIsNone(by_name["custom_task"]["accuracy"])
            self.assertEqual(by_name["custom_task"]["accuracy_policy"], "not_available")

            self.assertTrue((output / "summary.csv").read_text(encoding="utf-8-sig"))

    def test_verify_runs_compile_unittest_and_smoke_path(self):
        self.assertEqual(verify(run_unittests=False), 0)


if __name__ == "__main__":
    unittest.main()