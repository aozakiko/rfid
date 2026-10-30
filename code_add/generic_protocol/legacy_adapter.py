"""Adapter for legacy ``IProtocol.execute`` implementations."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stat_protocols.contracts import RunResult

from .base import (
    ProtocolAdapter,
    get_spec_value,
    nonnegative_float,
    nonnegative_int,
    requested_tasks,
    truth_for_task,
    unsupported_results,
)


_COUNTER_FIELDS = {
    "reader_bits": ("reader_bits", "reader_transmission_bits", "reader_tx_bits"),
    "tag_bits": ("tag_bits", "tag_transmission_bits", "tag_tx_bits"),
    "slots": ("slots", "slot_count", "total_slots"),
    "frames": ("frames", "frame_count", "total_frames"),
    "rounds": ("rounds", "round_count", "total_rounds"),
}


class LegacyProtocolAdapter(ProtocolAdapter):
    """Map ``execute(tags, price_min, price_max)`` to a generic RunResult.

    ``estimated_values`` becomes the estimate, ``metric`` and
    ``verification_data`` are retained in metadata, and ``execution_time`` is
    reported as ``cpu_time_ms``.  A legacy result without explicit
    communication counters is marked ``cost_status='missing'`` and therefore
    cannot be treated as a measured communication-cost row.
    """

    def __init__(
        self,
        protocol: Any,
        name: str | None = None,
        protocol_name: str | None = None,
        supports: Any = None,
    ):
        self.protocol = protocol
        self.name = str(
            name
            or protocol_name
            or getattr(protocol, "name", None)
            or type(protocol).__name__
        )
        self.supports = frozenset(str(task) for task in (supports or ("legacy_mode_query",)))

    def run_many(self, tags: Any, spec: Any, context: Any = None) -> dict[str, RunResult]:
        tasks = requested_tasks(spec, default_task="legacy_mode_query", explicit_wins=True)
        results: dict[str, RunResult] = {}
        raw_payload: Any = None
        execute_error: Exception | None = None
        for task in tasks:
            if task not in self.supports:
                results[task] = unsupported_results(
                    (task,), protocol_name=self.name, spec=spec, reason="unsupported_task",
                    metadata={"metric_kind": "set"},
                )[task]
                continue
            if raw_payload is None and execute_error is None:
                try:
                    raw_payload = self._execute(tags, spec)
                except Exception as exc:
                    execute_error = exc
            if execute_error is not None:
                results[task] = unsupported_results(
                    (task,),
                    protocol_name=self.name,
                    spec=spec,
                    reason=f"{type(execute_error).__name__}: {execute_error}",
                    metadata={"metric_kind": "set"},
                )[task]
                continue
            results[task] = self._to_result(task, tags, spec, raw_payload)
        return {task: results[task] for task in tasks}

    def _execute(self, tags: Any, spec: Any) -> Any:
        execute = getattr(self.protocol, "execute", None)
        if not callable(execute):
            raise TypeError("legacy protocol must expose execute(tags, price_min, price_max)")
        return execute(tags, get_spec_value(spec, "price_min", 0), get_spec_value(spec, "price_max", 0))

    @staticmethod
    def _unpack_payload(payload: Any) -> tuple[Any, Any, Any, Any]:
        if isinstance(payload, Mapping):
            return (
                payload.get("estimated_values", payload.get("estimate")),
                payload.get("metric"),
                payload.get("execution_time", payload.get("execution_time_ms", payload.get("cpu_time_ms"))),
                payload.get("verification_data", {}),
            )
        if isinstance(payload, (tuple, list)) and len(payload) == 4:
            return payload
        raise ValueError("legacy execute must return a 4-item tuple or mapping")

    @staticmethod
    def _estimate(raw: Any) -> Any:
        if isinstance(raw, (list, tuple, set)):
            return list(raw)
        return [raw]

    @staticmethod
    def _verification(raw: Any) -> dict[str, Any]:
        if isinstance(raw, Mapping):
            return dict(raw)
        return {"value": raw}

    @classmethod
    def _counters(cls, verification: Mapping[str, Any]) -> dict[str, int]:
        sources: list[Mapping[str, Any]] = [verification]
        for key in ("communication", "communication_counters", "cost"):
            nested = verification.get(key)
            if isinstance(nested, Mapping):
                sources.append(nested)
        counters = {}
        for canonical, aliases in _COUNTER_FIELDS.items():
            value = 0
            for source in sources:
                for alias in aliases:
                    if alias in source:
                        try:
                            candidate = int(source[alias])
                        except (TypeError, ValueError):
                            continue
                        if candidate >= 0:
                            value = max(value, candidate)
                            break
            counters[canonical] = value
        return counters

    @classmethod
    def _cost_status(cls, verification: Mapping[str, Any], counters: Mapping[str, int]) -> str:
        events = verification.get("communication_events", verification.get("events"))
        has_events = bool(events) if isinstance(events, (list, tuple, set, Mapping)) else False
        has_counters = any(value > 0 for value in counters.values())
        return "measured" if has_counters else "missing"

    def _to_result(self, task: str, tags: Any, spec: Any, payload: Any) -> RunResult:
        estimated_values, metric, execution_time, raw_verification = self._unpack_payload(payload)
        verification = self._verification(raw_verification)
        counters = self._counters(verification)
        cost_status = self._cost_status(verification, counters)
        metadata: dict[str, Any] = {
            "metric": metric,
            "verification_data": dict(verification),
            "metric_kind": "set",
            "cost_status": cost_status,
        }
        metadata.update(verification)
        if "communication_events" in verification:
            metadata["communication_events_present"] = bool(verification["communication_events"])
        return RunResult(
            protocol=self.name,
            task=task,
            truth=truth_for_task(tags, task, spec),
            estimate=self._estimate(estimated_values),
            reader_bits=nonnegative_int(counters["reader_bits"]),
            tag_bits=nonnegative_int(counters["tag_bits"]),
            slots=nonnegative_int(counters["slots"]),
            frames=nonnegative_int(counters["frames"]),
            rounds=nonnegative_int(counters["rounds"]),
            seed=nonnegative_int(get_spec_value(spec, "seed", 0)),
            cpu_time_ms=nonnegative_float(execution_time),
            metadata=metadata,
        )

