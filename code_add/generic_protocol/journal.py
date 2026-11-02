"""Append-only attempt evidence for the existing generation/repair workflow."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


class AttemptJournal:
    def __init__(self, scene_root, protocol_name, *, parents, model, task_profile, generation):
        self.root = Path(scene_root) / "outputs" / "protocol_traces" / protocol_name / uuid4().hex
        self.root.mkdir(parents=True)
        self.data = {"schema_version": "1.0", "workflow_id": self.root.name,
            "protocol": protocol_name, "parents": list(parents), "model": model, "task_profile": task_profile,
            "generation": generation, "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "running", "attempts": []}
        self.flush()

    def flush(self):
        write_json(self.root / "workflow.json", self.data)

    def begin(self, index, system_prompt, prompt):
        folder = self.root / f"attempt_{index:03d}"
        folder.mkdir()
        (folder / "system_prompt.txt").write_bytes(system_prompt.encode("utf-8"))
        (folder / "prompt.txt").write_bytes(prompt.encode("utf-8"))
        item = {"attempt": index, "folder": folder.name, "status": "requesting", "artifact_hashes": {}}
        self.data["attempts"].append(item)
        self.seal(folder)
        return folder

    def response(self, folder, response):
        (folder / "response.txt").write_bytes(response.encode("utf-8"))
        self.data["attempts"][-1]["status"] = "validating"
        self.seal(folder)

    def finish_attempt(self, folder, *, code, validation, candidate):
        (folder / "protocol.py").write_bytes(code.encode("utf-8"))
        write_json(folder / "validation.json", validation)
        write_json(folder / "candidate.json", candidate.to_dict())
        write_json(folder / "evaluation.json", candidate.benchmark_result)
        self.data["attempts"][-1].update(status=candidate.status, candidate_id=candidate.candidate_id,
            final_decision=validation.get("final_decision"), source_sha256=validation.get("source_sha256"))
        self.seal(folder)

    def seal(self, folder):
        self.data["attempts"][-1]["artifact_hashes"] = {p.name: digest(p.read_bytes()) for p in sorted(folder.iterdir()) if p.is_file()}
        self.flush()

    def finish(self, status, *, error_type=None):
        self.data.update(status=status, finished_at=datetime.now(timezone.utc).isoformat())
        if error_type:
            self.data["error_type"] = error_type
            self.data["attempts"][-1]["status"] = "error"
        self.flush()


def audit_journal(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    for attempt in data.get("attempts", []):
        folder = path.parent / attempt["folder"]
        if path.parent.resolve() not in folder.resolve().parents:
            errors.append("invalid_attempt_path")
            continue
        expected = attempt.get("artifact_hashes", {})
        actual = {p.name: digest(p.read_bytes()) for p in folder.glob("*") if p.is_file()}
        if actual != expected:
            errors.append(f"attempt_{attempt['attempt']}_artifact_mismatch")
    return {"passed": not errors and bool(data.get("attempts")), "errors": errors, "attempt_count": len(data.get("attempts", []))}
