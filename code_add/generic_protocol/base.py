"""Shared contracts and small utilities for protocol adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Mapping
from typing import Any, Iterable


STANDARD_TASKS = ("mean", "variance", "cardinality", "top_k")


def get_spec_value(spec: Any, name: str, default: Any = None) -> Any:
    """Read a task-spec field from either an object or a mapping."""
    if isinstance(spec, Mapping):
        return spec.get(name, default)
    return getattr(spec, name, default)


def requested_tasks(spec: Any, *, default_task: str = "legacy_mode_query", explicit_wins: bool = False) -> tuple[str, ...]:
    """Return the task names an adapter should emit.

    Legacy protocols have one mode-query result even when a caller keeps the
    standard TaskSpec ``tasks`` tuple. In that case an explicit ``task`` field
    wins, while statistical adapters continue to use ``spec.tasks``.
    """
    explicit = get_spec_value(spec, "task")
    tasks = get_spec_value(spec, "tasks")
    if isinstance(tasks, str):
        tasks = (tasks,)
    if tasks is not None:
        tasks = tuple(str(task) for task in tasks)
    else:
        tasks = ()
    if explicit_wins and explicit:
        explicit = str(explicit)
        if explicit not in tasks:
            return (explicit,)
    return tasks or ((str(explicit),) if explicit else (default_task,))


def nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def nonnegative_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")) or parsed < 0:
        return default
    return parsed


def _tag_value(tag: Any, name: str, default: Any = None) -> Any:
    if isinstance(tag, Mapping):
        return tag.get(name, default)
    return getattr(tag, name, default)


def mode_truth(tags: Iterable[Any]) -> list[Any]:
    """Return the exact mode set, ordered by value for deterministic output."""
    values = [_tag_value(tag, "price") for tag in tags]
    values = [value for value in values if value is not None]
    if not values:
        return []
    counts = Counter(values)
    top = max(counts.values())
    modes = [value for value, count in counts.items() if count == top]
    try:
        return sorted(modes)
    except TypeError:
        return sorted(modes, key=lambda value: str(value))


def truth_for_task(tags: Iterable[Any], task: str, spec: Any = None) -> Any:
    tags = list(tags)
    values = [_tag_value(tag, "price") for tag in tags]
    values = [value for value in values if value is not None]
    if task in {"legacy_mode_query", "mode_query"} or task.endswith("_mode_query"):
        return mode_truth(tags)
    if task == "top_k":
        top_k = nonnegative_int(get_spec_value(spec, "top_k", 1), 1)
        counts = Counter(values)
        return [value for value, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[: max(1, top_k)]]
    if not values:
        return [] if task == "top_k" else 0.0
    if task == "mean":
        return sum(values) / len(values)
    if task == "variance":
        mean = sum(values) / len(values)
        return sum((value - mean) ** 2 for value in values) / len(values)
    if task == "cardinality":
        return len(values)
    return None


def unsupported_results(
    tasks: Iterable[str],
    *,
    protocol_name: str,
    spec: Any,
    reason: str = "unsupported_task",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build complete RunResult rows for unsupported or failed tasks."""
    from stat_protocols.contracts import RunResult

    seed = nonnegative_int(get_spec_value(spec, "seed", 0))
    base_metadata = {"cost_status": "missing"}
    if metadata:
        base_metadata.update(metadata)
    return {
        str(task): RunResult(
            protocol=str(protocol_name),
            task=str(task),
            truth=None,
            estimate=None,
            reader_bits=0,
            tag_bits=0,
            slots=0,
            frames=0,
            rounds=0,
            seed=seed,
            metadata=dict(base_metadata),
            failure_reason=reason,
        )
        for task in tasks
    }


class ProtocolAdapter(ABC):
    """Uniform adapter surface: ``run_many(tags, spec, context=None)``."""

    name: str = "protocol"
    supports: frozenset[str] = frozenset()

    @abstractmethod
    def run_many(self, tags: Any, spec: Any, context: Any = None) -> dict[str, Any]:
        raise NotImplementedError


# A descriptive alias for callers that prefer the longer name.
GenericProtocolAdapter = ProtocolAdapter
