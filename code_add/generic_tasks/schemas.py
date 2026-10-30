"""Serializable contracts for generic RFID task profiles."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


SEMANTIC_STATISTICAL = "statistical_metric"
SEMANTIC_LEGACY = "legacy_truth"
SEMANTIC_EXECUTION = "execution_and_output_contract"

SEMANTIC_SCOPES = (
    SEMANTIC_STATISTICAL,
    SEMANTIC_LEGACY,
    SEMANTIC_EXECUTION,
)

_PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_FIELDS = (
    "profile_id",
    "title",
    "description",
    "semantic_scope",
    "input_kind",
    "output_kind",
    "truth_builder",
    "metric_kind",
    "canonical_tasks",
    "supported_distributions",
    "supported_protocol_types",
    "prompt_guidance",
    "validation_rules",
)

_FORBIDDEN_CUSTOM_METRICS = {
    "accuracy",
    "accuracy_metric",
    "semantic_accuracy",
}


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{field_name} must be a sequence of strings")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence of strings") from exc
    if not items:
        raise ValueError(f"{field_name} must not be empty")
    normalized = tuple(_required_text(item, field_name) for item in items)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    result = {
        _required_text(key, f"{field_name} key"): _required_text(item, field_name)
        for key, item in value.items()
    }
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


@dataclass(frozen=True)
class TaskProfile:
    """Immutable declaration of one task's semantics and contracts."""

    profile_id: str
    title: str
    description: str
    semantic_scope: str
    input_kind: str
    output_kind: str
    truth_builder: str
    metric_kind: str
    canonical_tasks: tuple[str, ...]
    supported_distributions: tuple[str, ...]
    supported_protocol_types: tuple[str, ...]
    prompt_guidance: Mapping[str, str]
    validation_rules: tuple[str, ...]

    def __post_init__(self) -> None:
        profile_id = _required_text(self.profile_id, "profile_id")
        if not _PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise ValueError(f"invalid profile_id: {profile_id}")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(self, "description", _required_text(self.description, "description"))
        object.__setattr__(self, "input_kind", _required_text(self.input_kind, "input_kind"))
        object.__setattr__(self, "output_kind", _required_text(self.output_kind, "output_kind"))
        object.__setattr__(self, "truth_builder", _required_text(self.truth_builder, "truth_builder"))
        object.__setattr__(self, "metric_kind", _required_text(self.metric_kind, "metric_kind"))

        semantic_scope = _required_text(self.semantic_scope, "semantic_scope")
        if semantic_scope not in SEMANTIC_SCOPES:
            raise ValueError(f"unsupported semantic_scope: {semantic_scope}")
        object.__setattr__(self, "semantic_scope", semantic_scope)

        object.__setattr__(self, "canonical_tasks", _string_tuple(self.canonical_tasks, "canonical_tasks"))
        object.__setattr__(
            self,
            "supported_distributions",
            _string_tuple(self.supported_distributions, "supported_distributions"),
        )
        object.__setattr__(
            self,
            "supported_protocol_types",
            _string_tuple(self.supported_protocol_types, "supported_protocol_types"),
        )
        object.__setattr__(
            self,
            "validation_rules",
            _string_tuple(self.validation_rules, "validation_rules"),
        )
        object.__setattr__(self, "prompt_guidance", _string_mapping(self.prompt_guidance, "prompt_guidance"))

        if semantic_scope == SEMANTIC_EXECUTION and self.metric_kind.lower() in _FORBIDDEN_CUSTOM_METRICS:
            raise ValueError("execution-only custom tasks must not declare an accuracy metric")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "title": self.title,
            "description": self.description,
            "semantic_scope": self.semantic_scope,
            "input_kind": self.input_kind,
            "output_kind": self.output_kind,
            "truth_builder": self.truth_builder,
            "metric_kind": self.metric_kind,
            "canonical_tasks": list(self.canonical_tasks),
            "supported_distributions": list(self.supported_distributions),
            "supported_protocol_types": list(self.supported_protocol_types),
            "prompt_guidance": dict(self.prompt_guidance),
            "validation_rules": list(self.validation_rules),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskProfile":
        if not isinstance(value, Mapping):
            raise ValueError("profile data must be a mapping")
        missing = [field for field in _REQUIRED_FIELDS if field not in value]
        if missing:
            raise ValueError(f"missing profile fields: {', '.join(missing)}")
        data = dict(value)
        unknown = sorted(set(data) - set(_REQUIRED_FIELDS))
        if unknown:
            raise ValueError(f"unknown profile fields: {', '.join(unknown)}")
        return cls(**data)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskProfile":
        return cls.from_mapping(value)


def profile_to_dict(profile: TaskProfile) -> dict[str, Any]:
    if not isinstance(profile, TaskProfile):
        raise ValueError("profile must be a TaskProfile")
    return profile.to_dict()


def profile_from_dict(value: Mapping[str, Any]) -> TaskProfile:
    return TaskProfile.from_mapping(value)


def serialize_profile(profile: TaskProfile) -> str:
    return json.dumps(profile_to_dict(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_profile(value: str | Mapping[str, Any]) -> TaskProfile:
    data = json.loads(value) if isinstance(value, str) else value
    return profile_from_dict(data)
