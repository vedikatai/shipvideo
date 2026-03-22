# Agent Browser CLI Accuracy Validation Plan

## Scope
This document is intentionally limited to **what we are doing now**:

- use the stock `agent-browser` binary
- use it through a **CLI-only** integration
- test whether it works accurately in this codebase right now

This document does **not** describe a future daemon architecture, worker pool, SaaS control plane, or a fork of Agent Browser. Those topics are intentionally out of scope for this file.

## 1. Current Goal
The current goal is simple:

> verify that Agent Browser, used through a CLI-only adapter, gives more accurate page understanding and interaction than the current selector/text-guessing stepwise flow.

We are not trying to productionize the full architecture in this phase. We are trying to answer:

- Does Agent Browser snapshot the right interactive elements?
- Does ref-based clicking work reliably on the pages we care about?
- Does it improve action accuracy enough to justify deeper integration later?

## 2. Current Codebase Architecture
### Existing automation paths
The codebase currently has two main browser automation paths:

1. **Script-first Playwright pipeline**
   - `app/steps/pipeline.py`
   - `app/script_pipeline.py`
   - `app/generator/script_generator.py`
   - `app/recorder/playwright_runner.py`

2. **Stepwise Playwright fallback**
   - `app/steps/step_execution.py`
   - `app/execution/step_runner.py`
   - `app/context/dom_extractor.py`
   - `app/policy/selector_validator.py`
   - `app/llm/step_generator.py`
   - `app/llm/retry_engine.py`

### Current weakness we want to test against
The current stepwise flow still depends on:

- simplified DOM extraction
- LLM-generated selectors or exact visible text
- Playwright execution based on those guesses

The biggest current issue is not browser control itself. The issue is **grounding**:

- the model is still asked to infer target selectors
- the DOM representation is lossy
- validation happens after a weak target has already been proposed

Agent Browser is being tested specifically because it replaces this with:

- accessibility-based snapshots
- concrete refs such as `@e1`, `@e2`
- direct interaction against known refs

## 3. Decision for This Phase
### Chosen approach
For this phase we will use:

- **Agent Browser via CLI only**
- **stock upstream binary**
- **Python subprocess wrapper**

### Why this is the right approach right now
Because the immediate task is accuracy validation, CLI-only is the fastest and most honest way to answer the question.

It lets us:

- test real snapshots
- test ref-based clicks
- compare outcomes against the current Playwright-based path
- avoid large architecture work before we have proof

### What this means in practice
For now:

- CLI-only **will work for a pilot or controlled launch**
- CLI-only is **good enough for this Phase 1 rollout**
- CLI-only is acceptable for **accuracy testing and maybe early beta-style experimentation**

For this file, that is enough.

## 4. What We Are Testing
We are testing whether Agent Browser is more accurate than the current stepwise layer in the places that matter most.

### Accuracy dimensions
#### 1. Snapshot quality
Can Agent Browser identify the actual interactive controls on the page better than the current DOM extractors?

We care about:

- buttons
- links
- inputs
- custom clickable elements
- menu/navigation items

#### 2. Action accuracy
Can it perform the intended interaction using a ref without requiring selector invention?

We care about:

- click success
- correct target clicked
- fewer "not found" failures
- fewer ambiguous text-based interactions

#### 3. Re-anchoring quality
After a click or navigation, can we take another snapshot and continue correctly from the new page state?

#### 4. Fit with the existing pipeline
Can we call Agent Browser from Python cleanly enough to run real experiments inside this repo without rewriting the full system?

#### 5. Observability of the experiment
Can we explain every decision the system made during the trial?

This matters because "it clicked the wrong thing" is not actionable unless we can inspect:

- the raw snapshot it saw
- which ref it chose
- why it chose it
- what the page looked like before and after
- how long each step took

## 5. Minimal Architecture For Now
### Immediate architecture
```text
PR / preview URL
  -> Python orchestrator
  -> thin CLI wrapper around agent-browser
  -> open page
  -> snapshot
  -> choose known ref manually or through simple logic / existing LLM prompt adaptation
  -> click ref
  -> snapshot again
  -> capture screenshot
  -> compare result against current Playwright flow
```

### Separation of concerns in this phase
#### Decision layer
- existing logic or lightweight temporary prompt adaptation
- consumes Agent Browser snapshot output
- selects an action using refs

#### Execution layer
- new CLI adapter only
- no daemon/session manager
- no fork
- no long-lived service abstraction beyond what the stock CLI already gives us

## 6. Minimal Stable Snapshot Contract
Even though this is only a CLI experiment, we still need a minimally stable normalized shape for downstream logic.

At minimum, each interactive element passed into the decision layer should normalize to:

```json
{
  "ref": "@e1",
  "role": "button",
  "name": "Submit",
  "url": "https://example.com/settings",
  "visible": true
}
```

Minimum run-level snapshot payload should include:

- `current_url`
- `snapshot_text`
- `interactive_elements`
- `raw_snapshot_path`

This is enough to keep the experiment stable without over-designing a permanent schema.

## 7. Files To Touch Now
### New files to introduce
- `app/browser/__init__.py`
- `app/browser/agent_browser_cli.py`
- `app/browser/agent_browser_types.py`

### Existing files to modify now
- `app/context/dom_extractor.py`
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/steps/dom_crawler.py`
- `app/dom_schema.py`

### Files we should avoid changing in this phase unless necessary
- `app/script_pipeline.py`
- `app/recorder/playwright_runner.py`
- `app/generator/script_generator.py`

Those belong to the script-first path and are not necessary for the immediate accuracy test.

## 8. Phase Implementation Plan
### Phase 1 — CLI Wrapper and Stable Snapshot Normalization
#### Goal
Build the thinnest possible Agent Browser integration point inside the Python codebase.

This phase exists first because every later experiment depends on being able to:

- invoke `agent-browser` reliably
- get back parseable snapshot output
- normalize that output into a stable local structure

Without this, all later accuracy results would be polluted by wrapper instability rather than browser accuracy.

#### Files Impacted
| File | Action | Changes | Why |
|------|--------|---------|-----|
| `app/browser/__init__.py` | **Create** | Export the CLI wrapper and shared types | Gives the browser integration a clear module boundary |
| `app/browser/agent_browser_cli.py` | **Create** | Add a thin subprocess wrapper with `open()`, `snapshot()`, `click()`, `wait()`, `screenshot()`, `close()` | Centralizes all CLI calls and avoids sprinkling raw subprocess logic across the codebase |
| `app/browser/agent_browser_types.py` | **Create** | Define lightweight TypedDict/dataclass types for normalized snapshot, interactive element, and command result | Keeps downstream logic stable despite raw CLI output variation |
| `app/dom_schema.py` | **Modify** | Add a temporary Agent Browser snapshot shape with `snapshot_text`, `refs`, `interactive_elements`, `current_url` | Gives downstream code a minimally stable contract for experiments |
| `app/context/dom_extractor.py` | **Left unchanged intentionally** | Do not switch extraction yet in this phase | Phase 1 is about proving the wrapper and normalization, not touching execution logic yet |
| `app/execution/step_runner.py` | **Left unchanged intentionally** | No runtime execution changes yet | Keeps experiment risk isolated to the wrapper layer |
| `app/steps/step_execution.py` | **Left unchanged intentionally** | No orchestration changes yet | Prevents mixing wrapper bugs with runner bugs |
| `app/steps/dom_crawler.py` | **Left unchanged intentionally** | No crawl-path swap yet | Route crawling comes after snapshot normalization is working |

#### Key Tasks
1. Create `app/browser/agent_browser_cli.py`.
2. Implement `subprocess.run(...)` command execution with:
   - non-zero exit detection
   - stdout/stderr capture
   - JSON parsing where supported
3. Implement a thin `snapshot(interactive=True, cursor=True, compact=True)` call that maps to Agent Browser flags.
4. Implement snapshot normalization into a stable local structure:
   - `ref`
   - `role`
   - `name`
   - `url`
   - `visible`
5. Save raw snapshot payloads to disk for inspection during development.
6. Add a tiny smoke test or manual harness proving:
   - open page
   - snapshot
   - parse refs

#### Success Criteria
- `agent-browser` can be invoked from Python without crashing on a normal page load.
- Snapshot output is parsed into a normalized structure with valid refs.
- At least one known interactive element from a sample page appears in `interactive_elements`.
- Raw snapshot JSON can be saved and re-opened for debugging.

#### Risks & Mitigation
- **CLI failure / binary missing**
  - Failure mode: subprocess returns non-zero or `agent-browser` is not installed.
  - Mitigation: fail early with explicit wrapper error including command and stderr.
- **Snapshot output shape drift**
  - Failure mode: raw CLI payload changes and breaks downstream parsing.
  - Mitigation: lock a minimal normalized schema in `app/browser/agent_browser_types.py`; only normalize the required fields.
- **Over-scoping Phase 1**
  - Failure mode: touching step runner too early mixes browser and orchestration bugs.
  - Mitigation: intentionally leave execution files unchanged in this phase.

### Phase 2 — Deterministic Ref Selection and Decision Policy
#### Goal
Define a real decision layer for the experiment instead of leaving it vague.

This phase exists second because once snapshots are stable, the next biggest source of false conclusions is noisy action selection. If ref choice is inconsistent, the experiment cannot tell us whether Agent Browser is better.

#### Files Impacted
| File | Action | Changes | Why |
|------|--------|---------|-----|
| `app/browser/ref_selector.py` | **Create** | Add deterministic ref-selection logic for exact match, case-insensitive match, partial match, and ambiguity detection | Makes experiment results repeatable and auditable |
| `app/browser/agent_browser_types.py` | **Modify** | Add selection result types such as `selection_reason`, candidate list, ambiguity status | Needed so the logger and runner can record why a ref was chosen |
| `app/llm/step_generator.py` | **Left unchanged intentionally** | Do not rewrite the LLM planner yet | The first decision strategy should be deterministic, not model-dependent |
| `app/llm/retry_engine.py` | **Left unchanged intentionally** | No retry policy change yet | Retry only becomes meaningful after execution is wired |
| `app/context/dom_extractor.py` | **Modify** | Add an Agent Browser-backed helper that returns normalized interactive elements for a current page | Gives the selector helper real snapshot input inside the repo |
| `app/dom_schema.py` | **Modify** | Add temporary fields needed by deterministic selection, such as normalized element name/role lists | Lets the rest of the experiment reuse one stable snapshot shape |
| `app/execution/step_runner.py` | **Left unchanged intentionally** | Still no production path swap yet | Keep decision-layer work isolated |

#### Key Tasks
1. Create `app/browser/ref_selector.py`.
2. Implement a deterministic click-target selection policy:
   - exact name match
   - case-insensitive exact match
   - partial match
   - ambiguous target if multiple remain
3. Return a structured selection result containing:
   - chosen ref
   - candidate refs considered
   - selection reason
4. Add a tiny adapter helper that takes a target intent like `"Generate API Key"` and returns a structured ref-selection result.
5. Define two explicit experiment modes:
   - **Experiment Mode A: Deterministic only**
   - **Experiment Mode B: Deterministic + LLM fallback**
6. **Disable LLM fallback for baseline runs.** Baseline comparison must be run in Mode A only.
7. If Mode B is used later, log it explicitly so results are never mixed with deterministic-only runs.

#### Success Criteria
- Given a snapshot with one exact match, the selector returns that ref deterministically.
- Given multiple partial matches, the selector returns `ambiguous_target` instead of guessing.
- Given no candidates, the selector returns `no_match` cleanly.
- Selection output is structured enough to log why a ref was chosen.
- Baseline experiment runs can be executed with LLM fallback fully disabled.

#### Risks & Mitigation
- **Snapshot ambiguity**
  - Failure mode: multiple elements share the same accessible name and the selector picks the wrong one.
  - Mitigation: ambiguity is a first-class result; do not auto-click on ambiguous matches in this phase.
- **Underspecified target intent**
  - Failure mode: test cases use vague goals and the selector appears weaker than it is.
  - Mitigation: define target intents explicitly per test case, such as `"click Generate API Key"`.
- **Integration mismatch with current planner**
  - Failure mode: existing LLM steps still output `selector` / `text` and bypass ref selection.
  - Mitigation: keep this as a separate experiment path first; do not rewrite planner files in this phase.
- **Polluted experiment results**
  - Failure mode: deterministic and LLM-assisted runs are mixed together and we cannot tell whether wins came from snapshot quality or ranking logic.
  - Mitigation: enforce explicit Mode A vs Mode B runs and keep Mode A as the required baseline.

### Phase 3 — Step Runner CLI Path and Loop Control
#### Goal
Add a temporary Agent Browser execution path to the existing stepwise runner so the experiment can execute real UI loops in this repo.

This phase exists third because only after wrapper stability and deterministic selection are in place can we meaningfully test interaction quality end to end.

#### Files Impacted
| File | Action | Changes | Why |
|------|--------|---------|-----|
| `app/execution/step_runner.py` | **Modify** | Add a CLI-backed execution branch for open -> snapshot -> select ref -> click -> snapshot -> screenshot | This is the core experiment loop |
| `app/steps/step_execution.py` | **Modify** | Add an explicit feature flag or backend switch to call the Agent Browser experiment path | Lets us run A/B comparisons without removing the Playwright path |
| `app/context/dom_extractor.py` | **Modify** | Route current-page extraction through Agent Browser when the experiment backend is selected | Needed so runner and selector see the same snapshot semantics |
| `app/browser/agent_browser_cli.py` | **Modify** | Add any missing wrapper calls discovered during execution wiring, especially screenshot and wait helpers | Real execution will reveal missing wrapper pieces |
| `app/browser/ref_selector.py` | **Modify** | Wire selector into the runner | Connects planning to execution |
| `app/policy/selector_validator.py` | **Left unchanged intentionally** | Keep current selector validator untouched | This experiment bypasses selector guessing rather than mutating the existing validator too early |

#### Key Tasks
1. Add a backend switch in `app/steps/step_execution.py`, for example `browser_backend = "playwright" | "agent_browser_cli"`.
2. In `app/execution/step_runner.py`, add a temporary execution loop that:
   - opens preview URL
   - snapshots page
   - selects ref for target action
   - captures before screenshot
   - clicks ref
   - captures after screenshot
   - snapshots page again
3. Add hard loop controls:
   - `max_steps_per_run = 10`
   - `max_retries_per_step = 2`
   - fail if no matching ref is found
   - fail if the same action repeats without page change
4. Add success/failure state checks:
   - expected URL reached
   - expected text visible
   - expected element exists in post-action snapshot
5. After every click, require one of:
   - fixed wait of 1-2 seconds minimum, or
   - polling snapshots until a state change is detected
6. Define `state_change` as:
   - URL changed, or
   - snapshot diff detected
7. Define `wrong_click` explicitly:
   - the click action succeeds technically
   - but the expected success condition is not reached
8. Implement per-test-case success validation as structured expectations, not ad hoc judgment.
5. Keep the existing Playwright stepwise path intact for direct comparison.

#### Success Criteria
- The Agent Browser CLI execution branch can run a full step on a real preview URL.
- `click(ref)` produces an observable UI change on at least one core demo page.
- The loop stops deterministically on success, failure, or retry exhaustion.
- Existing Playwright execution path still works unchanged.
- Success is validated by explicit ground-truth checks, not by "the click ran without throwing".

#### Risks & Mitigation
- **Stale refs**
  - Failure mode: ref is valid in snapshot A but stale by click time.
  - Mitigation: treat stale ref as step failure, re-snapshot once, and re-run selection within `max_retries_per_step`.
- **CLI/execution timing mismatch**
  - Failure mode: click succeeds but post-click snapshot is taken before UI settles.
  - Mitigation: add explicit wait helpers in the wrapper and require either a fixed wait floor or snapshot polling until state change is detected.
- **Integration mismatch with existing step runner**
  - Failure mode: step runner assumes Playwright `Page` everywhere.
  - Mitigation: add a side-path function rather than trying to make the whole runner backend-agnostic in one pass.
- **Fake success**
  - Failure mode: action is counted as success because the command returned OK, even though the expected product state was not reached.
  - Mitigation: treat success as valid only when an explicit post-action validation condition passes.

### Phase 4 — Multi-Route Accuracy Validation and Baseline Comparison
#### Goal
Run the same flows through current Playwright logic and the new Agent Browser CLI path, then compare actual results rather than anecdotes.

This phase exists fourth because without baseline numbers, the experiment can feel better without proving anything.

#### Files Impacted
| File | Action | Changes | Why |
|------|--------|---------|-----|
| `app/steps/dom_crawler.py` | **Modify** | Add an Agent Browser-backed crawl path for targeted route snapshots used in the experiment | Lets us validate route-aware accuracy beyond a single page |
| `app/context/dom_extractor.py` | **Modify** | Support route-level extraction from Agent Browser snapshots | Needed for apples-to-apples comparison with current extraction |
| `app/execution/step_runner.py` | **Modify** | Record outcome counters and failure categories for both backends | Enables direct baseline comparison |
| `app/steps/step_execution.py` | **Modify** | Expose backend choice and test case identifiers in run output | Makes experiment runs comparable |
| `app/browser/experiment_logger.py` | **Create** | Aggregate per-run metrics and save comparison-ready traces | Central place for experiment reporting |
| `app/script_pipeline.py` | **Left unchanged intentionally** | Script-first path is not part of the baseline experiment | Avoids widening experiment scope |
| `app/recorder/playwright_runner.py` | **Left unchanged intentionally** | Video recording is not needed for baseline metrics | Keeps comparison focused on interaction accuracy |

#### Key Tasks
1. Define a small fixed test suite of real flows from this repo:
   - semantic button
   - navigation link
   - custom clickable element
   - ambiguous target case
   - post-navigation re-anchoring case
2. For each test case, define an explicit ground-truth success condition. Allowed success conditions are:
   - URL match
   - DOM contains expected text
   - specific element exists in post-action snapshot
3. Record those success conditions in structured test metadata so both backends use the same definition of success.
4. Record baseline metrics from the current Playwright stepwise flow:
   - success rate
   - retries per run
   - failure types
   - wrong-target click count
   - average step latency
5. Run the same suite through Agent Browser CLI-only.
6. Save comparison results in one structured artifact per run.
7. Review failures by artifact, not memory.

#### Success Criteria
- A baseline report exists for the current Playwright stepwise path.
- A matching report exists for the Agent Browser CLI path on the same flows.
- We can compare failure types and latency directly.
- At least one core path shows reduced selector-related or target-selection failures.
- Every reported success is backed by a test-case-specific ground-truth validation rule.

#### Risks & Mitigation
- **Noisy comparisons**
  - Failure mode: different test flows or page states make comparisons meaningless.
  - Mitigation: use fixed URLs, fixed intents, and fixed validation conditions for every run.
- **Route-level extraction mismatch**
  - Failure mode: route crawl and current-page extraction produce incompatible inputs.
  - Mitigation: normalize both through the same temporary schema before comparison.
- **False confidence from one good demo**
  - Failure mode: a single successful click is mistaken for general improvement.
  - Mitigation: require the full fixed test suite and compare aggregate metrics, not isolated wins.
- **Weak success definitions**
  - Failure mode: different engineers interpret "worked" differently during review.
  - Mitigation: allow only three success-condition types and force every test case to pick one before execution.

### Phase 5 — Tighten Validation, Document Findings, and Decide Go / No-Go
#### Goal
Turn the experiment into a clear engineering decision: continue integrating Agent Browser or stop.

This phase exists last because raw experiment output is not enough. We need explicit decision criteria, documented findings, and a bounded recommendation.

#### Files Impacted
| File | Action | Changes | Why |
|------|--------|---------|-----|
| `AGENT_BROWSER_INTEGRATION_PLAN.md` | **Modify** | Update results section or append findings summary after the experiment | Keeps the decision artifact in the repo |
| `app/browser/experiment_logger.py` | **Modify** | Emit final experiment summary and aggregated metrics | Converts raw traces into a readable decision output |
| `app/steps/step_execution.py` | **Modify** | Keep backend flag explicit and default to current Playwright path unless the experiment passes | Prevents accidental promotion of the experiment path |
| `app/execution/step_runner.py` | **Modify** | Add final outcome categories such as `passed`, `ambiguous`, `regressed`, `inconclusive` | Makes the experiment result machine-readable |
| `app/generator/script_generator.py` | **Left unchanged intentionally** | Script-first path remains out of scope | No need to entangle the experiment with script generation |
| `app/llm/step_generator.py` | **Left unchanged intentionally** | Keep the current planner stable unless the experiment clearly justifies changing it next | Avoids false attribution of improvements |

#### Key Tasks
1. Define explicit go / no-go thresholds:
   - Agent Browser performs at least as well as current stepwise flow on core paths
   - Agent Browser reduces target-selection failures
   - Agent Browser logs are sufficient to explain every failure
2. Aggregate experiment results into one summary:
   - pass/fail per test case
   - baseline vs Agent Browser metrics
   - top failure modes
   - ambiguous cases
   - results for Mode A (deterministic only) separately from any optional Mode B runs
3. Keep the feature flag default on Playwright unless thresholds are met.
4. Write a short findings summary into this document or a companion results doc.

#### Success Criteria
- There is a clear written answer to: "Is Agent Browser CLI-only materially better for grounding accuracy in this repo?"
- The answer is backed by saved metrics and artifacts, not impressions.
- The repo still defaults to the existing stable path unless the experiment passes.

#### Risks & Mitigation
- **Experiment over-interpretation**
  - Failure mode: modest accuracy gains are treated as a full architecture win.
  - Mitigation: limit the phase conclusion to CLI-only accuracy validation only.
- **Inconclusive results**
  - Failure mode: mixed results do not justify either stop or continue.
  - Mitigation: classify the outcome as `inconclusive` explicitly and identify which test cases need follow-up.
- **Changing too much after weak evidence**
  - Failure mode: planner or runner rewrites start before the experiment proves value.
  - Mitigation: keep `app/llm/step_generator.py`, `app/script_pipeline.py`, and script-first files unchanged unless Phase 5 passes.
- **Attributing gains to the wrong layer**
  - Failure mode: improvements from LLM fallback are mistakenly attributed to Agent Browser snapshot quality.
  - Mitigation: require the go / no-go decision to be based on Mode A deterministic results first.

#### Findings Summary And Decision Record
Phase 5 uses one aggregate artifact, `app/data/experiment_runs/experiment_summary.json`, as the source of truth for the decision.

Go / no-go thresholds:
- Agent Browser Mode A (`deterministic`) performs at least as well as Playwright on the paired core paths.
- Agent Browser Mode A reduces target-selection failures (`NO_MATCH`, `AMBIGUOUS`, `WRONG_CLICK`, `CLICK_FAILED`, `STALE_REF`).
- Every Agent Browser failure remains explainable from saved artifacts and categorized failure types.

Decision rule:
- `passed` = all Mode A thresholds are met. Promotion to Agent Browser may be considered.
- `ambiguous` = at least one Mode A test case remains ambiguous, so the result is not a clear win.
- `regressed` = Mode A underperforms Playwright on success rate or target-selection failures.
- `inconclusive` = the saved artifacts are insufficient or the results are mixed.

Current repository default (capture):
- Video pipeline runs **stepwise only** by default (`VIDEO_PIPELINE` unset or `stepwise`). Set `VIDEO_PIPELINE=script_first` to opt into the legacy Playwright script-first path.
- `run_capture` defaults to **Agent Browser CLI** (`run_ab_stepwise`). Set `BROWSER_BACKEND=playwright` to use Playwright stepwise instead (e.g. CI without the `agent-browser` binary).

Current status in this branch:
- The decision pipeline is implemented.
- The written recommendation remains `inconclusive` until real experiment artifacts are generated and reviewed.

## 9. Accuracy Test Cases
The validation should focus on the most failure-prone flows in this repo.

### Test case categories
#### 1. Simple semantic button
Example:

- page with a clearly labeled button like "Generate API Key"

Expected result:

- snapshot shows a stable ref
- click works without selector invention
- success condition example: page contains `"API Key created"` or post-action snapshot contains the created-key confirmation element

#### 2. Navigation element
Example:

- link or nav item that changes route

Expected result:

- click lands on the right page
- post-click snapshot reflects new page state
- success condition example: URL matches expected route

#### 3. Custom clickable element
Example:

- a `div`/`span`-style clickable UI or modern component surface

Expected result:

- `-C` cursor-interactive mode exposes it
- click succeeds even when current Playwright extractor would miss it
- success condition example: expected text or element appears after click

#### 4. Ambiguous text
Example:

- repeated labels such as multiple "Edit" or "Save"

Expected result:

- we can detect whether snapshot quality is sufficient to disambiguate
- if it is ambiguous, we record that clearly
- success condition example: run result is `AMBIGUOUS`, not silent failure or guessed click

#### 5. Post-navigation re-anchoring
Example:

- click causes route change or modal open

Expected result:

- second snapshot provides a usable next action surface
- success condition example: second snapshot contains the expected page heading, text, or element

## 10. Acceptance Criteria
Agent Browser CLI-only is considered successful for this phase if all of the following are true:

### Required
- It can be invoked reliably from Python in this repo.
- It can open the preview URL and return a usable interactive snapshot.
- It can click a chosen ref and produce the expected state change.
- It can produce screenshots/debug artifacts for inspection.

### Accuracy success criteria
- It reduces reliance on guessed selectors.
- It finds interactive elements that the current extractor misses.
- It performs at least as well as the current stepwise flow on core demo paths.
- It shows fewer target-selection failures on real pages.
- It produces enough logs and artifacts that a wrong action can be debugged after the fact.
- It distinguishes clearly between `success`, `wrong_click`, `ambiguous`, and `timeout` outcomes.

### Decision threshold
If Agent Browser CLI-only is clearly more accurate on the key demo paths, then it is worth deeper integration later.

If it is not materially better, we should stop before doing larger architecture work.

## 11. Risks In This Phase
### 1. Snapshot ambiguity
Even with Agent Browser, multiple elements may still have the same accessible name.

What to do now:

- record ambiguity explicitly
- do not over-engineer a full resolver yet
- treat ambiguity as a valid experiment outcome, not as silent failure

### 2. Missing elements in accessibility tree
Some controls may still be weakly represented.

What to do now:

- test with `-C` cursor-interactive mode
- inspect whether those controls become available
- explicitly allow "Agent Browser failed to capture the critical element" as a valid experiment outcome

### 3. CLI output shape issues
The CLI JSON/text output may need normalization before it fits the current code.

What to do now:

- keep the adapter thin
- normalize only what is needed for the experiment
- lock the minimal normalized schema used by downstream logic

### 4. Drift from current pipeline expectations
Current code expects `selector` / `text`-based actions.

What to do now:

- add a temporary ref-based path
- do not rewrite the whole planning system yet

## 12. Non-Goals
The following are explicitly **not** part of this document or this phase:

- daemon-backed architecture
- worker pool architecture
- per-job session manager
- SaaS control plane design
- fork/embedding Agent Browser
- long-term observability hardening
- final model-routing strategy
- replacing the full script-first pipeline

If those are needed later, they should be planned in a separate document after this accuracy validation succeeds.

## 13. Instrumentation & Experiment Logging
If we cannot debug a bad click, the experiment is not useful.

### Logger schema
Use a stable run log shape from day 1:

```json
{
  "run_id": "string",
  "backend": "playwright|agent_browser_cli",
  "mode": "deterministic|deterministic_plus_llm",
  "test_case_id": "string",
  "steps": [
    {
      "intent": "string",
      "candidates": [],
      "chosen_ref": "string",
      "selection_reason": "string",
      "result": "success|failure|ambiguous",
      "failure_reason": "string",
      "timing": {}
    }
  ],
  "final_outcome": "success|failure|ambiguous|inconclusive"
}
```

### Per-step logs
Capture the following for every step:

- `run_id`
- `step_index`
- backend used (`playwright` or `agent_browser_cli`)
- experiment mode (`deterministic` or `deterministic_plus_llm`)
- target intent
- raw snapshot JSON path
- normalized snapshot payload
- candidate refs considered
- chosen ref
- selection reason
- action attempted
- action result (`success` / `failure`)
- failure reason
- snapshot time
- click time
- total step time
- URL before action
- URL after action
- success condition checked
- state change detected (`true` / `false`)
- snapshot diff detected (`true` / `false`)

### Failure taxonomy
Every failure must be categorized as one of:

- `NO_MATCH`
- `AMBIGUOUS`
- `WRONG_CLICK`
- `CLICK_FAILED`
- `TIMEOUT`
- `STALE_REF`

Definitions:

- `WRONG_CLICK` = action succeeded technically, but expected state was not reached
- `CLICK_FAILED` = click command itself failed
- `STALE_REF` = ref was present in prior snapshot but invalid at action time

### Per-step artifacts
Save the following artifacts:

- `step_<n>_snapshot.json`
- `step_<n>_before.png`
- `step_<n>_after.png`

### Per-run artifacts
Save the following artifacts per run:

- `run_trace.json`
- `run_summary.json`

### Comparison against current Playwright flow
For the same fixed test flows, record the following for both backends:

- success rate
- average retries per run
- failure type counts
- wrong-target click count
- average step latency
- whether the post-action state matched expectation

The experiment is only useful if both backends are measured using the same test cases and the same output format.

Recommended file/module impact for instrumentation:

- Create `app/browser/experiment_logger.py`
- Modify `app/execution/step_runner.py`
- Modify `app/steps/step_execution.py`

## 14. Final Recommendation For Now
Use **Agent Browser CLI-only** right now to test accuracy in this codebase.

That means:

1. use the stock `agent-browser` binary
2. wrap it with a thin Python subprocess adapter
3. use an explicit deterministic ref-selection policy
4. run snapshot -> ref click -> snapshot validation loops with hard stop/retry rules
5. capture structured logs, snapshots, screenshots, and timing data
6. compare results directly with the current Playwright-based stepwise flow baseline

This is the correct plan for the current phase because it is the fastest way to determine whether Agent Browser is materially better for grounding and interaction accuracy in this repo.
