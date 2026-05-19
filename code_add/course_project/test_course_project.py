from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from cloud_edge_demo.pipeline import run_distributed_demo
from course_project.database import get_run_detail, init_database, store_course_run
from course_project.platform_core import build_design_case, infer_from_natural_language
from course_project.security import run_security_check
from course_project.web_app import (
    read_project_file,
    run_ai_generation,
    run_experiment_task,
    run_selfopt_auto_iterate,
    run_selfopt_batch_test,
    selfopt_state,
    validate_selfopt_protocol,
)


class CourseProjectSmokeTest(unittest.TestCase):
    def test_cloud_edge_device_pipeline(self) -> None:
        result = run_distributed_demo(
            scene="multi_mode",
            protocol_name="g2_2",
            device_count=1,
            gateway_count=1,
            batch_count=1,
            num_tags=20,
            price_min=1,
            price_max=60,
            stream_window_size=2,
        )

        self.assertEqual(result["scenario"]["device_count"], 1)
        self.assertEqual(result["scenario"]["gateway_count"], 1)
        self.assertEqual(len(result["stream_snapshots"]), 1)
        self.assertEqual(result["batch_report"]["total_windows"], 1)
        self.assertIn("gateway_summary", result["batch_report"])

    def test_security_check_rejects_tampering(self) -> None:
        result = run_security_check()

        self.assertTrue(result["valid_message_accepted"])
        self.assertTrue(result["tampered_payload_rejected"])
        self.assertTrue(result["wrong_secret_rejected"])
        self.assertTrue(result["missing_signature_rejected"])
        self.assertTrue(result["passed"])

    def test_database_persists_run(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "course.sqlite3"
            output_dir = Path(tmp) / "outputs"
            init_database(db_path)
            result = run_distributed_demo(
                scene="multi_mode",
                protocol_name="g2_2",
                device_count=1,
                gateway_count=1,
                batch_count=1,
                num_tags=20,
                price_min=1,
                price_max=60,
                stream_window_size=2,
            )

            run_id = store_course_run(
                config={
                    "scene": "multi_mode",
                    "protocol": "g2_2",
                    "devices": 1,
                    "gateways": 1,
                    "batches": 1,
                    "num_tags": 20,
                    "price_min": 1,
                    "price_max": 60,
                    "stream_window": 2,
                    "shared_secret": "rfid-course-demo-secret",
                },
                result=result,
                security_result=run_security_check(),
                benchmark_rows=[],
                output_dir=output_dir,
                dashboard_path=None,
                wall_clock_s=0.01,
                db_path=db_path,
            )
            detail = get_run_detail(run_id, db_path=db_path)

            self.assertIsNotNone(detail)
            self.assertEqual(detail["total_windows"], 1)
            self.assertEqual(len(detail["stream_snapshots"]), 1)
            self.assertEqual(len(detail["gateway_summaries"]), 1)

    def test_design_model_generates_prompt_and_cloud_edge_manifest(self) -> None:
        parse = infer_from_natural_language(
            "冷链仓库需要 500ms 内监测药品温湿度，识别丢失标签，边缘低功耗，云端追溯审计。"
        )
        self.assertEqual(parse["preset"], "fresh_cold_chain")
        self.assertIn("temperature_outlier", parse["task_ids"])
        self.assertIn("traceability", parse["priorities"])

        case = build_design_case(
            {
                "requirement": "冷链仓库需要 500ms 内监测药品温湿度，识别丢失标签，边缘低功耗，云端追溯审计。",
                "scene": "multi_mode",
                "devices": 3,
                "gateways": 2,
            },
            ["g2_1", "g2_2", "ModeQueryTKQ", "ModeQueryTCS"],
        )

        self.assertIn("GRDL", case["summary"]["optimization_route"])
        self.assertIn("LLM", case["llm_prompt"])
        self.assertEqual(len(case["deployment"]["devices"]), 3)
        self.assertEqual(len(case["deployment"]["gateways"]), 2)
        self.assertTrue(case["charts"]["protocol_scores"])

    def test_selfopt_web_core_flows(self) -> None:
        state = selfopt_state()
        scene_names = [scene["name"] for scene in state["scenes"]]
        self.assertIn("multi_mode", scene_names)

        task_file = read_project_file("multi_mode/Task.py")
        self.assertIn("generate_tags", task_file["content"])

        iteration = run_selfopt_auto_iterate(
            {
                "scene": "multi_mode",
                "parents": ["g2_1", "g2_2"],
                "prefix": "unit_selfopt_iter",
                "iterations": 1,
                "dry_run": True,
                "validate_num_tags": 40,
            }
        )
        self.assertEqual(len(iteration["iterations"]), 1)
        self.assertEqual(iteration["iterations"][0]["status"], "dry_run")

        ai_job = run_ai_generation(
            {
                "scene": "multi_mode",
                "parent_a": "g2_1",
                "parent_b": "g2_2",
                "protocol_name": "unit_ai_generation_dry_run",
                "target_generation": "next",
                "dry_run": True,
            }
        )
        self.assertEqual(ai_job["result"]["status"], "dry_run")

        single = validate_selfopt_protocol(
            {
                "scene": "multi_mode",
                "protocol": "g2_1",
                "num_tags": 40,
                "total_runs": 1,
                "price_min": 1,
                "price_max": 1000,
            }
        )
        self.assertTrue(single["validator_passed"])
        self.assertEqual(single["pass_rate"], 100.0)

        batch = run_selfopt_batch_test(
            {
                "scene": "multi_mode",
                "protocols": ["g2_1"],
                "num_tags": 40,
                "total_runs": 1,
                "price_min": 1,
                "price_max": 1000,
                "timeout_seconds": 60,
            }
        )
        self.assertEqual(batch["returncode"], 0)
        self.assertTrue(batch["results"])

        experiment = run_experiment_task(
            {
                "task": "robust",
                "protocols": "g8_3:multi_mode",
                "factors": "density",
                "num_tags": 40,
                "total_runs": 1,
                "point_count": 2,
                "output": "Experiments/sensitivity_results_unit.csv",
                "timeout_seconds": 120,
            }
        )
        self.assertEqual(experiment["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
