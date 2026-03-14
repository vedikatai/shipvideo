"""
Lightweight observability for the pipeline: tracing with readable step logs (no JSON export).
Use init_tracing(), get_tracer(), pipeline_run_span(), pipeline_step, set_current_span_error, print_pipeline_summary; do not import OpenTelemetry from business logic.
"""
from observability.tracing import (
    init_tracing,
    get_tracer,
    set_current_span_error,
    pipeline_run_span,
    print_pipeline_summary,
)
from observability.decorators import pipeline_step

__all__ = [
    "init_tracing",
    "get_tracer",
    "set_current_span_error",
    "pipeline_run_span",
    "print_pipeline_summary",
    "pipeline_step",
]
