"""Reproducibility and artifact-integrity helpers for benchmark outputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


EXCLUDED_ARTIFACTS = frozenset({"manifest.json"})


def source_hashes(root: str | Path) -> dict[str, str]:
    root = Path(root).resolve()
    paths = {root / name for name in ("base.py", "hash.py", "Validator.py", "ai_protocol_optimizer.py",
        "backend_tools/protocol_metrics.py", "course_project/web_app.py", "course_project/statistics_service.py",
        "course_project/engineering_service.py", "course_project/candidate_evaluation.py")}
    for folder in ("stat_protocols", "benchmark", "generic_protocol", "generic_tasks"):
        paths.update((root / folder).glob("*.py"))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}


def artifact_hashes(root: str | Path) -> dict[str, str]:
    """Return SHA-256 hashes for published files excluding self-referential files."""
    root = Path(root).resolve()
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in EXCLUDED_ARTIFACTS:
            continue
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def reproducibility_key(manifest: dict[str, Any]) -> str:
    """Build a stable identity from experimental dimensions and source provenance."""
    fields = {
        key: manifest.get(key)
        for key in (
            "schema_version",
            "seeds",
            "blind_seeds",
            "sizes",
            "distributions",
            "cost_model",
            "source_hashes",
            "llm_status",
            "llm_record",
            "config",
            "task_specs",
            "protocols",
            "python_version",
            "package_versions",
            "reference_artifact_hashes",
        )
        if key in manifest
    }
    if "reference_artifact_hashes" in fields:
        # Reference locations are operational metadata, not experiment identity.
        fields["reference_artifact_hashes"] = sorted(set(fields["reference_artifact_hashes"].values()))
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_manifest(root: str | Path) -> dict[str, Any]:
    """Compare recorded artifact hashes and reproducibility key with disk."""
    root = Path(root).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"passed": False, "root": str(root), "error": "manifest.json not found"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "root": str(root), "error": f"invalid manifest: {exc}"}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifact_hashes", {}), dict):
        return {"passed": False, "status": "failed", "root": str(root), "error": "invalid manifest schema"}
    expected = manifest.get("artifact_hashes") or {}
    actual = artifact_hashes(root)
    if not expected and not manifest.get("reproducibility_key"):
        return {
            "passed": False,
            "status": "not_available",
            "root": str(root),
            "artifact_count": len(actual),
            "missing": [],
            "unexpected": [],
            "mismatched": [],
            "reproducibility_key_match": False,
            "expected_reproducibility_key": None,
            "actual_reproducibility_key": reproducibility_key(manifest),
            "error": "manifest 未记录 artifact_hashes/reproducibility_key；需要重新运行 benchmark.suite",
        }
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    mismatched = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
    expected_key = manifest.get("reproducibility_key")
    actual_key = reproducibility_key(manifest)
    key_match = bool(expected_key) and expected_key == actual_key
    return {
        "passed": not missing and not unexpected and not mismatched and key_match,
        "status": "passed" if not missing and not unexpected and not mismatched and key_match else "failed",
        "root": str(root),
        "artifact_count": len(actual),
        "missing": missing,
        "unexpected": unexpected,
        "mismatched": mismatched,
        "reproducibility_key_match": key_match,
        "expected_reproducibility_key": expected_key,
        "actual_reproducibility_key": actual_key,
    }
