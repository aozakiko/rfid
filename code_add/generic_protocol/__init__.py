"""Generic protocol adapters and isolated protocol-file execution."""
from .base import GenericProtocolAdapter, ProtocolAdapter
from .candidate import CandidateRecord, candidate_from_mapping
from .legacy_adapter import LegacyProtocolAdapter
from .statistics_adapter import StatisticsProtocolAdapter
from .security import InformationIsolationViolation, TagIsolationProxy, analyze_file, analyze_source
from .subprocess_runner import run_protocol_file

__all__ = [
    "CandidateRecord",
    "GenericProtocolAdapter",
    "LegacyProtocolAdapter",
    "ProtocolAdapter",
    "StatisticsProtocolAdapter",
    "InformationIsolationViolation",
    "TagIsolationProxy",
    "analyze_file",
    "analyze_source",
    "candidate_from_mapping",
    "run_protocol_file",
]
