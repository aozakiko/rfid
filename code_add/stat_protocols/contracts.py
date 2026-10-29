"""Common interfaces for statistical protocols and reproducible experiments.

The dataclasses in this module are the boundary between protocol code and the
benchmark/evolution layers.  They deliberately contain communication counters
instead of treating Python wall-clock time as RFID latency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping


@dataclass(frozen=True)
class CostModel:
    """Convert communication events into a transparent simulation cost."""

    reader_bit_ms: float = 0.025
    tag_bit_ms: float = 0.4
    slot_ms: float = 0.0
    frame_ms: float = 30.0
    round_ms: float = 0.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v < 0 for v in self.to_dict().values()):
            raise ValueError("cost coefficients must be finite and non-negative")

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
    extreme_rate: float = 0.0
    seed: int = 0
    relative_tolerance: float = 0.05
    split: str = "evaluation"

    def __post_init__(self):
        if any(type(v) is not int for v in (self.num_tags, self.price_min, self.price_max, self.top_k, self.seed)):
            raise ValueError("population, domain, top_k and seed must be integers")
        if not self.tasks or any(t not in {"mean", "variance", "cardinality", "top_k"} for t in self.tasks):
            raise ValueError("unsupported or empty task list")
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("duplicate tasks")
        if not 0 <= self.num_tags <= 200000 or self.price_min > self.price_max:
            raise ValueError("invalid population or value bounds")
        if self.price_max - self.price_min + 1 > 10000:
            raise ValueError("value domain exceeds 10000 bins")
        if self.price_min < 0 or self.price_max >= 2 ** 31:
            raise ValueError("value domain must fit non-negative signed 32-bit fields")
        if not 1 <= self.top_k <= self.price_max - self.price_min + 1:
            raise ValueError("invalid top_k")
        if self.seed < 0 or not 0 <= self.relative_tolerance <= 1:
            raise ValueError("invalid seed or tolerance")
        if any(not 0 <= v <= 1 for v in (self.missing_rate, self.noise_rate, self.extreme_rate)):
            raise ValueError("rates must be in [0, 1]")
        if self.distribution not in {"single_peak", "multi_peak", "gaussian", "long_tail", "power_law", "uniform"}:
            raise ValueError("unsupported distribution")

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

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.reader_bits, self.tag_bits, self.slots, self.frames, self.rounds, self.seed)):
            raise ValueError("communication counters and seed must be non-negative integers")
        if not math.isfinite(self.cpu_time_ms) or self.cpu_time_ms < 0:
            raise ValueError("cpu_time_ms must be finite and non-negative")

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
