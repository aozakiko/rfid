"""Adapter for the existing ``stat_protocols`` object contract."""
from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from stat_protocols.contracts import RunResult

from .base import ProtocolAdapter, requested_tasks, unsupported_results


class StatisticsProtocolAdapter(ProtocolAdapter):
    """Expose a statistical protocol through the generic ``run_many`` API.

    The delegate is called without changing its result rows, so communication
    counters, metadata, truth and estimates remain identical to the original
    ``stat_protocols`` implementation.
    """

    def __init__(self, protocol: Any, name: str | None = None, supports: Any = None):
        self.protocol = protocol
        self.name = str(name or getattr(protocol, "name", None) or type(protocol).__name__)
        if supports is None:
            supports = getattr(protocol, "tasks", ())
        self.supports = frozenset(str(task) for task in supports)

    @staticmethod
    def _call_delegate(delegate: Any, tags: Any, spec: Any, context: Any) -> Mapping[str, Any]:
        run_many = getattr(delegate, "run_many", None)
        if not callable(run_many):
            raise TypeError("statistics protocol must expose run_many(tags, spec, ...)")
        if context is None:
            output = run_many(tags, spec)
        else:
            kwargs: dict[str, Any] = {}
            try:
                parameters = inspect.signature(run_many).parameters
            except (TypeError, ValueError):
                parameters = {}
            accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
            if isinstance(context, Mapping):
                if "channel" in context and ("channel" in parameters or accepts_kwargs):
                    kwargs["channel"] = context["channel"]
                elif "context" in parameters:
                    kwargs["context"] = context
                elif "channel" in parameters:
                    kwargs["channel"] = context
                else:
                    output = run_many(tags, spec)
                    return _require_mapping(output)
            elif "context" in parameters:
                kwargs["context"] = context
            elif "channel" in parameters or accepts_kwargs:
                kwargs["channel"] = context
            else:
                output = run_many(tags, spec)
                return _require_mapping(output)
            output = run_many(tags, spec, **kwargs)
        return _require_mapping(output)

    def run_many(self, tags: Any, spec: Any, context: Any = None) -> dict[str, RunResult]:
        tasks = requested_tasks(spec, default_task="mean")
        supported = [task for task in tasks if task in self.supports]
        missing = [task for task in tasks if task not in self.supports]
        results: dict[str, RunResult] = {}
        if supported:
            try:
                output = self._call_delegate(self.protocol, tags, spec, context)
            except Exception as exc:
                results.update(
                    unsupported_results(
                        supported,
                        protocol_name=self.name,
                        spec=spec,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                for task in supported:
                    if task not in output:
                        results[task] = unsupported_results(
                            (task,), protocol_name=self.name, spec=spec, reason="missing_result"
                        )[task]
                        continue
                    result = output[task]
                    if not isinstance(result, RunResult):
                        raise TypeError(f"statistics result for {task!r} must be RunResult")
                    results[task] = result
        results.update(
            unsupported_results(missing, protocol_name=self.name, spec=spec, reason="unsupported_task")
        )
        return {task: results[task] for task in tasks}


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("statistics protocol run_many must return a mapping")
    return value
