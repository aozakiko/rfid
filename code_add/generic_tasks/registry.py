"""Registry API for generic RFID task profiles."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .schemas import (
    SEMANTIC_EXECUTION,
    SEMANTIC_LEGACY,
    SEMANTIC_STATISTICAL,
    TaskProfile,
)

__all__ = [
    "SEMANTIC_STATISTICAL",
    "SEMANTIC_LEGACY",
    "SEMANTIC_EXECUTION",
    "TaskProfile",
    "UnknownProfileError",
    "get_profile",
    "list_profiles",
    "register_profile",
    "resolve_canonical_tasks",
    "validate_profile_id",
]

_REGISTRY: dict[str, TaskProfile] = {}


class UnknownProfileError(KeyError, ValueError):
    """An unknown profile is both an invalid input and a registry miss."""


def _normalize_profile_id(profile_id: Any) -> str:
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError("profile_id must be a non-empty string")
    return profile_id.strip()


def validate_profile_id(profile_id: Any) -> str:
    """Return a registered profile ID or raise a stable unknown-profile error."""
    normalized = _normalize_profile_id(profile_id)
    if normalized not in _REGISTRY:
        raise UnknownProfileError(f"unknown task profile: {normalized}")
    return normalized


def register_profile(profile: TaskProfile, *, replace: bool = False) -> TaskProfile:
    """Register one profile while preventing accidental name collisions."""
    if not isinstance(profile, TaskProfile):
        raise ValueError("profile must be a TaskProfile")
    profile_id = profile.profile_id
    if profile_id in _REGISTRY and not replace:
        raise ValueError(f"task profile already registered: {profile_id}")
    _REGISTRY[profile_id] = profile
    return profile


def get_profile(profile_id: Any) -> TaskProfile:
    normalized = validate_profile_id(profile_id)
    return _REGISTRY[normalized]


def list_profiles() -> list[TaskProfile]:
    return list(_REGISTRY.values())


def resolve_canonical_tasks(profile_ids: str | Iterable[str]) -> list[str]:
    """Resolve one or more profile IDs into a stable, de-duplicated task list."""
    if isinstance(profile_ids, str):
        requested = (profile_ids,)
    else:
        try:
            requested = tuple(profile_ids)
        except TypeError as exc:
            raise ValueError("profile_ids must be a string or iterable of strings") from exc

    resolved: list[str] = []
    for profile_id in requested:
        for task in get_profile(profile_id).canonical_tasks:
            if task not in resolved:
                resolved.append(task)
    return resolved
