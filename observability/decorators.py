"""
Pipeline step decorator: creates a span, prints readable step logs, measures duration, sets error status on failure.
Business logic must not import OpenTelemetry directly; use @pipeline_step from this module.
"""
import asyncio
import functools
import time

from opentelemetry.trace import Status, StatusCode

from observability.tracing import get_tracer, record_step_timing

SLOW_STEP_MS = 5000

# Simple ANSI colors for step logs:
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _format_duration_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{round(ms):.0f} ms"
    if ms >= 1:
        return f"{ms:.1f} ms"
    return f"{ms:.3f} ms"


def pipeline_step(step_name: str):
    """
    Decorator that starts an OpenTelemetry span and prints human-readable step logs.
    - On start: ▶ step_name
    - On success: ✓ step_name (duration); if duration > 5000 ms also prints ⚠ SLOW STEP
    - On error: ✗ step_name FAILED (still records exception in span)
    - Records duration_ms on the span and in pipeline summary.
    Supports both sync and async functions.
    """

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                print(f"{GREEN}▶ {step_name}{RESET}", flush=True)
                with tracer.start_as_current_span(step_name) as span:
                    start = time.time()
                    try:
                        result = await fn(*args, **kwargs)
                        duration_ms = (time.time() - start) * 1000
                        span.set_attribute("duration_ms", round(duration_ms, 2))
                        record_step_timing(step_name, duration_ms)
                        color = RED if duration_ms > SLOW_STEP_MS else GREEN
                        print(f"{color}✓ {step_name} ({_format_duration_ms(duration_ms)}){RESET}", flush=True)
                        return result
                    except Exception as e:
                        duration_ms = (time.time() - start) * 1000
                        span.set_attribute("duration_ms", round(duration_ms, 2))
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        print(f"{RED}✗ {step_name} FAILED{RESET}", flush=True)
                        raise
            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                tracer = get_tracer()
                print(f"{GREEN}▶ {step_name}{RESET}", flush=True)
                with tracer.start_as_current_span(step_name) as span:
                    start = time.time()
                    try:
                        result = fn(*args, **kwargs)
                        duration_ms = (time.time() - start) * 1000
                        span.set_attribute("duration_ms", round(duration_ms, 2))
                        record_step_timing(step_name, duration_ms)
                        color = RED if duration_ms > SLOW_STEP_MS else GREEN
                        print(f"{color}✓ {step_name} ({_format_duration_ms(duration_ms)}){RESET}", flush=True)
                        return result
                    except Exception as e:
                        duration_ms = (time.time() - start) * 1000
                        span.set_attribute("duration_ms", round(duration_ms, 2))
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        print(f"{RED}✗ {step_name} FAILED{RESET}", flush=True)
                        raise
            return sync_wrapper
    return decorator
