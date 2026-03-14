# Observability (MVP)

Lightweight distributed tracing for the pipeline using **OpenTelemetry**. Spans are kept in-memory (no JSON export). **Readable step logs** are printed by the decorator. No Jaeger, Grafana, or Loki.

## How tracing works

1. **Startup**: `init_tracing()` is called once when the app starts (FastAPI `startup` event). It configures a `TracerProvider` with **no span exporter**, so no raw JSON is printed.

2. **Root span**: The webhook background job uses `pipeline_run_span()` as a context manager. It starts the `pipeline_run` span, clears step timings, and on exit prints the **PIPELINE SUMMARY** table. Attributes `repo`, `pr_number`, `preview_url`, and `steps_generated` are set on the root span when available.

3. **Child spans**: Each major step is wrapped with `@pipeline_step("step_name")`. The decorator:
   - Starts a span
   - Prints `▶ step_name` when the step starts
   - On success: prints `✓ step_name (duration)` and records timing for the summary; if duration > 5 s, prints `⚠ SLOW STEP` first
   - On error: prints `✗ step_name FAILED` and sets span status to `ERROR`
   - Uses `time.time()` for duration and sets `duration_ms` on the span

4. **Hierarchy**: Spans are nested automatically (unchanged):
   - `pipeline_run`
     - `preview_lookup`
     - `preview_ready`
     - `route_diff_analysis`
       - `route_diff_fetch`
       - `llm_analysis`
     - `video_pipeline`
       - `capture`
       - `render`
       - `upload`

## How the terminal looks

No JSON. You’ll see:

```
▶ preview_lookup
✓ preview_lookup (0.3 ms)

▶ preview_ready
✓ preview_ready (196 ms)

▶ route_diff_fetch
✓ route_diff_fetch (430 ms)

▶ llm_analysis
⚠ SLOW STEP
✓ llm_analysis (26863 ms)

▶ capture
✓ capture (420 ms)

...

PIPELINE SUMMARY
preview_lookup       0.3 ms
preview_ready      196.0 ms
route_diff_fetch   430.2 ms
llm_analysis     26863.8 ms
capture            420.0 ms
render             210.0 ms
TOTAL              27.9 s
```

On step failure: `✗ step_name FAILED` (exception is still recorded on the span).

## How to add a new pipeline step

1. Import the decorator (do **not** import OpenTelemetry in business logic):

   ```python
   from observability import pipeline_step
   ```

2. Decorate the function:

   ```python
   @pipeline_step("my_step_name")
   def my_step(...):
       ...
   ```

   For async functions the same decorator works:

   ```python
   @pipeline_step("my_async_step")
   async def my_async_step(...):
       ...
   ```

3. The step will get `▶` / `✓` (or `✗`) logs and appear in the pipeline summary.

## How to add attributes to spans

- **Root span**: Inside `with pipeline_run_span() as span:` in the webhook, use `span.set_attribute("key", value)` for `repo`, `pr_number`, `preview_url`, `steps_generated`.
- **Errors**: Use `set_current_span_error(message)` from `observability` to mark the current span as failed.
- **Duration**: The decorator sets `duration_ms` on every step span.

## Rules

- **Business logic must NOT import OpenTelemetry** (`opentelemetry.*`). Only the `observability` package imports it.
- Use only: `init_tracing`, `get_tracer`, `pipeline_run_span`, `pipeline_step`, and `set_current_span_error` from `observability`.
- Avoid duplicate instrumentation: decorate each meaningful step once.
