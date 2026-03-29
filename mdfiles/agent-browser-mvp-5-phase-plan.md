# Agent Browser MVP 5-Phase Plan

Goal: fix the three current production blockers first.

1. Conditional UI: plans skip prerequisite interactions, so later buttons never appear.
2. Snapshot timing: snapshots happen before UI settles, so real elements are missing.
3. Below-the-fold targets: no viewport strategy or `scrollintoview`, so valid buttons never get surfaced.

This plan is ordered by highest ROI first. It favors small, production-safe changes over broad refactors.

## Current Dumb Logic To Change Or Remove

- In [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py), `WAIT_AFTER_CLICK_MS` is a fixed sleep. This is blunt and flaky. Replace as the primary strategy with condition-based waits.
- In [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py), the click path is still mostly `snapshot -> select_ref -> click -> sleep -> snapshot`. It does not gate on visibility, enabled state, or settled page state before selection.
- In [`app/browser/ref_selector.py`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py), selection is too snapshot-local and too string-based. Good for a baseline, not enough for runtime recovery.
- In [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py), the wrapper is too thin. The runner is forced to own retry policy, waiting policy, and diagnostics.
- In [`app/steps/step_generation.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py), the extraction schema is too narrow for conditional UI. It extracts `click_labels`, but not prerequisite dependencies, interaction type, or whether a target appears only after another action.
- In [`app/steps/contract_extraction.py`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py), target extraction is shallow. It is useful, but not strong enough to guarantee missing intermediate steps are caught.
- In [`app/steps/dom_crawler.py`](/Users/sourabhligade/shipvideo-engine/app/steps/dom_crawler.py), crawl data is static route-level grounding only. It helps planning, but it cannot prove dynamic post-click UI exists.

## Phase 1: Fix Snapshot Timing And Viewport Determinism

Why first:
This is the fastest reliability win. It directly addresses false `no_match` from loading races and responsive layout drift.

Goal:
- Make snapshots and clicks happen only after the page is ready enough for deterministic execution.
- Standardize viewport behavior for CI.

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)
- [`app/config_types.py`](/Users/sourabhligade/shipvideo-engine/app/config_types.py)

Changes:
- Add wrapper methods for `wait --load`, `wait --url`, `wait --text`, `wait <selector>`, `scrollintoview`, `is visible`, `is enabled`, `get count`, `get url`.
- Replace fixed post-click sleep as the primary path with:
  - `wait --load domcontentloaded`
  - optional `wait --load networkidle` when navigation or async content is expected
  - explicit validation wait when the step has a success condition
- After every `goto`, wait for settled state before the first snapshot.
- Add a CI-safe viewport preset in capture settings and initialize it at session start.
- Record whether a wait succeeded or timed out in step results.

Remove or de-emphasize:
- Blind dependence on `WAIT_AFTER_CLICK_MS`.

Success criteria:
- Fewer `no_match` failures immediately after navigation.
- Less variance between local and CI runs.

## Phase 2: Add Scroll And Pre-Click Gating Before Selection

Why second:
Below-the-fold elements are currently lost before we even try to act. This is a simple execution-layer fix with strong ROI.

Goal:
- Surface offscreen targets before declaring `no_match`.
- Avoid clicking hidden or disabled elements.

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)
- [`app/browser/ref_selector.py`](/Users/sourabhligade/shipvideo-engine/app/browser/ref_selector.py)

Changes:
- Add a reusable target resolution flow:
  - snapshot
  - deterministic select
  - if no match, semantic `find` fallback
  - if still no match, `scrollintoview` or bounded scroll-and-resnapshot retry
  - re-run selection
- Before click, check `is visible` and `is enabled`.
- Use `get count` to fail explicitly on ambiguity instead of letting string heuristics guess.
- Store whether selection came from deterministic match, semantic find, or scroll recovery.

Remove or de-emphasize:
- Single-shot `no_match` failure when the target may simply be offscreen.
- Purely text-only selection confidence as the only execution signal.

Success criteria:
- Fewer failures on modals, drawers, and footer CTA flows.
- Better diagnostics for hidden versus missing versus ambiguous targets.

## Phase 3: Handle Conditional UI With Runtime Recovery

Why third:
This is the root fix for "Proceed Recharge" style failures. The dependency is often implicit in component logic, not explicit in diff text, so a prompt-only planning fix will miss it regularly.

Goal:
- Detect missing prerequisite interactions at runtime and recover safely.

Files to change:
- [`app/steps/step_generation.py`](/Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py)
- [`app/steps/preflight.py`](/Users/sourabhligade/shipvideo-engine/app/steps/preflight.py)
- [`app/steps/contract_extraction.py`](/Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py)
- [`app/steps/demo_contract.py`](/Users/sourabhligade/shipvideo-engine/app/steps/demo_contract.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)

Changes:
- After a click returns `state_changed=false`, re-snapshot and check whether the next target now exists.
- If the next target still does not exist, mark the current step as a prerequisite failure.
- Ask the LLM what interaction is needed to reveal the next target, then re-run with the new step.
- Keep static planning improvements only as a best-effort hint, not the primary correctness mechanism.

Remove or de-emphasize:
- The assumption that diff text alone can reliably infer hidden UI dependencies.
- The assumption that a prompt tweak will solve conditional UI by itself.
- Over-trusting late terminal assertions to reveal a broken plan.

Success criteria:
- Missing prerequisite interactions are recovered at runtime instead of guessed in planning.
- Fewer runs where a later button never existed because the earlier step was skipped.

## Phase 4: Introduce Reusable Command Flows And Slim The Runner

Why fourth:
Once the top failures are contained, we should reduce maintenance burden. Right now too much browser policy lives in the runner.

Goal:
- Make execution modular, retry-safe, and easier to extend without adding more ad-hoc logic.

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/browser/agent_browser_types.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_types.py)
- [`app/execution/step_runner.py`](/Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py)

Changes:
- Add typed command helpers and small execution macros:
  - `navigate_and_settle`
  - `resolve_target`
  - `act_and_validate`
  - `capture_failure_artifacts`
- Move retry policy out of the click loop and into reusable helpers.
- Make validation command-driven with `wait --text`, `wait --url`, `get text`, `get attr`, not snapshot-string inspection only.
- Keep LLM fallback as last resort only.

Remove or de-emphasize:
- Custom snapshot diff summary as a primary correctness signal.
- A monolithic click loop that mixes selection, waiting, validation, and failure capture.

Success criteria:
- Smaller runner.
- Easier addition of `fill`, `select`, `check`, `press`, and other non-click actions.

## Phase 5: Add Production Diagnostics And CI Hardening

Why fifth:
This is valuable, but it should follow the reliability fixes so we instrument a cleaner system.

Goal:
- Make failures obvious and reproducible in CI.

Files to change:
- [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py)
- [`app/steps/metrics.py`](/Users/sourabhligade/shipvideo-engine/app/steps/metrics.py)
- [`observability/tracing.py`](/Users/sourabhligade/shipvideo-engine/observability/tracing.py)
- [`app/steps/pipeline.py`](/Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py)

Changes:
- Move the four core run metrics into Phase 1 so we can measure impact from the first real PR run:
  - `preflight_passed`
  - `terminal_condition_reached`
  - `steps_validated` vs `steps_unvalidated`
  - `video_usable` via manual score
- Add wrappers for `console`, `errors`, `network requests`, `trace start/stop`, `network har start/stop`.
- Save artifact paths and counts into run metrics.
- Log command-level timing, retries, wait timeouts, target strategy, and validation outcome.
- Capture failure bundles automatically on terminal failures and wrong-click failures.

Remove or de-emphasize:
- Sparse postmortem data that forces manual guesswork.

Success criteria:
- A failed CI run tells us whether the issue was plan quality, missing element, hidden element, disabled element, wrong click, console error, or backend failure.

## What To Skip For MVP Right Now

These are useful, but not the best next spend for MVP:

- Full multi-action planner redesign across all step types.
- Deep LLM runtime recovery loops.
- Complex visual diff or narration diff systems.
- Generic popup/tab/frame orchestration unless a current flow needs it.
- Broad session persistence and auth-state abstractions unless login flows become a blocker.
- A large browser orchestration framework before we finish the high-ROI fixes above.

## Recommended Execution Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5

## MVP Bottom Line

If we only do three things now, do these:

1. Replace blind sleeps with condition-based waits.
2. Add bounded scroll plus visibility checks before declaring `no_match`.
3. Add runtime recovery for missing prerequisite interactions on conditional UI.

That should remove the biggest sources of flakiness without overcomplicating the system.
