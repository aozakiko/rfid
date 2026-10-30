"""Generic task profile registry and serializable contracts."""
from __future__ import annotations

from . import profiles as _profiles
from .profiles import BUILTIN_PROFILES
from .registry import (
    SEMANTIC_EXECUTION,
    SEMANTIC_LEGACY,
    SEMANTIC_STATISTICAL,
    TaskProfile,
    UnknownProfileError,
    get_profile,
    list_profiles,
    register_profile,
    resolve_canonical_tasks,
    validate_profile_id,
)
from .schemas import (
    deserialize_profile,
    profile_from_dict,
    profile_to_dict,
    serialize_profile,
)

__all__ = [
    "BUILTIN_PROFILES",
    "SEMANTIC_EXECUTION",
    "SEMANTIC_LEGACY",
    "SEMANTIC_STATISTICAL",
    "TaskProfile",
    "UnknownProfileError",
    "deserialize_profile",
    "get_profile",
    "list_profiles",
    "profile_from_dict",
    "profile_to_dict",
    "register_profile",
    "resolve_canonical_tasks",
    "serialize_profile",
    "validate_profile_id",
]
