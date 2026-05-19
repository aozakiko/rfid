from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from course_project.database import database_stats, init_database, store_ai_job
from course_project.web_app import (
    read_project_file,
    run_ai_generation,
    run_experiment_task,
    run_selfopt_auto_iterate,
    selfopt_state,
    validate_selfopt_protocol,
)


class CourseProjectSmokeTest(unittest.TestCase):
    def test_selfopt_state_exposes_current_platform_assets(self) -> None:
        state = selfopt_state()
        scene_names = [scene["name"] for scene in state["scenes"]]

        self.assertIn("test", scene_names)
        self.assertIn("multi_mode", scene_names)
        self.assertIn("mode_query", state["task_profiles"])
        self.assertTrue(state["base_protocols"])
        self.assertIn("ai_jobs", state)

    def test_project_file_reader_serves_test_scene(self) -> None:
        task_file = read_project_file("test/Task.py")

        self.assertEqual(task_file["name"], "Task.py")
        self.assertIn("generate_tags", task_file["content"])
        self.assertIn("deterministic_two_peak_with_tail", task_file["content"])

    def test_validate_test_scene_protocol(self) -> None:
        result = validate_selfopt_protocol(
            {
                "scene": "test",
                "protocol": "ModeQuerySEM",
                "task_profile": "mode_query",
                "num_tags": 120,
                "total_runs": 1,
                "price_min": 100,
                "price_max": 900,
            }
        )

        self.assertTrue(result["validator_passed"])
        self.assertTrue(result["information_isolation_passed"])
        self.assertGreaterEqual(result["pass_rate"], 90.0)

    def test_ai_generation_and_auto_iteration_dry_run(self) -> None:
        ai_job = run_ai_generation(
            {
                "scene": "test",
                "parent_a": "ModeQuerySEM",
                "parent_b": "ModeQueryTKQ",
                "protocol_name": "unit_ai_generation_dry_run",
                "target_generation": "next",
                "task_profile": "mode_query",
                "dry_run": True,
            }
        )
        created_prompts: list[Path] = []
        try:
            if ai_job["result"].get("prompt_file"):
                created_prompts.append(Path(str(ai_job["result"]["prompt_file"])))
            self.assertEqual(ai_job["result"]["status"], "dry_run")

            iteration = run_selfopt_auto_iterate(
                {
                    "scene": "test",
                    "parents": ["ModeQuerySEM", "ModeQueryTKQ", "g2_2"],
                    "prefix": "unit_selfopt_iter",
                    "iterations": 2,
                    "task_profile": "mode_query",
                    "dry_run": True,
                    "validate_num_tags": 60,
                }
            )
            for item in iteration["iterations"]:
                if item.get("prompt_file"):
                    created_prompts.append(Path(str(item["prompt_file"])))
            self.assertEqual(len(iteration["iterations"]), 2)
            self.assertTrue(all(item["status"] == "dry_run" for item in iteration["iterations"]))
        finally:
            for prompt_path in created_prompts:
                if prompt_path.exists():
                    prompt_path.unlink()

    def test_database_records_ai_jobs(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "course.sqlite3"
            init_database(db_path)
            job_id = store_ai_job(
                request={
                    "scene": "test",
                    "parent_a": "ModeQuerySEM",
                    "parent_b": "ModeQueryTKQ",
                    "protocol_name": "unit_db_job",
                    "target_generation": "next",
                    "dry_run": True,
                },
                result={"status": "dry_run", "protocol_exists": False},
                logs=["dry run"],
                db_path=db_path,
            )

            stats = database_stats(db_path=db_path)
            self.assertGreater(job_id, 0)
            self.assertEqual(stats["counts"]["ai_jobs"], 1)
            self.assertEqual(stats["ai_jobs"][0]["protocol_name"], "unit_db_job")

    def test_experiment_task_runs_on_test_outputs(self) -> None:
        result = run_experiment_task(
            {
                "task": "plot_final",
                "source_scene": "test",
                "output_dir": "Experiments",
                "timeout_seconds": 120,
            }
        )

        self.assertEqual(result["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
