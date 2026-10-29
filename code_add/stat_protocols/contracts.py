"""Common interfaces for statistical protocols and reproducible experiments.

The dataclasses in this module are the boundary between protocol code and the
benchmark/evolution layers.  They deliberately contain communication counters
instead of treating Python wall-clock time as RFID latency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class CostModel:
    """Convert communication events into a transparent simulation cost."""

    reader_bit_ms: float = 0.025
    tag_bit_ms: float = 0.4
    slot_ms: float = 0.0
    frame_ms: float = 30.0
    round_ms: float = 0.0

    def estimate_ms(self, *, reader_bits: int, tag_bits: int, slots: int, frames: int, rounds: int) -> float:
        return round(
            reader_bits * self.reader_bit_ms
            + tag_bits * self.tag_bit_ms
            + slots * self.slot_ms
            + frames * self.frame_ms
            + rounds * self.round_ms,
            6,
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSpec:
    """Frozen experiment input: task, distribution, scale and reproducibility."""

    task: str = "mean"
    tasks: tuple[str, ...] = ("mean", "variance", "cardinality", "top_k")
    distribution: str = "multi_peak"
    num_tags: int = 1_000
    price_min: int = 1
    price_max: int = 1_000
    top_k: int = 3
    missing_rate: float = 0.0
    noise_rate: float = 0.0
    seed: int = 0
    relative_tolerance: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tasks"] = list(self.tasks)
        return data

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSpec":
        data = dict(value)
        if "tasks" in data and not isinstance(data["tasks"], tuple):
            data["tasks"] = tuple(data["tasks"])
        return cls(**data)


@dataclass(frozen=True)
class ProtocolCandidate:
    candidate_id: str
    protocol_name: str
    parents: tuple[str, ...] = ()
    genotype: Mapping[str, Any] = field(default_factory=dict)
    prompt_hash: str = ""
    model_version: str = ""
    source_hash: str = ""
    status: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["parents"] = list(self.parents)
        return data


@dataclass(frozen=True)
class RunResult:
    """One protocol/task execution under one exact seed and distribution."""

    protocol: str
    task: str
    truth: Any
    estimate: Any
    reader_bits: int
    tag_bits: int
    slots: int
    frames: int
    rounds: int
    seed: int
    cpu_time_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    failure_reason: str = ""

    @property
    def communication_bits(self) -> int:
        return int(self.reader_bits) + int(self.tag_bits)

    def to_dict(self, cost_model: CostModel | None = None) -> dict[str, Any]:
        data = asdict(self)
        data["communication_bits"] = self.communication_bits
        data["metadata"] = dict(self.metadata)
        if cost_model is not None:
            data["simulated_cost_ms"] = cost_model.estimate_ms(
                reader_bits=self.reader_bits,
                tag_bits=self.tag_bits,
                slots=self.slots,
                frames=self.frames,
                rounds=self.rounds,
            )
        return data


@dataclass(frozen=True)
class EvaluationReport:
    protocol: str
    task: str
    seed: int
    status: str
    absolute_error: float | None = None
    relative_error: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    jaccard: float | None = None
    communication_bits: int = 0
    simulated_cost_ms: float = 0.0
    cpu_time_ms: float = 0.0
    failure_reason: str = ""
    run_result: RunResult | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.run_result is not None:
            data["run_result"] = self.run_result.to_dict()
        return data
