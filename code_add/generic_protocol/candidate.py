from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping


CANDIDATE_TYPES = {"legacy_python", "stat_operator_json", "imported_record"}
STATUS_VALUES = {"generated", "validated", "failed", "imported", "dry_run"}


@dataclass(frozen=True)
class CandidateRecord:
    """Machine-readable provenance for one generated/imported protocol candidate."""
    candidate_id: str
    protocol_name: str
    candidate_type: str
    parents: tuple[str, ...] = ()
    task_profile: str = ""
    genotype: Mapping[str, Any] = field(default_factory=dict)
    prompt_hash: str = ""
    response_hash: str = ""
    model: str = ""
    source_hash: str = ""
    validation_result: Mapping[str, Any] = field(default_factory=dict)
    benchmark_result: Mapping[str, Any] | None = None
    status: str = "generated"

    def __post_init__(self) -> None:
        for field_name in ("candidate_id", "protocol_name", "candidate_type", "source_hash"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.candidate_type not in CANDIDATE_TYPES:
            raise ValueError(f"unsupported candidate_type: {self.candidate_type}")
        if self.status not in STATUS_VALUES:
            raise ValueError(f"unsupported status: {self.status}")
        if not isinstance(self.parents, tuple):
            object.__setattr__(self, "parents", tuple(self.parents))
        if not isinstance(self.genotype, Mapping):
            raise ValueError("genotype must be a mapping")
        if self.benchmark_result is not None and not isinstance(self.benchmark_result, Mapping):
            raise ValueError("benchmark_result must be a mapping or None")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["parents"] = list(self.parents)
        data["genotype"] = dict(self.genotype)
        data["validation_result"] = dict(self.validation_result)
        if self.benchmark_result is not None:
            data["benchmark_result"] = dict(self.benchmark_result)
        return data

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target


def candidate_from_mapping(value: Mapping[str, Any]) -> CandidateRecord:
    if not isinstance(value, Mapping):
        raise ValueError("candidate record must be a mapping")
    data = dict(value)
    if "parents" in data:
        data["parents"] = tuple(data["parents"])
    return CandidateRecord(**data)
