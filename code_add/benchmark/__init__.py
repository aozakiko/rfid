"""Reproducible communication/error benchmark for RFID statistical protocols."""

from .runner import evaluate_run, run_benchmark, summarize_reports
from .scenarios import generate_tags

__all__ = ["evaluate_run", "run_benchmark", "summarize_reports", "generate_tags"]
