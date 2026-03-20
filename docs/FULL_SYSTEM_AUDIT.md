# Full System Audit

## Scope

This audit covers the full pipeline end to end:

- diff extraction
- trigger evaluation
- DOM crawl
- step generation
- validation
- stepwise execution
- repair loop
- rendering
- script-first pipeline interaction

Reference sources:

- `docs/EXECUTION_PLAN.md` as source of truth
- `docs/ACCURACY_IMPROVEMENT_ROADMAP.md` for original intent and edge cases

## Executive Summary

The system is materially improved, but it is not fully production-ready.

The strongest parts are:

- unified DOM field naming
- multi-route crawl with route seeding
- live selector existence validation
- structured navigation detection
- render normalization for full-page screenshots

The biggest remaining gaps are:

1. runtime `goto` validation can reject valid multi-route steps
2. failed re-anchoring after navigation does not stop execution
3. trigger ingress is only partially unified (`force` and `general_demo` are incomplete)
4. diff-budgeting is partially undermined by the upstream per-file patch cap
5. the preferred script-first path does not fully inherit the accuracy hardening added to stepwise execution

---

## Phase Compliance

### Phase 1: Unified DOM Schema

Status: Mostly implemented

What is correct:

- `app/dom_schema.py` exists and defines canonical DOM structures.
- Legacy `label` aliasing is removed from step/script generation.
- `dom_extractor` separates `aria` and `title`.
- `dom_crawler` preserves `testid`, `aria`, `id`, `role`, and `selector`.

Problems:

- `route_snapshots` is returned by `dom_crawler` but is not represented in `DomSnapshot`.
- `dom_crawler` sets button `title` to `""` instead of collecting it.

Required fix:

- Extend `app/dom_schema.py` to include `RouteSnapshot` and `route_snapshots`.
- Either collect `title` in `dom_crawler` or remove it from crawler-produced button expectations.

### Phase 2: Trigger Filtering + Config Validation

Status: Partially implemented

What is correct:

- `app/trigger.py` contains `is_ui_file()`, `score_file()`, `TriggerDecision`, and `evaluate_trigger()`.
- `app/config.py` validates trigger mode, viewport, and `routeMap`.
- `pipeline.py` short-circuits when `should_run=False`.

Problems:

- `force=True` exists in `evaluate_trigger()` but is never threaded from webhook/comment execution.
- `general_demo` is never set to `True`, so the homepage-only crawl mode is effectively dead.
- Webhook still contains a separate smart-mode filter path, so trigger behavior is duplicated and can drift.

Required fix:

- Add `force` to `analyze_pr()` and pass it through from `app/webhook.py`.
- Compute `general_demo` in `evaluate_trigger()` when appropriate.
- Remove or collapse the webhook-side smart trigger pre-check into `evaluate_trigger()`.

### Phase 3: Multi-Route DOM Crawl

Status: Mostly implemented

What is correct:

- `crawl_dom_data()` accepts `seed_routes` and uses a shared `BrowserContext`.
- A fresh page is created per route.
- Auth-wall detection is present.
- Route discovery strips query strings and fragments.
- Visit ordering is deterministic and guarantees `/`.
- Per-route errors do not fail the whole crawl.

Problems:

- Returned `current_path` is always `"/"` even though the snapshot is merged across multiple routes.
- `route_snapshots` exists only as an undocumented extra key from a typing perspective.
- Input merging drops some inputs when both `name` and `placeholder` are empty.

Required fix:

- Define the merged-snapshot contract explicitly in `app/dom_schema.py`.
- Replace `current_path` with a clearer merged-snapshot field, or document it as entry path rather than active path.
- Improve input dedupe keys to include `testid`, `aria`, `id`, or `input_type` when name/placeholder are absent.

### Phase 4: Live Selector Validation + Stable Nav Detection

Status: Partially implemented

What is correct:

- `PageFingerprint` is implemented.
- `detect_major_change()` uses structural signals.
- `validate_step_against_dom(..., page=...)` performs live existence checks.
- `step_runner` and `retry_engine` pass `page` through.

Problems:

- Live validation checks only existence, not uniqueness.
- Re-anchoring after navigation is not enforced when regeneration fails.
- Runtime `goto` validation uses current-page DOM context rather than the multi-route crawl route set.

Required fix:

- Reject ambiguous click targets when live count is greater than 1, or introduce a deterministic disambiguation rule.
- Make failed post-navigation regeneration a hard failure.
- Validate `goto` against generation-time allowed routes, not only the runtime extractor’s local route list.

### Phase 5: Smarter Diff Budgeting

Status: Partially implemented

What is correct:

- `app/steps/diff_budget.py` exists.
- `budget_diff_files()` uses `score_file()` from `app.trigger`.
- `step_generation` uses the budgeted diff payload instead of the old blunt slice.

Problems:

- `fetch_pr_diff()` truncates each patch to 3000 chars, so the 4000-char primary budget tier is unreachable in the main path.
- Budgeting is applied to raw patch text, not the final serialized prompt string.

Required fix:

- Raise `MAX_PATCH_CHARS` to at least 4000 or lower the documented tier to 3000.
- If strict prompt size control is needed, budget the final serialized JSON payload instead of only patch substrings.

### Phase 6: Repair Loop Hardening + Config Wiring

Status: Mostly implemented

What is correct:

- `CaptureSettings` was moved into `app/config_types.py`.
- `step_runner` uses the configured viewport and `full_page_screenshots`.
- `render.py` uses the same viewport source of truth and normalizes frame sizes.
- `app/llm/step_generator.py` has `json_schema -> json_object` fallback.
- `record_spend()` is called once per successful `generate_next_steps()` invocation.
- `retry_engine` treats `RuntimeError` as retryable.

Problems:

- `full_page_debug_screenshots` remains loaded but unused.
- `retry_engine` only retries `RuntimeError`, not other generation-side exceptions like parse failures.

Required fix:

- Either wire `full_page_debug_screenshots` into the active runtime path or remove it.
- Expand retry classification to include the generation failures that should be treated as recoverable.

---

## Cross-Module Contract Issues

### 1. `dom_crawler -> step_generation`

Affected files:

- `app/dom_schema.py`
- `app/steps/dom_crawler.py`
- `app/steps/step_generation.py`

Problem:

`step_generation` consumes `dom_data` as if it were the canonical `DomSnapshot`, but `dom_crawler` returns additional structure (`route_snapshots`) that is not reflected in the schema.

Why this matters:

- Typed consumers cannot rely on the schema being complete.
- Future code may accidentally depend on undeclared fields.
- The merged snapshot’s `current_path` no longer means what the schema says it means.

Fix:

- Promote `route_snapshots` into the schema.
- Define a dedicated merged-snapshot type or clarify the semantics of `current_path`.

### 2. `step_generation -> validators`

Affected files:

- `app/steps/step_generation.py`
- `app/steps/step_normalizer.py`
- `app/policy/selector_validator.py`

Problem:

Generation-time validation uses the multi-route crawl snapshot, but runtime validation switches to `extract_dom_context(page)`, which only reflects the current page. This is especially dangerous for `goto` steps.

Why this matters:

- A valid route discovered from diff inference, `routeMap`, or another crawled page can be rejected before execution.

Fix:

- Carry the generation-time allowed route set into execution-time validation.
- Treat `goto` as a special case whose authority comes from crawl context, not only the current page.

### 3. `validators -> step_runner`

Affected files:

- `app/policy/selector_validator.py`
- `app/execution/step_runner.py`

Problem:

Validation proves only that a selector or text target exists, while execution clicks `.first`.

Why this matters:

- Multi-match selectors can pass validation but click the wrong control in execution.

Fix:

- Reject `count > 1` unless the selector is intentionally unique by construction.
- Or add explicit ranking/disambiguation logic before execution.

---

## Accuracy Risks

### 1. Runtime `goto` over-filtering

Affected files:

- `app/context/dom_extractor.py`
- `app/policy/selector_validator.py`
- `app/execution/step_runner.py`

Problem:

The runtime validator checks `goto` routes against the current-page extractor’s `routes`, not the full multi-route crawl result.

Accuracy impact:

- Valid navigation steps can be dropped.
- The system falls back to regeneration or generic screenshots more often than necessary.

Fix:

- Merge generation-context routes into runtime validation.

### 2. Missing button `title` in crawler snapshots

Affected files:

- `app/steps/dom_crawler.py`
- `app/dom_schema.py`

Problem:

The schema includes `title`, but crawler output always sets it to `""`.

Accuracy impact:

- Tooltip-only affordances are not represented consistently across sources.

Fix:

- Collect `title` in the crawler JS extraction, while keeping it selector-ineligible.

### 3. Partial input coverage in merged snapshots

Affected file:

- `app/steps/dom_crawler.py`

Problem:

Inputs with empty `name` and `placeholder` are omitted from merged `inputs`.

Accuracy impact:

- Forms with testid/aria-only inputs lose grounding signal.

Fix:

- Expand dedupe key to include `testid`, `aria`, `id`, and `input_type`.

### 4. Weak selector strategy under ambiguity

Affected files:

- `app/policy/selector_validator.py`
- `app/execution/step_runner.py`

Problem:

Existence is treated as sufficient proof of correctness.

Accuracy impact:

- Wrong-element clicks remain possible in repeated-component UIs.

Fix:

- Add uniqueness enforcement or stable prioritization.

### 5. Script-first path lags behind stepwise hardening

Affected files:

- `app/steps/pipeline.py`
- `app/script_pipeline.py`
- `app/generator/script_generator.py`

Problem:

The preferred path is script-first, but most new hardening was added to stepwise execution.

Accuracy impact:

- The production-preferred path can still underperform the fallback path on difficult PRs.

Fix:

- Bring script-first prompt grounding and retry parity closer to stepwise behavior, or temporarily prefer stepwise until parity is achieved.

---

## Runtime Failure Risks

### 1. Failed re-anchor does not abort

Affected file:

- `app/execution/step_runner.py`

Problem:

After a major navigation, regeneration may fail and the old queue tail is left intact.

Failure mode:

- Steps intended for the previous page execute on the new page.

Fix:

- Abort immediately if navigation re-anchor fails.

### 2. Repair loop retries too narrowly

Affected file:

- `app/llm/retry_engine.py`

Problem:

Only `RuntimeError` is treated as retryable.

Failure mode:

- Other generation-side failures break the repair loop outright.

Fix:

- Expand retry handling to include parse and transport errors that should be recoverable.

### 3. Trigger `on-demand` comment flow is incomplete

Affected files:

- `app/webhook.py`
- `app/steps/pipeline.py`
- `app/trigger.py`

Problem:

Webhook parses `--force`, but that override never reaches `evaluate_trigger()`.

Failure mode:

- Explicit user override still leads to a skipped run in on-demand mode.

Fix:

- Thread `force` all the way into `evaluate_trigger()`.

### 4. Render path is normalized, but prompt budget can still collapse upstream

Affected files:

- `app/render.py`
- `app/steps/pr_extraction.py`
- `app/steps/diff_budget.py`

Problem:

Rendering is robust now, but large diffs can still degrade step generation earlier due to upstream patch truncation and total prompt size issues.

Fix:

- Align upstream patch extraction with downstream budgeting.

---

## Edge Case Behavior

### Large PR diff

Expected behavior:

- Non-UI files are stubbed.
- UI files are ranked.
- Large diffs are reduced before the LLM call.

Where it breaks:

- Primary UI budget cannot exceed the upstream 3000-char patch cap.
- Final serialized diff payload can still exceed the intended budget envelope.

Fix:

- Align patch cap with tier budget and budget serialized output if necessary.

### Multi-route UI change

Expected behavior:

- Diff routes and `routeMap` seed the crawl.
- The changed page is crawled and grounded.

Where it breaks:

- Runtime `goto` can be rejected if the current-page DOM extractor does not list the route.

Fix:

- Validate runtime navigation against generation-time route authority.

### Dynamic or lazy-loaded elements

Expected behavior:

- Live selector validation waits briefly and recounts.

Where it breaks:

- Slow renders beyond the 1500ms wait window can still fail validation.
- Duplicate matches still pass validation and may click the wrong target.

Fix:

- Consider configurable wait windows and uniqueness checks.

### Auth-protected routes

Expected behavior:

- Crawl skips auth walls instead of failing the entire run.

Where it breaks:

- If all important changed UI is behind auth, grounding signal becomes weak and the system falls back to generic behavior.

Fix:

- Implement authenticated crawl/session injection when auth configuration is available.

---

## Redundancy and Dead Code

### 1. Trigger logic duplication

Affected files:

- `app/webhook.py`
- `app/trigger.py`

Problem:

There are still two trigger decision systems:

- webhook smart pre-check
- pipeline `evaluate_trigger()`

Fix:

- Consolidate on `evaluate_trigger()` and remove custom webhook-side filtering.

### 2. Dead `general_demo`

Affected files:

- `app/trigger.py`
- `app/steps/pipeline.py`
- `app/steps/step_generation.py`

Problem:

The field exists and is threaded through, but is never actually enabled.

Fix:

- Add real producer logic in `evaluate_trigger()`.

### 3. Dead `full_page_debug_screenshots`

Affected file:

- `app/config_types.py`

Problem:

The config field exists, loads, and validates, but is not consumed by the live runtime path.

Fix:

- Wire it into debug screenshot behavior or remove it.

### 4. Missing automated test coverage for the roadmap guarantees

Problem:

The repo does not contain the test suite needed to prove phase success criteria.

Fix:

- Add focused tests for:
  - trigger modes and `force`
  - multi-route crawl route ordering
  - route validation at runtime
  - re-anchor failure behavior
  - diff budgeting
  - render normalization

---

## Top 5 Remaining Risks Before Shipping

1. Runtime `goto` validation can reject correct steps discovered by the multi-route crawl.
2. Navigation re-anchor failure can silently continue with stale queued steps.
3. Trigger ingress is not fully unified; `force` and `general_demo` are incomplete.
4. Diff budgeting is only partially effective because of the upstream 3000-char cap.
5. Script-first remains behind stepwise in grounding and recovery behavior.

---

## Recommended Fix Order

### Priority 1

- Fix runtime `goto` validation to use generation-time route authority.
- Make failed navigation re-anchor a hard failure.

### Priority 2

- Thread `force` through webhook -> pipeline -> trigger evaluation.
- Implement real `general_demo` production logic.
- Remove duplicate webhook smart gating.

### Priority 3

- Align `MAX_PATCH_CHARS` with Phase 5 tier budgets.
- Tighten live validation to handle ambiguous selectors/text.

### Priority 4

- Normalize schema by adding `route_snapshots` to `DomSnapshot`.
- Improve input merge coverage and collect crawler `title`.

### Priority 5

- Bring script-first closer to stepwise parity or temporarily prefer stepwise as default.
- Add the missing test suite for all critical phase guarantees.

---

## Final Verdict

The system is close, but not yet ready to ship as fully production-grade.

The architecture now has the right building blocks, but there are still a few important contract and control-flow gaps that can cause:

- correct steps to be rejected
- stale plans to continue after navigation
- explicit user overrides to be ignored
- large diffs to be grounded less effectively than intended

The most important fixes are small and surgical. Once those are addressed and backed by tests, the system will be in a much stronger position for production rollout.
