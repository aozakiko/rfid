"""Compatibility CLI: sensitivity experiments now use the shared sandbox."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from course_project.sensitivity import (
    VALID_FACTORS, discover_valid_sources, generate_tags_single_peak,
    generate_tags_for_dominance_test, load_protocol, parse_protocol_specs,
    parse_factors, run_sensitivity_test_for_protocol, main,
)

if __name__ == "__main__":
    raise SystemExit(main())
