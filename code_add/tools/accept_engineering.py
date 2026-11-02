"""Live Web acceptance with an explicitly offline model fixture (no gateway)."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from playwright.sync_api import sync_playwright

import ai_protocol_optimizer as optimizer
from backend_tools.openai_compatible import LLMConfig
from benchmark.provenance import audit_manifest
from course_project import engineering_service as service
from course_project.test_engineering import SAFE_SOURCE, generator


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "course_project/outputs/engineering_check_v2"
BASE = "http://127.0.0.1:8765"


def offline_evidence():
    target = OUTPUT / ("offline_fixture_" + uuid4().hex)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        scene = root / "offline_fixture"
        scene.mkdir()
        (scene / "Task.py").write_text("# Offline acceptance fixture; not a research result\n", encoding="utf-8")
        (scene / "Parent.py").write_bytes(SAFE_SOURCE.encode())
        kwargs = dict(scene_name=scene.name, parent_a="Parent", parent_b="Parent", protocol_name="Repaired",
            target_generation="generation_1", temperature=.1, max_tokens=1000, timeout_seconds=10,
            validate_num_tags=8, max_repair_rounds=1, dry_run=False, logger=lambda message: None,
            llm_config=LLMConfig(base_url="http://offline.invalid", api_key="offline-fixture", model="offline-fixture"))
        with patch.object(optimizer, "PROJECT_ROOT", root), patch.object(optimizer, "_build_optimizer_prompt", return_value="offline acceptance fixture"), \
             patch.object(optimizer, "_load_scene_task", return_value=(generator, {"PRICE_MIN": 1, "PRICE_MAX": 2})), \
             patch.object(optimizer.OpenAICompatibleClient, "generate_text", side_effect=["import os\n" + SAFE_SOURCE, SAFE_SOURCE, SAFE_SOURCE]):
            first = optimizer.run_ai_optimization_workflow(**kwargs)
            second = optimizer.run_ai_optimization_workflow(**{**kwargs, "protocol_name": "Child", "parent_a": "Repaired", "target_generation": "generation_2"})
        assert first["status"] == second["status"] == "success"
        with patch.object(service, "ROOT", root), patch.object(service, "EXPERIMENT_ROOT", root / "Experiments"):
            lineage = service.candidate_lineage()
        shutil.copytree(root, target)
    prefix = target.relative_to(ROOT).as_posix() + "/"
    # Multiple candidates can share one workflow object in the service result.
    # Detach references before remapping this copied fixture for browser tests.
    lineage = json.loads(json.dumps(lineage))
    for node in lineage["nodes"]:
        node["record_path"] = prefix + node["record_path"]
        node["record_paths"] = [prefix + p for p in node["record_paths"]]
        if node["workflow"]:
            for attempt in node["workflow"]["attempts"]:
                attempt["artifact_paths"] = {name: prefix + path for name, path in attempt["artifact_paths"].items()}
    lineage["note"] = "Offline acceptance fixture: fixed model responses, real validator/subprocess/evaluator. Not a research candidate."
    (OUTPUT / "offline_lineage.json").write_text(json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")
    return lineage


def wait_job(page, job_id):
    for _ in range(360):
        job = page.request.get(BASE + "/api/selfopt/job?id=" + job_id).json()["job"]
        if job["status"] in {"success", "failed"}:
            return job
        page.wait_for_timeout(500)
    raise AssertionError("Web job timed out")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fixture = offline_evidence()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until="networkidle")
        actual = page.request.get(BASE + "/api/selfopt/candidates").json()
        expected_candidates = set()
        for path in (ROOT / "Experiments/stat_protocols/runs").glob("*/manifest.json"):
            record = json.loads(path.read_text(encoding="utf-8")).get("llm_record") or {}
            if record.get("candidate_id") and audit_manifest(path.parent)["passed"]:
                expected_candidates.add(record["candidate_id"])
        linked_candidates = {node["candidate_id"] for node in actual["nodes"] if node["formal_results"]}
        assert expected_candidates <= linked_candidates, "Recorded statistical candidates must link audited formal results"
        formal_check = "passed" if expected_candidates else "not_available_no_audited_candidate_archive"
        page.locator('nav button[data-view="statistics"]').click()
        page.wait_for_function("state.statistics && state.statistics.available")
        for name, value in {"configSeeds": ",".join(map(str, range(20))), "configSizes": "1000",
            "configDistributions": "uniform,long_tail", "configProtocols": "polling,single_serial,shared_sketch,adaptive",
            "configMax": "64", "pipelineValidationSeeds": ",".join(map(str, range(1000, 1020))),
            "pipelineBlindSeeds": ",".join(map(str, range(2000, 2020)))}.items():
            page.locator("#" + name).fill(value)
        with page.expect_response(lambda response: "/api/statistics/run-pipeline" in response.url) as submission:
            page.locator("#runEngineeringPipeline").click()
        assert submission.value.status == 202, submission.value.text()
        pipeline_job = wait_job(page, submission.value.json()["job"]["id"])
        assert pipeline_job["status"] == "success", pipeline_job
        run_id = pipeline_job["result"]["run_id"]
        page.wait_for_function("id => document.getElementById('pipelineEvidence').textContent.includes(id)", arg=run_id)
        data = page.request.get(BASE + "/api/statistics/pipelines?run_id=" + run_id).json()
        assert data["audit"]["passed"] and data["manifest"]["replay"]["passed"]
        assert len(data["manifest"]["stages"]) == 6
        page.locator("#pipelineEvidence").screenshot(path=str(OUTPUT / "pipeline-desktop.png"))
        # Deliberately reuse recorded development input as blind input.
        for name, value in {"configSeeds": "0", "configDistributions": "uniform", "configProtocols": "shared_sketch"}.items():
            page.locator("#" + name).fill(value)
        page.locator("#configSplit").select_option("blind")
        with page.expect_response(lambda response: "/api/statistics/run-config" in response.url) as rejected:
            page.locator("#runConfigExperiment").click()
        rejected_job = wait_job(page, rejected.value.json()["job"]["id"])
        assert rejected_job["status"] == "failed", rejected_job
        failed_data = page.request.get(BASE + "/api/statistics/config-runs").json()
        assert failed_data["manifest"]["split_audit"]["overlap_count"] > 0
        assert all(not row["is_pareto"] and not row["pareto_eligible"] for row in failed_data["rows"])
        page.wait_for_function("id => document.getElementById('configRunEvidence').textContent.includes(id)", arg=failed_data["run_id"])
        assert page.locator("#rebuildConfigEvidence").is_disabled()
        page.locator("#configRunEvidence").screenshot(path=str(OUTPUT / "blind-overlap-rejected.png"))
        # Independent held-out inputs should still be runnable.
        page.locator("#configSeeds").fill("8000,8001")
        with page.expect_response(lambda response: "/api/statistics/run-config" in response.url) as accepted:
            page.locator("#runConfigExperiment").click()
        accepted_job = wait_job(page, accepted.value.json()["job"]["id"])
        assert accepted_job["status"] == "success", accepted_job
        independent = page.request.get(BASE + "/api/statistics/config-runs?run_id=" + accepted_job["result"]["run_id"]).json()
        assert independent["manifest"]["split_audit"]["passed"] and independent["audit"]["passed"]
        page.route("**/api/selfopt/candidates", lambda route: route.fulfill(json=fixture))
        page.locator('nav button[data-view="validate"]').click()
        page.wait_for_function("document.getElementById('candidateLineage').textContent.includes('Offline acceptance fixture')")
        candidate = page.locator("#candidateLineage > details").first
        candidate.evaluate("element => element.open = true")
        assert candidate.inner_text().count("尝试") >= 2
        assert "验证诊断" in candidate.inner_text()
        first_artifact = candidate.locator('a[href*="/api/selfopt/download"]').first.get_attribute("href")
        assert page.request.get(BASE + first_artifact).status == 200
        candidate.screenshot(path=str(OUTPUT / "offline-repair-trace.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("switchView('statistics')")
        page.locator("#pipelineEvidence").scroll_into_view_if_needed()
        page.screenshot(path=str(OUTPUT / "pipeline-mobile.png"))
        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        browser.close()
    report = {"page_errors": errors, "mobile_horizontal_overflow": overflow, "pipeline_run_id": run_id,
        "pipeline_audit": data["audit"], "stage_counts": {s["name"]: s["run_count"] for s in data["manifest"]["stages"]},
        "blind_replay": data["manifest"]["replay"], "blind_overlap_rejected": failed_data["manifest"]["split_audit"],
        "failed_run_id": failed_data["run_id"], "failed_rows_excluded_from_pareto": True,
        "independent_blind_run_id": independent["run_id"], "independent_blind_passed": True,
        "offline_workflow_nodes": len(fixture["nodes"]), "offline_model_calls": "mocked; no gateway",
        "formal_candidate_linked": bool(expected_candidates), "formal_candidate_check": formal_check}
    (OUTPUT / "browser_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"blind_replay", "blind_overlap_rejected"}}, indent=2))
    assert not errors, errors
    assert not overflow, "mobile viewport overflow"


if __name__ == "__main__":
    main()
