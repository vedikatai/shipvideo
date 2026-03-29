"""
Lightweight observability for the pipeline: tracing with readable step logs (no JSON export).
Use init_tracing(), get_tracer(), pipeline_run_span(), pipeline_step, set_current_span_error,
record_contract_integrity_error, record_agent_browser_diagnostics, print_pipeline_summary; do not import OpenTelemetry directly.
"""
from observability.tracing import (
    init_tracing,
    get_tracer,
    set_current_span_error,
    record_contract_integrity_error,
    record_agent_browser_diagnostics,
    pipeline_run_span,
    print_pipeline_summary,
)
from observability.decorators import pipeline_step

__all__ = [
    "init_tracing",
    "get_tracer",
    "set_current_span_error",
    "record_contract_integrity_error",
    "record_agent_browser_diagnostics",
    "pipeline_run_span",
    "print_pipeline_summary",
    "pipeline_step",
]
