# 5 Architectural Fixes for Video Accuracy (Current Codebase Reality)

This is based on what is actually running in this repo today:
- planning in `app/steps/step_generation.py`
- normalization + DOM filtering in `app/steps/step_normalizer.py`
- orchestration in `app/steps/pipeline.py`
- execution in `app/steps/step_execution.py` and `app/execution/step_runner.py`
- AB ref intent bridge in `app/browser/ref_selector.py`

No design-doc assumptions. No imaginary journey extractor. No hidden "contract system."

---

## Fix 1: Stop `normalize_steps` From Dropping Validation Signals

### Goal
Make post-click verification actually run in production flows by preserving validation metadata from generation into execution.

### Why this is root cause
`run_ab_stepwise` already supports explicit post-click validation (`success_condition` / `validation_condition`).  
But `normalize_steps` often strips click steps down to only `{action, label|selector}`. That turns many runs into "unvalidated click success."  
You cannot enforce correctness if the fields required for correctness are deleted before execution.

### Files/modules affected
- `app/steps/step_normalizer.py`
- `app/steps/step_generation.py`
- `app/execution/step_runner.py` (consumer path, mostly validation/logging checks)

### Concrete implementation changes
1. In `normalize_steps`, preserve pass-through fields on click steps:
   - `success_condition`
   - `validation_condition`
   - `validation_source`
   - `expected_url`
   - `expected_testid`
   - `terminal`
2. Add `assert_terminal` to allowed actions (see Fix 3) and preserve its payload.
3. In `step_generation.py`, add an integrity check before return:
   - if a generated click had validation metadata but normalized click lost it, raise typed integrity error (Fix 5).
4. In `run_ab_stepwise`, keep current validation logic, but count and emit `unvalidated` explicitly into metrics.

### Integration with the pipeline
- Generation emits candidate step metadata.
- Normalization preserves metadata rather than flattening it away.
- AB runner consumes metadata and executes validation branch.
- Pipeline metrics capture validated vs unvalidated execution quality.

### Expected impact
- Removes silent "wrong click but green run" behavior.
- Converts hidden divergence into explicit `wrong_click` / `validation_failed`.
- Improves trust in step success and failure taxonomy.

---

## Fix 2: Introduce a Typed `DemoContract` as Single Source of Truth

### Goal
Create one authoritative runtime object that defines what the run must achieve (start route, ordered targets, terminal condition).

### Why this is root cause
Current intent is fragmented:
- diff JSON in prompt
- live DOM payload
- LLM-emitted steps
- filtered/normalized steps

There is no single contract to compare these against.  
Without one, each layer makes local decisions and drift is undetectable.

### Files/modules affected
- New: `app/steps/demo_contract.py`
- `app/steps/pipeline.py`
- `app/steps/step_generation.py`
- `app/steps/step_normalizer.py`
- `app/steps/step_execution.py`
- `app/execution/step_runner.py`

### Concrete implementation changes
1. Add `DemoContract` model in `app/steps/demo_contract.py`:
   - `start_route: str`
   - `targets: List[TargetRef]` (ordered click targets)
   - `terminal: TerminalCondition`
   - `contract_id: str`
   - `confidence: Literal["high","medium","low"]`
2. Build contract in `pipeline.analyze_pr()` using at least one **independent** source before planning.
   - Do **not** derive contract from the same planner call that generates steps.
   - Recommended extraction pipeline (in order):
     - `extract_contract_static(diff_files)` in new `app/steps/contract_extraction.py`
       - `start_route`: infer from changed route files (`app/**/page.tsx`, `pages/**`) and existing `_extract_routes_from_diff` style logic.
       - `targets`: parse added JSX/text literals for probable CTA labels, plus `data-testid`/`aria-label` anchors.
       - `terminal`: detect completion markers from added attributes/strings (`complete|success|done|finish`) and normalize to typed condition.
     - optional `extract_contract_llm(diff_files)` as a **separate JSON-only extraction call** (no DOM, no step planning) returning only contract fields + confidence.
   - Merge strategy:
     - static + extraction-LLM agreement -> `confidence=high`
     - partial agreement -> `confidence=medium`
     - extraction-LLM only or ambiguous static -> `confidence=low`
   - Rule: low-confidence contract cannot auto-run full demo; either regenerate extraction or degrade to safe fallback.
3. Pass contract through:
   - into `generate_steps_from_diff(...)`
   - into `run_capture(...)` and then runner path
4. Include contract in `generation_context` so stepwise and script paths share the same objective.
5. Reject "unguided" contracts for full demo runs (or flag them and force fallback mode).
6. Add contract provenance fields for auditability:
   - `source_static: bool`
   - `source_extraction_llm: bool`
   - `agreement_score: float`
   - `extraction_notes: List[str]`

### Integration with the pipeline
- `pipeline.py` owns contract lifecycle.
- `step_generation.py` plans against contract + DOM.
- preflight/gating validates plan vs contract.
- runner evaluates completion against contract terminal assertion.

### Expected impact
- Eliminates silent plan corruption between stages.
- Breaks circular validation (contract no longer derived from planner output itself).
- Makes failures attributable: extraction vs planning vs execution.
- Enables deterministic gating and meaningful metrics.

---

## Fix 3: Replace DOM Filtering With Reconcile + Preflight Gate

### Goal
Add a hard verification boundary between planning and execution so invalid plans never open the browser.

### Why this is root cause
`validate_against_dom(...)` currently acts as a lossy filter:
- drops steps that do not match exact labels/selectors
- may still accept routes inferred from diff even if not crawled
- does not fail the run when critical steps are lost

That is cleanup, not contract enforcement.

### Files/modules affected
- `app/steps/step_normalizer.py` (split responsibilities)
- `app/steps/step_generation.py` (invoke reconciliation + preflight)
- `app/steps/pipeline.py` (retry/abort policy)
- New: `app/steps/preflight.py` (recommended)

### Concrete implementation changes
1. Keep reconciliation non-blocking:
   - `reconcile_steps_with_dom(steps, dom_data, contract) -> ReconciliationResult`
   - annotate each click with match method/confidence (`exact`, `high`, `low`, `none`)
2. Add `match_label(...)` with confidence scoring:
   - exact text -> `exact`
   - explicit testid mapping -> `high`
   - constrained fuzzy contains overlap -> `high` or `low`
3. Add blocking preflight gate:
   - `preflight_gate(steps, contract, reconciliation) -> PreflightResult`
   - enforce:
     - first `goto` equals `contract.start_route`
     - all contract targets covered at acceptable confidence
     - explicit `assert_terminal` step exists and matches contract terminal
     - no degenerate plan (e.g., zero click steps)
4. In `pipeline.py`:
   - if preflight fails, call a **separate repair-generation path** one time with structured errors
   - if second preflight fails, abort as `plan_invalid`; do not execute
5. Add hard retry constraints (non-negotiable):
   - second attempt must use a different prompt template than first attempt:
     - include only: `DemoContract`, preflight failures, reconciled DOM match summary
     - exclude: raw diff payload, broad DOM dumps (`real_buttons` full list), long narrative instructions
   - enforce bounded output schema for repair:
     - only `steps` array
     - max steps cap (e.g., 8)
     - required actions: `goto`, `click`, `screenshot`, `assert_terminal`
   - require explicit "fix list" acknowledgement in response:
     - each preflight error maps to at least one corrected step id
   - no third attempt in `analyze_pr`; fail fast after second preflight failure.
6. Implement repair call as separate function in `step_generation.py`:
   - `regenerate_steps_from_preflight(contract, preflight_errors, dom_hints) -> steps`
   - internally use a dedicated system message and reduced context payload.

### Integration with the pipeline
- Generation produces candidate steps.
- Reconciliation annotates, preflight decides.
- Only preflight-passed plans proceed to `run_capture`.
- Failures trigger one constrained repair call (contract + errors only), then re-preflight.
- Structured reasons are transformed into deterministic repair inputs, not appended to the original noisy prompt.

### Expected impact
- Removes "broken plan reaches browser" failure class.
- Converts hidden cleanup losses into explicit regeneration or abort.
- Avoids expensive "same prompt, same mistakes" retries by forcing a structurally different second pass.
- Improves consistency of captured videos and reduces wasted runs.

---

## Fix 4: Give AB Runner Real Adaptive Recovery (Parity With Stepwise)

### Goal
Close the reliability gap between default AB execution and Playwright stepwise by enabling bounded replan from current snapshot on divergence.

### Why this is root cause
Current asymmetry:
- `run_stepwise` (Playwright) can regenerate queue via `regenerate_with_feedback`.
- `run_ab_stepwise` retries local click issues but exits fatally on `no_match`, `ambiguous`, repeated divergence.

Default backend should not be less adaptive than fallback backend.

### Files/modules affected
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/llm/retry_engine.py` (extend for AB snapshot replanning)
- `app/browser/ref_selector.py` (context for disambiguation remains)

### Concrete implementation changes
1. Add bounded AB replan hook in `run_ab_stepwise`:
   - trigger on `no_match`, `ambiguous`, repeated `wrong_click` (not stale-ref single retry case)
   - replan remaining steps from fresh AB snapshot + contract remainder
2. Add run-level limits:
   - max replans per run: **1** (hard cap)
   - max total step budget retained
3. Feed replan failures into typed integrity/reporting errors (Fix 5).
4. Keep fatal exit when replan budget is exhausted (`unrecoverable` outcome).
5. On failed replan, surface the unreachable contract target explicitly:
   - `unreachable_target_label`
   - `unreachable_target_index`
   - `failure_stage=execution`
   - abort immediately (no second replan).

### Integration with the pipeline
- AB execution remains primary.
- On divergence, AB requests targeted replan of remaining objectives.
- Contract ensures replans do not drift off-goal.
- Metrics capture number and reason of replans.
- Replan is explicitly runtime-drift recovery, not a band-aid for weak extraction/preflight.

### Expected impact
- Reduces hard aborts from resolvable runtime drift.
- Improves completion rate on dynamic staging pages.
- Makes AB behavior closer to the existing adaptive model in Playwright path.
- Prevents runtime recovery from masking upstream planning/extraction defects.

---

## Fix 5: Add First-Class Metrics and Typed Integrity Errors

### Goal
Turn video-accuracy work from anecdotal debugging into measurable engineering with stage-level failure attribution.

### Why this is root cause
Main PR path lacks reliable top-line metrics for:
- plan validity before execution
- terminal condition completion
- validated vs unvalidated clicks
- integrity failures across stages

Without these, you cannot prove improvements or regressions.

### Files/modules affected
- `app/steps/pipeline.py`
- `app/steps/step_generation.py`
- `app/steps/step_execution.py`
- `app/execution/step_runner.py`
- New: `app/steps/metrics.py` (recommended)
- New: `app/steps/errors.py` (typed errors)

### Concrete implementation changes
1. Add typed error:
   - `ContractIntegrityError(stage, field, expected, actual, contract_id)`
   - stage enum: `normalization | preflight | execution | terminal`
2. Raise it when:
   - normalization drops required validation field
   - preflight misses required contract target
   - execution ends without terminal assertion evaluation
3. Add `RunMetrics` payload written per run:
   - planning: `preflight_passed`, `preflight_errors`, `targets_matched/total`, `degenerate_plan`
   - execution: `steps_executed`, `steps_validated`, `steps_unvalidated`, `wrong_clicks`, `stale_refs`, `replans_triggered`
   - outcome: `terminal_condition_reached`, `execution_outcome`, `backend_used`
   - integrity: list of `contract_integrity_errors`
4. Persist metrics as JSON per run and aggregate rates in a simple summary job.

### Integration with the pipeline
- Every stage updates the same run metrics object.
- Typed errors automatically map to metric events.
- CI / dashboards consume aggregate rates for release decisions.

### Expected impact
- Exposes dominant failure source by stage.
- Prevents "looks better in one run" false confidence.
- Enables data-driven iteration on planning, gating, and execution.

---

## Recommended rollout order

1. **Fix 1** (preserve validation metadata)  
2. **Fix 2** (introduce minimal contract)  
3. **Fix 3** (reconcile + blocking preflight gate)  
4. **Fix 5** (metrics + typed integrity errors)  
5. **Fix 4** (AB adaptive replan after contract/gate are stable)

Reason: recovery logic should not be added before plan integrity is enforceable.

---

## What changes after these five fixes

The system stops pretending a filtered plan is a valid plan.  
Every run has a contract, every plan is preflight-verified, every click can be validated, AB can recover from runtime drift within bounds, and every failure is measured by stage.

That is the difference between "sometimes good videos" and a pipeline that can explain, prevent, and improve bad runs.

