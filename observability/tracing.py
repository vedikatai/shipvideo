import contextvars
from contextlib import contextmanager
from typing import List, Optional, Tuple

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

_TRACER_PROVIDER: Optional[TracerProvider] = None
_TRACER: Optional[trace.Tracer] = None

SERVICE_NAME = "shipvideo-engine"


class _NoOpSpanExporter(SpanExporter):

    def export(self, spans):
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000):
        return True



_step_timings: contextvars.ContextVar[Optional[List[Tuple[str, float]]]] = contextvars.ContextVar(
    "pipeline_step_timings", default=None
)


def _get_timings_list() -> Optional[List[Tuple[str, float]]]:
    return _step_timings.get()


def clear_pipeline_timings() -> None:
    lst = _step_timings.get()
    if lst is not None:
        lst.clear()


def record_step_timing(step_name: str, duration_ms: float) -> None:
    lst = _step_timings.get()
    if lst is not None:
        lst.append((step_name, duration_ms))


def _print_pipeline_summary() -> None:
    lst = _step_timings.get()
    if not lst:
        return
    print("", flush=True)
    print("PIPELINE SUMMARY", flush=True)
    total_ms = 0.0



    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"


    SLOW_THRESHOLD_MS = 5000.0                      



    COMPOSITE_STEPS = {"analyze_pr", "video_pipeline"}

    for name, ms in lst:
        if name not in COMPOSITE_STEPS:
            total_ms += ms
        if ms >= SLOW_THRESHOLD_MS:
            color = RED
        else:
            color = GREEN

        if ms >= 1000:
            line = f"{name:<22} {ms / 1000:.1f} s"
        else:
            line = f"{name:<22} {ms:.1f} ms"
        print(f"{color}{line}{RESET}", flush=True)


    total_color = RED if total_ms >= SLOW_THRESHOLD_MS else GREEN
    if total_ms >= 1000:
        total_line = f"{'TOTAL':<22} {total_ms / 1000:.1f} s"
    else:
        total_line = f"{'TOTAL':<22} {total_ms:.1f} ms"
    print(f"{total_color}{total_line}{RESET}", flush=True)
    print("", flush=True)
    lst.clear()


def print_pipeline_summary() -> None:
    _print_pipeline_summary()


def init_tracing() -> None:
    global _TRACER_PROVIDER, _TRACER
    if _TRACER_PROVIDER is not None:
        return
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_NoOpSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    _TRACER = trace.get_tracer(SERVICE_NAME, "1.0.0")


def get_tracer() -> trace.Tracer:
    if _TRACER is None:
        init_tracing()
    assert _TRACER is not None
    return _TRACER


def set_current_span_error(message: str) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_status(Status(StatusCode.ERROR, message))


def record_contract_integrity_error(
    *,
    stage: str,
    reason: str,
    contract_id: str = "",
    missing_targets: Optional[List[str]] = None,
) -> None:
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attribute("contract_integrity.stage", stage[:256])
    span.set_attribute("contract_integrity.reason", reason[:512])
    if contract_id:
        span.set_attribute("contract_integrity.contract_id", contract_id[:64])
    if missing_targets:
        joined = ",".join(missing_targets)[:1024]
        span.set_attribute("contract_integrity.missing_targets", joined)


def record_agent_browser_diagnostics(
    *,
    console_count: int = 0,
    page_error_count: int = 0,
    network_request_count: int = 0,
    network_error_count: int = 0,
) -> None:
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attribute("agent_browser.console_count", int(console_count))
    span.set_attribute("agent_browser.page_error_count", int(page_error_count))
    span.set_attribute("agent_browser.network_request_count", int(network_request_count))
    span.set_attribute("agent_browser.network_error_count", int(network_error_count))


@contextmanager
def pipeline_run_span():
    _step_timings.set([])
    tracer = get_tracer()
    try:
        with tracer.start_as_current_span("pipeline_run") as span:
            yield span
    finally:
        _print_pipeline_summary()
        _step_timings.set(None)
