# ShipDemos.com MVP Accuracy Audit

## Goal

Make the core pipeline produce customer-sendable demo videos on real PRs.

Ignore for now:
- metrics
- observability
- tracing
- HAR/trace bundles
- infra or platform work that does not change whether the video shows the right flow

Definition of done:
- every click is correct
- the flow reaches the terminal condition
- the recorded video shows the intended feature path without embarrassing wrong states

## Executive Summary

The biggest accuracy problem is not rendering. It is flow correctness.

Today the system can:
- infer a start route
- infer visible click labels
- inject an `assert_terminal`
- execute deterministic clicks with some recovery

But it still fails on the exact cases that matter in real product demos:
- prerequisite interactions are not represented in extraction, so plans skip hidden dependencies
- preflight checks coverage, not causal completeness
- target resolution is still too snapshot-local and text-first
- terminal verification is still largely snapshot-text based
- failed clicks can still leave wrong-state frames in the final video

The five highest ROI fixes are:
1. Replace terminal snapshot matching with browser-native terminal validation
2. Upgrade target resolution to command-first lookup with stronger priority order
3. Make runtime prerequisite recovery the primary fix for conditional UI
4. Tighten planning and preflight so obviously incomplete plans never execute
5. Drop invalid frames from failed runs so bad states do not leak into the video

## Layer 1: Extraction

### What the code does now

- Static diff parsing happens in [`extract_contract_static()`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py#L17).
- PR analysis wires that contract into step generation in [`analyze_pr()`](/Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py#L35).
- LLM extraction is a separate call, not mixed into planning, in [`_run_extraction_phase()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L227).
- That extraction only returns:
  - `start_route`
  - `terminal_testid`
  - `click_labels`
  in [`app/steps/step_generation.py:261-271`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L261).

### Exact gaps causing wrong videos

1. Extraction cannot represent prerequisite interactions
- Breakpoint: [`_run_extraction_phase()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L227)
- Problem:
  - schema has no place for `select amount`, `toggle option`, `fill input`, `choose tab`, `open drawer`, or `step required before CTA appears`
  - it only extracts visible CTA labels from JSX text
- Root cause:
  - hidden dependencies live in component logic, not in diff-visible button text

2. Static contract extraction is too shallow for real flows
- Breakpoint: [`_extract_targets()`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py#L55)
- Problem:
  - extracts `data-testid` and JSX button/link text only
  - no notion of ordered dependencies or interaction type
- Root cause:
  - regex extraction cannot infer stateful UI transitions

3. Empty or low-quality extraction degrades into weak planning
- Breakpoint: [`generate_steps_from_diff()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L552)
- Problem:
  - weak extraction still flows into planning
  - very bad cases fall back to generic fallback steps instead of a flow-specific recovery
- Root cause:
  - no explicit “extraction incomplete for runnable demo” state

### Known failure explained

Flow:
- select amount
- click Recharge Now
- click Proceed Recharge

Current break:
- extraction sees `Recharge Now` and `Proceed Recharge`
- extraction does not represent `select amount`
- planning is asked to include extracted clicks, not hidden prerequisites
- execution tries to click a button that cannot exist yet

### Highest ROI fix at this layer

Fix extraction to emit interaction hints, not just click labels.

Change:
- [`app/steps/step_generation.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py)
- [`app/steps/contract_extraction.py`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py)

What exactly:
- extend extraction output with a lightweight `interaction_hints` list
- allow hints like `select`, `fill`, `check`, `tab_switch`, `reveal_step`
- keep it best-effort only for signals explicit in changed code

Why this fixes root cause:
- it gives planning something richer than a flat list of CTA labels

## Layer 2: Step Generation

### What the code does now

- Planning prompt is built in [`_build_planning_prompt()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L452).
- It enforces:
  - first step must be `goto`
  - every extracted click label must appear
  - do not stop before terminal
  - last meaningful step must be `assert_terminal`
  in [`app/steps/step_generation.py:515-527`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L515).
- `normalize_steps()` preserves `success_condition`, `validation_condition`, `expected_testid`, and `terminal` on click steps via `_PASSTHROUGH_FIELDS` in [`app/steps/step_normalizer.py:11-18`](/Users/sourabhligade/shipvideo-engine/app/steps/step_normalizer.py#L11) and [`app/steps/step_normalizer.py:87-99`](/Users/sourabhligade/shipvideo-engine/app/steps/step_normalizer.py#L87).
- `validate_against_dom()` annotates unconfirmed clicks instead of dropping them in [`app/steps/step_normalizer.py:162-236`](/Users/sourabhligade/shipvideo-engine/app/steps/step_normalizer.py#L162).
- Preflight blocks:
  - wrong start route
  - missing required contract targets
  - missing or mismatched `assert_terminal`
  - zero-click degenerate plans
  in [`preflight_gate()`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L17).
- `assert_terminal` is injected automatically in [`_inject_terminal_assertion()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L360).

### Exact gaps causing wrong videos

1. Planning prompt does not enforce sequential completeness
- Breakpoint: [`_build_planning_prompt()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L452)
- Problem:
  - it says every extracted label must appear
  - it does not say every label must be made reachable through explicit prior state-changing steps
- Root cause:
  - the planner optimizes for plausible click coverage, not causal reachability

2. Preflight checks presence, not prerequisite ordering
- Breakpoint: [`preflight_gate()`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L17)
- Problem:
  - target coverage is label-presence based at [`app/steps/preflight.py:61-93`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L61)
  - if the plan mentions the CTA, preflight is satisfied even if earlier required steps are missing
- Root cause:
  - plan integrity is measured as checklist coverage, not executable flow completeness

3. DOM reconciliation is intentionally permissive
- Breakpoint: [`validate_against_dom()`](/Users/sourabhligade/shipvideo-engine/app/steps/step_normalizer.py#L162)
- Problem:
  - good for conditional UI, but it also allows obviously shaky plans to survive into execution
- Root cause:
  - there is no second check for “this target is currently absent because a setup step is likely missing”

### Highest ROI fix at this layer

Tighten planning and preflight just enough to reject obviously incomplete plans.

Change:
- [`app/steps/step_generation.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py)
- [`app/steps/preflight.py`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py)

What exactly:
- add prompt rule: every extracted target must be made reachable by explicit prior interactions
- reject plans where the last click is a terminal CTA but no earlier setup interaction exists when extraction hints imply setup
- require the last click before `assert_terminal` to carry a validation condition

Why this fixes root cause:
- it blocks the most obviously incomplete plans before the browser opens

## Layer 3: Execution

### What the code does now

- Agent Browser execution path is [`run_ab_stepwise()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L996).
- Page settling is in [`_settle_ab_page()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L173).
- Target resolution is in [`_resolve_ab_click_target()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L234).
- Actionability gating is in [`_ensure_ab_target_actionable()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L278).
- Click attempt flow is in [`_run_ab_click_attempt()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L315).
- Runtime prerequisite recovery is in [`_recover_ab_prerequisite_steps()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L554).
- Deterministic ref selection is in [`select_ref()`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py#L120).

### Exact findings

- There is a best-effort `networkidle` wait before snapshots, but not a guarantee.
  - code: [`app/execution/step_runner.py:193-229`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L193)
- `scrollintoview` exists, but only after a ref is already found.
  - code: [`app/execution/step_runner.py:283-289`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L283)
- On `no_match`, there is one semantic find attempt and then one blind page scroll retry.
  - resolution: [`app/execution/step_runner.py:248-275`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L248)
  - retry: [`app/execution/step_runner.py:1301-1314`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1301)
- `assert_terminal` is explicit pass/fail in the runner.
  - code: [`app/execution/step_runner.py:1167-1230`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1167)
- Terminal condition checking is still fragile snapshot matching.
  - code: [`_terminal_match_in_snapshot()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L768)
- Current ref selection priority is:
  - `label`
  - `text`
  - selector-derived testid/id converted into words
  - exact a11y-name match
  - case-insensitive match
  - partial match
  - semantic `find_ref()` fallback
  This is not a true `testid > role+name > label > text` execution policy.
- Fixed sleeps still exist:
  - `WAIT_AFTER_CLICK_MS` in [`app/execution/step_runner.py:39`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L39)
  - `cli.wait(500)` in retry path at [`app/execution/step_runner.py:1304`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1304)

### Exact gaps causing wrong videos

1. Target resolution is still too text-first and snapshot-local
- Breakpoint: [`select_ref()`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py#L120)
- Problem:
  - it matches names in snapshot elements, not browser-native locator intent priority
  - it does not use `find testid`, `find label`, or `find nth`
- Root cause:
  - custom selection logic is still carrying too much weight

2. Below-fold recovery is too dumb
- Breakpoint: [`run_ab_stepwise()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1301)
- Problem:
  - retry is just `scroll down 700` then snapshot again
  - no element-scoped recovery before selection failure
- Root cause:
  - scroll happens without target knowledge

3. Terminal verification is still brittle
- Breakpoint: [`_terminal_match_in_snapshot()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L768)
- Problem:
  - string matching over `snapshot_text` can pass or fail on incidental text
- Root cause:
  - terminal assertion is not browser-command driven

4. Runtime recovery exists, but is too narrow
- Breakpoint: [`_recover_ab_prerequisite_steps()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L554)
- Problem:
  - it only triggers in a bounded branch after no state change
  - it should also be the main answer to blocked conditional UI
- Root cause:
  - recovery is treated like exception handling, not a first-class path

### Highest ROI fixes at this layer

#### Fix 1

Replace terminal snapshot matching with command-driven terminal validation.

Change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

What exactly:
- add wrappers for:
  - `find testid`
  - `find role`
  - `get count`
  - `get attr`
- evaluate `assert_terminal` by condition type:
  - `text_present` -> `wait --text`
  - `url_match` -> `wait --url`
  - `element_present` -> `find testid` or semantic find plus `get count`
- keep snapshot fallback only as last resort

Why root cause:
- it makes the end-of-flow check use browser truth, not string heuristics

#### Fix 2

Move target resolution to command-first lookup.

Change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/browser/ref_selector.py`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

What exactly:
- prefer:
  - explicit `data-testid`
  - role + accessible name
  - exact text
  - partial text
- expose command wrappers for:
  - `find testid`
  - `find role`
  - `find label`
  - `get count`
- fail on ambiguity instead of guessing

Why root cause:
- most wrong videos begin with clicking the wrong thing or failing to click the right thing

#### Fix 3

Make runtime prerequisite recovery the main solution for conditional UI.

Change:
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)
- [`app/llm/retry_engine.py`](/Users/sourabhligade/shipvideo-engine/app/llm/retry_engine.py)
- [`app/steps/step_execution.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_execution.py)

What exactly:
- in `_recover_ab_prerequisite_steps()`, use one unified recovery trigger that covers all three cases:
  - `state_changed=false` on a click that was expected to change state
  - `selection_failed:no_match` on the current step
  - `selection_failed:no_match` on the next step when the current step completed unvalidated
- ask for only the missing revealing interaction
- insert recovered steps immediately before the blocked step
- bound to one recovery cycle

Why root cause:
- conditional UI is the main reason accurate plans still fail in execution

## Layer 4: Video Accuracy

### What the code does now

- Viewport is set deterministically before main execution through [`_configure_ab_session()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L161).
- UI diff is computed after each click in [`_run_ab_click_attempt()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L462).
- Narration is generated before execution, from step generation context, not from validated runtime UI changes.
- Wrong clicks are caught immediately only when the click step has validation metadata.
- Failed runs keep already captured screenshots, so partial wrong states can still reach rendering.

### Exact gaps causing wrong or embarrassing videos

1. Narration is not grounded in verified runtime state
- Breakpoint: step generation return payload in [`app/steps/step_generation.py:862-883`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L862)
- Problem:
  - narration reflects planned story more than what the user actually saw

2. Bad frames can survive a failed run
- Breakpoint: click failure handling in [`run_ab_stepwise()`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1440)
- Problem:
  - screenshots may already exist for a wrong or partial interaction before the run aborts

### Highest ROI fix at this layer

Discard invalid click frames before they reach the final video.

Change:
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

What exactly:
- keep before/after screenshots only for:
  - validated click success
  - state-changing unvalidated steps that are known-safe
- discard the last click frame pair on:
  - `wrong_click`
  - `click_failed`
  - `terminal_not_reached` when the preceding click did not validate

Why root cause:
- even good failure detection still produces bad videos if wrong frames are left in the output

## Gap Checklist And Phase Mapping

This checklist ensures every identified gap is covered by one or more of the existing five phases.

- [ ] Extraction cannot represent prerequisite interactions -> Phases 3 and 4
- [ ] Static contract extraction is too shallow for real flows -> Phase 4
- [ ] Empty or low-quality extraction degrades into weak planning -> Phases 3 and 4
- [ ] Planning prompt does not enforce sequential completeness -> Phase 4
- [ ] Preflight checks coverage, not prerequisite ordering -> Phase 4
- [ ] DOM reconciliation is permissive and lets shaky plans through -> Phases 3 and 4
- [ ] No guaranteed settled state before every snapshot -> Phases 2 and 3
- [ ] `scrollintoview` only happens after a ref is found -> Phase 2
- [ ] `no_match` recovery is still too weak -> Phases 2 and 3
- [ ] Ref selection priority is still too text-first -> Phase 2
- [ ] Fixed sleeps still exist in critical execution paths -> Phases 1 and 2
- [ ] Terminal verification is still fragile snapshot matching -> Phase 1
- [ ] Runtime prerequisite recovery exists but is too narrow -> Phase 3
- [ ] Narration is not grounded in verified runtime state -> Phase 5
- [ ] Wrong or partial frames can survive a failed run -> Phase 5

## The Five Highest ROI Implementation Phases

### Phase 1: Browser-native terminal validation

Goal:
- make final verification trustworthy

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

Why highest ROI:
- if terminal checking is wrong, the whole video can be wrong even when clicks looked plausible

Also addresses these identified gaps: fragile terminal snapshot matching, sleep-heavy assertion paths, and incorrect final pass/fail on visually plausible but incomplete flows.

### Phase 2: Command-first target resolution

Goal:
- click the right thing reliably before adding more planner complexity

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/browser/ref_selector.py`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

Why highest ROI:
- most execution failures begin at element targeting

Also addresses these identified gaps: text-first target resolution, weak below-fold handling, post-selection-only `scrollintoview`, weak `no_match` recovery, incorrect selector priority, and sleep-heavy no-match retries.

### Phase 3: Runtime recovery for conditional UI

Goal:
- recover missing prerequisite interactions during execution

Files to change:
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)
- [`app/llm/retry_engine.py`](/Users/sourabhligade/shipvideo-engine/app/llm/retry_engine.py)
- [`app/steps/step_execution.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_execution.py)

Why highest ROI:
- this directly fixes the known “button does not exist yet” class of failures

Also addresses these identified gaps: hidden prerequisite interactions, weak extraction that still reaches execution, permissive DOM grounding that requires runtime save paths, and today’s too-narrow recovery logic. The unified trigger must cover `state_changed=false` on an expected state-changing click, `selection_failed:no_match` on the current step, and `selection_failed:no_match` on the next step when the current step completed unvalidated.

### Phase 4: Stronger planning and preflight for sequential completeness

Goal:
- stop obviously incomplete plans before browser execution

Files to change:
- [`app/steps/step_generation.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py)
- [`app/steps/preflight.py`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py)
- [`app/steps/contract_extraction.py`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py)

Why highest ROI:
- reduces wasted executions on flows that never had a chance to work

Also addresses these identified gaps: shallow prerequisite extraction, weak static contract signals, missing sequential-completeness rules, label-only preflight coverage, permissive DOM reconciliation, and weak extraction degrading into weak planning.

### Phase 5: Remove invalid frames from failed runs

Goal:
- stop wrong-state screenshots from polluting the rendered video

Files to change:
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

Why highest ROI:
- even a failed run should fail cleanly, not produce an embarrassing video artifact

Also addresses these identified gaps: narration being disconnected from verified runtime state, wrong or partial frames surviving failed runs, and embarrassing video output even when failure was detected correctly.

## What To Ignore For Now

- metrics
- observability
- tracing
- HAR or trace capture
- repo-level benchmarking and experiment promotion
- generic infra cleanup
- multi-tab or popup orchestration unless a current real flow needs it
- broader narration system redesign beyond using validated UI changes only

## Bottom Line

The core issue is not that the system lacks tooling. It is that the current extraction and execution stack still assumes visible CTAs are enough to define the flow.

They are not.

To make this product accurate enough for customer-facing videos:
- treat conditional UI as a runtime recovery problem
- move terminal checks and target resolution to browser-native commands
- reject obviously incomplete plans earlier
- prevent wrong frames from surviving failed flows

That is the shortest path to videos a PM would actually send.
