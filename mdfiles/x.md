# ShipVideo Engine Codebase Onboarding

## 1. What problem this system solves

This system turns a pull request into a demo video automatically.

The core product promise is:

1. Read the PR diff.
2. Infer what user-visible flow changed.
3. Open a preview deployment of the app.
4. Navigate the real UI and capture proof-backed screenshots of the changed flow.
5. Stitch those screenshots into a video.
6. Upload the video and comment back on the PR.

The code is optimized around one MVP goal stated in the repo instructions: **video accuracy over completeness**. That bias shows up everywhere:

- The system prefers proving that the changed UI was really reached over producing a flashy video.
- It aborts if capture is not "sendable".
- It uses a contract + validation model to reduce LLM hallucinations.
- It falls back to screenshot-only or changed-testid discovery modes instead of pretending it succeeded.

## 2. Overall architecture

At a high level the runtime is split into these layers:

### A. Trigger / API layer

- `app/webhook.py`
- `scripts/ci_pipeline.py`
- `app/trigger.py`
- `app/preview_url_resolver.py`
- `app/github_comment.py`

This layer decides **when** to run, resolves the preview URL, waits for it to become ready, and reports results back to GitHub.

### B. PR analysis and planning layer

- `app/steps/pr_extraction.py`
- `app/steps/contract_extraction.py`
- `app/steps/demo_contract.py`
- `app/steps/step_generation.py`
- `app/llm/step_generator.py`
- `app/llm/retry_engine.py`
- `app/steps/preflight.py`
- `app/steps/step_normalizer.py`
- `app/steps/diff_budget.py`
- `app/policy/selector_validator.py`

This layer answers: **What changed, what should the demo do, and how do we make the plan safe enough to execute?**

### C. DOM understanding layer

- `app/steps/dom_crawler.py`
- `app/context/dom_extractor.py`
- `app/dom_schema.py`

This layer provides the live UI inventory used to ground plans and validate LLM output.

### D. Execution layer

- `app/steps/step_execution.py`
- `app/execution/step_runner.py`
- `app/execution/navigation_detector.py`
- `app/browser/agent_browser_cli.py`
- `app/browser/ref_selector.py`
- `app/browser/agent_browser_types.py`

This layer turns planned steps into real browser actions and decides whether those actions actually demonstrated the intended feature.

### E. Render / output layer

- `app/render.py`
- `app/script_pipeline.py`
- `app/generator/script_generator.py`
- `app/recorder/playwright_runner.py`
- `app/recorder/video_processor.py`
- `app/storage.py`

This layer converts validated frames or recorded browser sessions into the final MP4 and publishes it.

### F. Runtime support layer

- `app/config.py`
- `app/config_types.py`
- `app/steps/metrics.py`
- `app/llm_guards.py`
- `observability/tracing.py`
- `observability/decorators.py`

This layer handles config, budgeting, metrics, tracing, dedupe, and run bookkeeping.

## 3. Main pipeline: input to output

The canonical path is:

### Step 1: GitHub event arrives

`app/webhook.py` is the main production entrypoint.

- `webhook()` verifies the GitHub HMAC signature with `verify_signature()`.
- It supports two trigger styles:
  - PR events (`opened`, `synchronize`, `reopened`, `ready_for_review`)
  - PR comment commands like `/glimpse`, parsed by `_parse_glimpse_command()`
- It loads `project_config.json` through `load_config()`.
- In smart mode it fetches the PR diff and counts UI-related changed lines before deciding whether to run.

Important design choice: after accepting the webhook, it starts a `Thread(target=background_job)` and returns immediately. So the HTTP request is decoupled from the long-running pipeline.

### Step 2: Resolve preview URL and wait for deploy readiness

Inside `background_job()` in `app/webhook.py`:

- `get_preview_url()` in `app/preview_url_resolver.py` fills a URL template using `pr_number` and/or `branch_slug`.
- `wait_for_preview_ready()` repeatedly sends HEAD requests until the preview responds or times out.
- If preview readiness fails, `comment_on_pr()` posts an error comment and the run stops.

### Step 3: Fetch and summarize the PR diff

`analyze_pr()` in `app/steps/pipeline.py` drives planning.

- It calls `fetch_pr_diff()` in `app/steps/pr_extraction.py`.
- `fetch_pr_diff()` hits `GET /repos/{repo}/pulls/{pr}/files`.
- Each file is stored as `{path, status, patch}`.
- Patch text is truncated to `MAX_PATCH_CHARS = 3000` per file.
- Pagination is capped at `MAX_PAGES = 50`.

This means the planner never sees the full raw diff for very large PRs; it sees a bounded summary.

### Step 4: Build a static demo contract

Still inside `analyze_pr()`:

- `extract_contract_static()` in `app/steps/contract_extraction.py` derives a `DemoContract`.
- That contract captures:
  - `start_route`
  - `targets` (`TargetRef`)
  - `terminal` (`TerminalCondition`)
  - `confidence`
  - extraction notes such as interaction hints

This is one of the most important ideas in the codebase. The contract acts as a **proof skeleton** that later planning must satisfy.

### Step 5: Trigger evaluation

`analyze_pr()` calls `evaluate_trigger()` from `app/trigger.py`.

- `auto`: run for any UI-relevant diff
- `smart`: only run if the UI diff magnitude is above a threshold
- `on-demand`: only run via comment command

If the trigger rejects the diff, the system returns a skipped flow with fallback steps.

### Step 6: Crawl live DOM context

If the trigger allows execution, `generate_steps_from_diff()` in `app/steps/step_generation.py` begins planning.

- It extracts seed routes from the diff using `_extract_routes_from_diff()` from `app/steps/step_normalizer.py`.
- It merges route hints from `project_config.json.routeMap`.
- It calls `crawl_dom_data()` from `app/steps/dom_crawler.py`.

`crawl_dom_data()` uses async Playwright to:

- discover routes from links on the page
- visit up to `max_routes=6`
- skip auth-wall redirects using `_is_auth_wall()`
- collect buttons, links, inputs, and `data-testid`s per route
- merge route snapshots into a global DOM view

The planner therefore sees both:

- a merged global DOM snapshot
- per-route UI inventories in `route_snapshots`

### Step 7: Budget and filter the diff for LLM consumption

Still in `generate_steps_from_diff()`:

- `budget_diff_files()` in `app/steps/diff_budget.py` sorts files by UI importance using `score_file()`
- primary UI files get more character budget than secondary files
- non-UI files are replaced with `"changed (non-UI, omitted)"`
- if the resulting diff text is still too large, `should_skip_llm_for_size()` in `app/llm_guards.py` may force a fallback

### Step 8: LLM extraction phase

`_run_extraction_phase()` in `app/steps/step_generation.py` asks the LLM for structured facts:

- `start_route`
- `terminal_testid`
- `click_labels`
- `interaction_hints`

If the static contract is already strong enough, extraction can be skipped and the contract becomes the source of truth.

The LLM is called through `_call_llm()` using JSON schema mode first and JSON object fallback second.

### Step 9: Upgrade the contract

After extraction:

- `_upgrade_contract_from_extraction()` merges extracted click labels into the contract.
- The contract is now a hybrid of:
  - static diff heuristics
  - LLM extraction

This hybrid contract is what the rest of planning uses.

### Step 10: Decide direct planning vs discovery mode

`generate_steps_from_diff()` branches here:

- If `_should_fallback_to_guarded_screenshot(contract)` is true, it enters **discovery mode**
- Otherwise it attempts a full LLM-generated demo plan

Discovery mode returns only:

- a `goto`
- a `screenshot`

but includes a rich `generation_context` with:

- `changed_testids`
- `start_route_candidates`
- contract
- extraction results
- DOM data
- `discovery_mode = True`

That context later enables Agent Browser changed-testid search.

### Step 11: LLM planning phase

If the contract is strong enough:

- `_build_planning_prompt()` constructs a strict prompt containing:
  - extracted journey facts
  - terminal condition
  - required click labels
  - route catalog
  - DOM-grounded routes and visible elements
  - app hints
  - previous preflight errors, if any
- The LLM returns:
  - `suggested_demo_flow`
  - `steps`
  - `narration`

This is the main "planner" call.

### Step 12: Ground, normalize, and preflight the plan

`generate_steps_from_diff()` then hardens the plan in several passes:

1. `_inject_terminal_assertion()`
2. `_inject_click_validation_from_terminal()`
3. `_inject_sequential_click_validations()`
4. `_validate_against_route_snapshots()`
5. `validate_steps()`
6. `normalize_steps()`
7. `_ensure_screenshots_for_visited_pages()`
8. `preflight_gate()`

If preflight fails:

- it retries planning once with error feedback
- if the failure is specifically zero-click degeneration, it synthesizes click steps from extraction labels via `_synthesize_click_steps()`

If normalization drops a validation condition that existed earlier, the code raises `ContractIntegrityError`. That is a deliberate guard against silently weakening the proof model.

### Step 13: Capture execution

`run_pipeline()` in `app/steps/pipeline.py` receives the steps and `generation_context`.

It first rejects a screenshot-only plan unless there is changed-testid recovery context. This is another explicit anti-fake-video guard.

Then it chooses between:

- `VIDEO_PIPELINE=script_first`
- default stepwise path

Even when script-first succeeds, the code currently treats it as **not proof-backed** and falls back to stepwise for sendable approval.

So the real production path is the stepwise capture path.

### Step 14: Stepwise capture path

`run_capture()` in `app/steps/step_execution.py` chooses browser backend:

- `playwright`
- `agent_browser_cli` (default)

It calls:

- `run_stepwise()` for Playwright
- `run_ab_stepwise()` for Agent Browser

### Step 15: Validate whether the capture is actually sendable

After capture, `run_capture()` calls `_build_render_approval()`.

A video is only sendable if:

- there are approved frames
- no wrong click was detected
- the target route was reached
- a changed target was shown
- some proof condition was satisfied

If any of these fail, the run is marked unsuccessful even if browser actions technically executed.

### Step 16: Render the MP4

If capture is sendable:

- `render_video()` in `app/render.py` takes the approved frame list
- it loops each frame for 3 seconds
- it uses ffmpeg to concatenate them
- it writes `app/screenshots/out.mp4`

Only approved frames are rendered, not every debug screenshot.

### Step 17: Upload and comment back

Finally `run_pipeline()`:

- uploads the MP4 to Cloudflare R2 via `upload_video()` in `app/storage.py`
- returns the public URL
- `app/webhook.py` posts the comment with `comment_on_pr()`

It also writes:

- `app/data/run_metrics/*.json`
- `app/data/pipeline_run_summary.json`

## 4. System design and data flow

### Data objects that matter most

The most important "contract" objects flowing through the system are:

- PR diff files: `List[Dict[path, status, patch]]`
- `DemoContract`
- DOM snapshot / route snapshots
- planned steps: `List[Dict[action, ...]]`
- `generation_context`
- execution result records
- render approval summary

### End-to-end data flow

The runtime data flow is:

`GitHub event`
-> `webhook.py`
-> preview resolution
-> diff fetch
-> static contract extraction
-> DOM crawl
-> LLM extraction
-> contract upgrade
-> LLM planning
-> DOM grounding + preflight
-> execution
-> validation
-> approved frames
-> render
-> upload
-> PR comment

### Asynchronous and event-driven behavior

There are three separate concurrency models in the code:

1. **Event-driven ingress**
   - GitHub webhook events start the pipeline.
   - Comment commands can manually force runs.

2. **Background threading**
   - `app/webhook.py` creates a background `Thread` so the HTTP handler can return quickly.

3. **Async Playwright**
   - `analyze_pr()` is async.
   - `crawl_dom_data()` uses `playwright.async_api`.
   - The webhook bridges async and sync by calling `asyncio.run(analyze_pr(...))` inside the background thread.

Execution itself is mostly synchronous after planning.

## 5. Major components

### 5.1 API / integration layer

#### `app/webhook.py`

Purpose:

- Main FastAPI app
- GitHub ingress
- background job launcher
- PR commenting and pipeline summary handling

Key functions:

- `webhook()`
- `verify_signature()`
- `get_video()`
- `budget_status()`

Connections:

- Calls `analyze_pr()` and `run_pipeline()`
- Uses `get_preview_url()`, `wait_for_preview_ready()`, `comment_on_pr()`, `load_config()`

Assumptions:

- GitHub webhook secret is configured
- preview URL template is valid
- GitHub token exists
- long-running background threads are acceptable in the deployment model

#### `scripts/ci_pipeline.py`

Purpose:

- Non-webhook execution path for CI

Key behavior:

- fetches PR metadata from GitHub
- resolves preview URL
- runs `analyze_pr()` and `run_pipeline()`
- posts result comment back to PR

Connections:

- Mirrors webhook behavior without FastAPI

Assumptions:

- CI environment has GitHub token and preview access

#### `app/preview_url_resolver.py`

Purpose:

- Turn PR metadata into a deploy URL
- wait until the preview responds

Key functions:

- `get_preview_url()`
- `wait_for_preview_ready()`

Assumptions:

- HEAD requests are enough to test readiness
- config template matches preview provider behavior

#### `app/github_comment.py`

Purpose:

- Post success or failure back to the PR

Assumptions:

- each run creates a new PR issue comment rather than updating an existing one

### 5.2 Trigger / filtering layer

#### `app/trigger.py`

Purpose:

- Decide whether a diff is UI-relevant and whether the pipeline should run

Key functions:

- `is_ui_file()`
- `score_file()`
- `evaluate_trigger()`

Connections:

- used by `analyze_pr()`
- also used indirectly by `diff_budget.py`

Assumptions:

- file path heuristics correlate with actual UI impact
- magnitude of diff is a useful proxy for demo worthiness

### 5.3 Pipeline orchestration layer

#### `app/steps/pipeline.py`

Purpose:

- Main orchestration hub between planning, capture, render, upload, and metrics

Key functions:

- `analyze_pr()`
- `run_pipeline()`

Connections:

- calls `fetch_pr_diff()`
- calls `extract_contract_static()`
- calls `evaluate_trigger()`
- calls `generate_steps_from_diff()`
- calls `run_capture()`
- calls `render_video()`
- calls `upload_video()`
- writes run metrics through `new_run_metrics()` and `write_run_metrics()`

Important behavior:

- converts empty or failed planning into fallback screenshot plans
- blocks screenshot-only plans unless changed-testid recovery context exists
- supports `VIDEO_PIPELINE=script_first` but still insists on proof-backed stepwise output
- computes and writes run-level metrics even on failure

Assumptions:

- orchestration can stay in-process
- capture backends return enough structured detail to compute render approval and run metrics

#### `app/job_runner.py`

Purpose:

- tiny convenience export that re-exports `run_pipeline()` and repo paths

Why it exists:

- it gives external callers a stable import surface without pulling in the whole app layout

### 5.4 Diff extraction and contract layer

#### `app/steps/pr_extraction.py`

Purpose:

- Pull changed files from the GitHub PR API

Key function:

- `fetch_pr_diff()`

Important behavior:

- truncates each patch
- caps pagination

Risk:

- very large or important diffs may lose crucial detail before planning ever starts

#### `app/steps/demo_contract.py`

Purpose:

- Defines the data model used to express a runnable demo

Key types:

- `TargetRef`
- `TerminalCondition`
- `DemoContract`

Important methods:

- `is_runnable()`
- `is_direct_plan_eligible()`
- `summary()`

#### `app/steps/contract_extraction.py`

Purpose:

- Build a first-pass contract directly from the diff using heuristics

Key functions:

- `extract_contract_static()`
- `_infer_start_route()`
- `_extract_targets()`
- `_detect_terminal()`
- `_extract_interaction_hints()`

Important design:

- route, targets, and terminal are extracted without LLM usage first
- confidence is determined from how complete that heuristic contract is

Assumptions:

- added diff lines contain enough route, copy, and terminal signal
- visible strings and `data-testid`s are good proxies for demo targets

### 5.5 DOM grounding layer

#### `app/steps/dom_crawler.py`

Purpose:

- Crawl the live app and collect route-level UI evidence

Key functions:

- `crawl_dom_data()`
- `_discover_routes()`
- `_build_visit_order()`
- `_extract_ui_from_current_page()`
- `_merge_snapshots()`
- `crawl_ab_routes()`

Connections:

- used by `generate_steps_from_diff()`

Assumptions:

- a small route sample is enough
- links reveal navigable routes
- auth walls can be detected from redirect path segments

Limitations:

- route discovery only sees routes exposed as `<a href>`
- dynamic navigation and hidden routes are missed
- forms and multi-step stateful pages are only partially represented

#### `app/context/dom_extractor.py`

Purpose:

- Extract live DOM context during execution

Key functions:

- `extract_dom_context(page)` for Playwright
- `extract_ab_context(cli)` for Agent Browser
- `merge_ab_route_snapshots()`

Connections:

- used by step runners and retry logic

#### `app/dom_schema.py`

Purpose:

- typed dict definitions for DOM and Agent Browser snapshot shapes

This file is small but important because it defines the vocabulary shared across planning and execution.

### 5.6 LLM planning and retry layer

#### `app/steps/step_generation.py`

Purpose:

- Main planner
- diff-to-demo conversion
- contract-aware LLM prompting
- preflight and normalization

This is the single most important planning file in the codebase.

Key functions:

- `_call_llm()`
- `_run_extraction_phase()`
- `_upgrade_contract_from_extraction()`
- `_route_snapshot_catalog()`
- `_validate_against_route_snapshots()`
- `_inject_terminal_assertion()`
- `_inject_click_validation_from_terminal()`
- `_inject_sequential_click_validations()`
- `_build_planning_prompt()`
- `generate_steps_from_diff()`

Important behavior:

- switches between direct planning and discovery mode
- augments the contract with LLM output
- forces terminal assertions and validation conditions into the plan
- runs preflight and regeneration loops
- aborts on proof-loss via `ContractIntegrityError`

Assumptions:

- the LLM can follow strict JSON schema and prompt constraints
- route-level DOM snapshots are enough to reject hallucinated steps

#### `app/llm/step_generator.py`

Purpose:

- Runtime replanning during execution

Key functions:

- `generate_next_steps()`
- `generate_single_step_toward_testid()`
- `find_ref_with_llm()`

Connections:

- used by `app/llm/retry_engine.py`
- then by execution runners

Difference from `step_generation.py`:

- `step_generation.py` plans the initial demo from diff + DOM
- `llm/step_generator.py` replans tactical next steps from the **current** DOM after execution failures

#### `app/llm/retry_engine.py`

Purpose:

- Wrapper around runtime step generation with validation feedback loops

Key functions:

- `regenerate_with_feedback()`
- `regenerate_single_step_toward_testid()`

Important behavior:

- generation is never trusted directly
- every generated step is revalidated against DOM before being accepted

### 5.7 Validation and proof layer

#### `app/steps/preflight.py`

Purpose:

- Validate that a planned flow satisfies the contract before execution

Key function:

- `preflight_gate()`

Checks:

- correct starting route
- required click targets present
- terminal assertion exists and matches
- every click has explicit proof condition
- plan is not degenerate
- prerequisite interaction hints are covered

#### `app/steps/errors.py`

Purpose:

- defines `ContractIntegrityError`

Why it matters:

- this is the planner's hard-stop signal for "we had a stronger proof-bearing plan earlier, but a later transformation weakened it"
- it protects against silent regressions in normalization or validation injection

#### `app/steps/step_normalizer.py`

Purpose:

- canonicalize step shapes and reject obviously invalid ones

Key functions:

- `validate_steps()`
- `normalize_steps()`
- `_extract_routes_from_diff()`
- `validate_against_dom()`

Important behavior:

- preserves validation-related passthrough fields
- rejects routes not in DOM
- drops unconfirmed click steps unless they are required contract targets

#### `app/policy/selector_validator.py`

Purpose:

- runtime selector safety checks

Key function:

- `validate_step_against_dom()`

Important rules:

- raw CSS is mostly forbidden
- semantic selectors like testid and aria-label are preferred
- label-based clicks must exist in DOM or on page

### 5.8 Execution layer

#### `app/steps/step_execution.py`

Purpose:

- Orchestrate capture using the chosen browser backend

Key functions:

- `_build_render_approval()`
- `run_capture()`

Important behavior:

- attaches inherited success conditions from experiment test cases
- routes to Playwright or Agent Browser
- computes `render_approval`
- can mark a run failed even after execution if proof criteria are not met

#### `app/execution/step_runner.py`

Purpose:

- Implements both stepwise executors

Main public functions:

- `run_stepwise()` for Playwright
- `run_ab_stepwise()` for Agent Browser

Key helper logic:

- `_run_ab_click_attempt()`
- `_evaluate_click_validation()`
- `_assert_ab_terminal_condition()`
- `_recover_ab_prerequisite_steps()`
- `_run_ab_changed_testid_search()`
- `_approved_frame_paths()`

Important design points:

- validation is differential: the proof must appear after the click, not before
- wrong clicks are explicitly classified
- stale refs are retried
- missing prerequisites can trigger on-the-fly replanning
- approved frames are carefully filtered

#### `app/execution/navigation_detector.py`

Purpose:

- detect meaningful page transitions for re-anchoring

Key functions:

- `capture_state()`
- `detect_major_change()`
- `wait_stable_after_navigation()`

Used heavily in Playwright stepwise mode.

#### `app/browser/agent_browser_cli.py`

Purpose:

- Thin wrapper around the `agent-browser` CLI

Key responsibilities:

- command execution
- snapshot normalization
- semantic find helpers
- screenshot capture
- network / console / error inspection

Important behavior:

- raises structured `AgentBrowserError`
- exposes semantic search methods like `find_testid_ref()`, `find_role_ref()`, `find_label_ref()`

#### `app/browser/ref_selector.py`

Purpose:

- Deterministic ref selection from Agent Browser snapshots

Key functions:

- `derive_intent()`
- `select_ref()`

Selection order:

- exact testid
- aria
- id
- exact visible name
- case-insensitive
- partial
- scored candidate match
- ambiguous / no match

This file is central to reducing bad clicks.

#### `app/browser/agent_browser_types.py`

Purpose:

- Typed structures for Agent Browser command results and selection results

#### `app/browser/experiment_logger.py`

Purpose:

- Benchmarking and promotion framework for backend experimentation

Why it exists:

- The repo is comparing Agent Browser against plain Playwright on fixed test cases.
- This is part of the system's accuracy-improvement workflow.

This is not the main user-facing pipeline, but it is strategically important for backend evaluation.

### 5.9 Render and media layer

#### `app/render.py`

Purpose:

- Convert approved screenshot frames into the final MP4

Key function:

- `render_video()`

Important behavior:

- refuses to render if `render_approval.is_sendable` is false
- scales and pads frames to configured viewport
- loops each frame for 3 seconds

#### `app/script_pipeline.py`

Purpose:

- Alternate script-first capture pipeline

Key function:

- `run_script_pipeline()`

Important reality:

- It can produce a video, but the main pipeline currently treats it as not proof-backed.
- So it is effectively a fallback / experimental branch, not the trusted output path.

#### `app/generator/script_generator.py`

Purpose:

- Ask the LLM to generate a Playwright script from the narrative flow

Key function:

- `generate_playwright_script()`

Important behavior:

- strict prompt around semantic locators only
- retry mode feeds back prior failing script and error

#### `app/recorder/playwright_runner.py`

Purpose:

- Execute generated Playwright Python code and record browser video

Key function:

- `run_script()`

Risk:

- it uses `exec(compile(script, ...))`, so generated code is executed directly inside the process

#### `app/recorder/video_processor.py`

Purpose:

- Convert recorded `.webm` output to `.mp4`

Key function:

- `convert_webm_to_mp4()`

### 5.10 Config, storage, observability

#### `app/config.py`

Purpose:

- Load `project_config.json`

#### `app/config_types.py`

Purpose:

- Turn config into `CaptureSettings`

#### `app/storage.py`

Purpose:

- Upload finished MP4s to Cloudflare R2
- monitor and clean old artifacts

Key functions:

- `upload_video()`
- `cleanup_old_videos()`
- `check_storage_usage()`

#### `app/steps/metrics.py`

Purpose:

- Write per-run JSON metrics for later analysis

#### `app/llm_guards.py`

Purpose:

- Monthly budget enforcement
- dedupe across same commit SHA
- local or Azure spend tracking

Key functions:

- `check_budget()`
- `record_spend()`
- `check_already_ran()`
- `record_run()`
- `should_skip_llm_for_size()`

#### `observability/tracing.py` and `observability/decorators.py`

Purpose:

- OpenTelemetry instrumentation
- colored step timing logs
- pipeline summary

Key behavior:

- `@pipeline_step` wraps most major pipeline stages
- tracing is no-op-exported today, so the repo gets structured spans without requiring a real backend

## 6. Core logic in depth

### 6.1 How PR diffs are processed

The diff pipeline is:

1. `fetch_pr_diff()` fetches file patches from GitHub.
2. `evaluate_trigger()` decides whether the diff matters.
3. `extract_contract_static()` heuristically extracts route, targets, terminal, and hints.
4. `budget_diff_files()` trims the diff for prompt use.
5. `_extract_changed_testids_from_diff()` in `step_generation.py` captures new `data-testid`s from added lines.

Why changed testids matter:

- they are the strongest evidence that a specific changed UI element exists
- discovery mode and Agent Browser search use them as proof targets

This is a strong design decision: the diff is not only used to tell a story, it is also used to derive **runtime validation markers**.

### 6.2 How the LLM is used

There are really four separate LLM jobs in this repo.

#### Job A: Extraction from diff

In `step_generation.py -> _run_extraction_phase()`

Goal:

- derive route, terminal marker, click labels, and prerequisite hints

Output shape is tightly constrained by `_EXTRACTION_JSON_SCHEMA`.

#### Job B: Main plan generation

In `step_generation.py -> generate_steps_from_diff()`

Goal:

- produce the full planned demo flow with steps and narration

Constraints:

- must use known routes
- must include required click labels
- must reach terminal condition
- must include screenshots
- must use visible labels or approved selectors

#### Job C: Runtime replanning

In `llm/step_generator.py -> generate_next_steps()`

Goal:

- recover from execution failures using only the current DOM

This is much more tactical than the main planner.

#### Job D: Single-step testid pursuit

In `llm/step_generator.py -> generate_single_step_toward_testid()`

Goal:

- choose one next action most likely to reveal a changed testid

This powers changed-testid recovery and discovery mode.

#### LLM safety model

The repo does not trust the LLM by default. Instead it uses a layered constraint system:

- JSON schema output
- DOM grounding
- selector policy validation
- normalization
- preflight checks
- runtime validation after each click

That is the core anti-hallucination strategy.

### 6.3 How browser automation is executed

There are two execution engines.

#### Playwright stepwise mode

`run_stepwise()`:

- loads preview URL
- extracts DOM context
- validates each step before execution
- executes with `_execute_one()`
- after navigation or major state changes, re-extracts DOM and optionally replans

This is simpler, but weaker in UI understanding than Agent Browser mode.

#### Agent Browser stepwise mode

`run_ab_stepwise()`:

- opens the preview in Agent Browser
- snapshots the UI before actions
- derives intent from the step
- resolves a ref via semantic search and deterministic selection
- checks actionability
- captures before and after screenshots
- validates the click against explicit proof conditions

Agent Browser mode has richer failure classification and recovery:

- scroll retry if no match
- stale ref retry
- prerequisite recovery
- changed-testid search mode
- terminal assertion via visible element, testid, URL, or snapshot text

### 6.4 How validation is performed after each step

Validation is one of the most important pieces of the system.

#### Planning-time validation

- `validate_against_dom()` checks that steps map to known routes or DOM targets
- `preflight_gate()` ensures the plan still satisfies the contract

#### Execution-time validation

For clicks, validation usually comes from `validation_condition` or `success_condition`.

Those conditions can be:

- `url_match`
- `text_present`
- `element_present`

`_evaluate_click_validation()` then checks:

- whether the condition already matched before the click
- whether it matches after the click

The click only passes if the signal appears **after** the action. That prevents a very common false positive where the target was already on screen.

#### Video-level validation

`_build_render_approval()` aggregates execution evidence and decides if the video is sendable.

This is stricter than step success alone. A run can execute steps successfully and still fail render approval if:

- wrong click happened
- changed target never appeared
- no approved frames were kept
- proof was never satisfied

### 6.5 How failures and hallucinations are handled

The repo treats failures as expected, not exceptional.

#### During planning

- budget exceeded -> fallback steps
- diff too large -> skip LLM
- preflight failed -> regenerate once
- zero-click plan -> synthesize from extracted labels
- validation condition lost during normalization -> `ContractIntegrityError`

#### During execution

- selector invalid -> regenerate from current DOM
- click execution failed -> regenerate from current DOM
- navigation changed page -> re-anchor plan
- no matching ref -> scroll retry or fail
- stale ref -> retry
- wrong click -> classify and reject video
- terminal not reached -> fail run

#### In low-confidence cases

- the planner does not force a fake multi-step flow
- it falls back to discovery mode and uses changed-testid search

That is a smart MVP decision because it prefers evidence over polish.

## 7. Robustness and edge cases

### Where the system can fail

#### 1. Diff understanding failures

Examples:

- the PR changes behavior without obvious route or label changes
- the important patch text is truncated
- the changed UI is mostly state logic, not visible strings or testids

Current handling:

- fallback narration / screenshot
- static contract confidence drops
- discovery mode may take over

#### 2. DOM crawl coverage failures

Examples:

- route is not linked from the initial page
- feature is behind auth, modal state, or feature flag
- important target lives on a route beyond the `max_routes=6` crawl budget

Current handling:

- route may be absent from planning context
- contract targets can survive as `contract_missing` clicks
- Agent Browser may recover at runtime if discovery mode or replanning helps

#### 3. LLM planning failures

Examples:

- missing click steps
- invalid routes
- selectors not in DOM
- premature stop before terminal state

Current handling:

- DOM grounding drops bad steps
- preflight rejects incomplete plans
- one retry is attempted with explicit error feedback
- zero-click plans can be synthesized from extracted labels

#### 4. Runtime selection failures

Examples:

- multiple matching "Edit" buttons
- UI changed and ref became stale
- element is visible but disabled
- visible text differs slightly from inferred intent

Current handling:

- `select_ref()` can return `ambiguous`
- stale refs are retried
- pre-click actionability checks block disabled / hidden elements
- partial / scored matching tries to salvage safe cases

#### 5. Proof failures

Examples:

- the step clicked something, but not the intended changed UI
- terminal state never appears
- screenshot exists but no evidence of the changed target exists

Current handling:

- click marked `wrong_click`
- render approval rejects the run
- screenshots from failed clicks can be discarded

#### 6. Infra failures

Examples:

- preview not ready
- GitHub token missing
- Azure OpenAI unavailable
- ffmpeg missing
- R2 misconfigured
- `agent-browser` binary missing

Current handling:

- most of these raise explicit errors
- webhook posts failure comment back to PR

### Common failure modes in practice

The most likely real-world failure modes are:

1. UI changed but `data-testid` and visible labels are weak, so the contract is low-confidence.
2. The planner finds a plausible flow but the changed UI was already visible before the click, causing validation to fail.
3. The DOM crawler misses the real route because the app uses client-side navigation without crawlable links.
4. The UI has repeated labels, causing ambiguous target selection.
5. Preview deploy is not ready or points to a stale environment.

## 8. Design tradeoffs

### Why this architecture likely was chosen

#### Contract-first planning

The combination of:

- static diff heuristics
- LLM extraction
- preflight
- runtime proof conditions

is a practical way to keep the LLM useful without making it authoritative.

That is exactly the right architecture for an MVP where accuracy matters more than coverage.

#### Stepwise execution over pure generated scripts

The repo clearly moved away from trusting a monolithic generated Playwright script. Stepwise execution allows:

- per-click validation
- mid-run recovery
- frame-level approval
- explicit wrong-click rejection

That is much better for accuracy.

#### Agent Browser as default

The code prefers `agent_browser_cli` over plain Playwright because the product problem is not "can we automate a browser", it is "can we select the correct UI target reliably even in messy DOMs". Agent Browser gives richer semantic snapshots and ref-based interaction.

### Current limitations

#### 1. Route discovery is shallow

It mostly follows visible links and caps route crawling aggressively.

#### 2. The contract heuristic is still brittle

`extract_contract_static()` relies heavily on added strings, visible text, and `data-testid`s.

#### 3. Background threading inside FastAPI is operationally simple but not ideal

There is no durable job queue, retry worker, or external state machine.

#### 4. Script-first path is not trusted

It exists, but the code itself admits it is not proof-backed.

#### 5. The video renderer is minimal

The final video is just approved screenshots looped into MP4. There is no narration, audio mixing, or cinematic sequencing in the trusted path yet.

#### 6. A lot of run state is file-based

Spend, dedupe, metrics, experiment logs, and summaries are local JSON files.

### What I would improve if scaling this system

#### 1. Replace the background thread with a durable job system

Use a queue and worker model so runs are resumable, retryable, and observable across process restarts.

#### 2. Make the contract a first-class persisted artifact

Right now the contract is powerful but transient. Persist it, diff it, and surface it in PR comments or run dashboards.

#### 3. Expand DOM crawl coverage using app-aware navigation recipes

Relying on links is not enough for serious apps. Add configurable crawl recipes for tabs, menus, drawers, and auth bootstrap.

#### 4. Strengthen proof beyond visible text

Use:

- screenshot similarity diffs
- DOM subtree diffs
- network event expectations
- route-specific assertions

to make validation more resilient.

#### 5. Separate demo planning from proof planning

The current planner mixes narrative and proof structure. I would eventually split it into:

- a proof plan
- a presentation plan

and render only presentation steps that are backed by proven milestones.

#### 6. Introduce per-app adapters

The generic engine is good for MVP, but scale will come from app-specific knowledge:

- login/bootstrap
- route maps
- stable success markers
- feature area recipes

#### 7. Add a run review UI

Because accuracy is the main KPI, the team will eventually want a UI showing:

- planned contract
- executed steps
- before/after snapshots
- why a video was rejected

That would accelerate debugging far more than raw logs.

## 9. Peripheral files and what to ignore at first

These files are useful but not on the main runtime hot path:

- `README.md`: one-paragraph product description
- `run.sh`: local dev convenience wrapper for uvicorn
- `project_config.json`: repo-local runtime config
- `cleanup_r2.py`: manual storage cleanup helper
- `doppler.py`, `export_secrets.py`, `export_secrets.sh`: local secret helpers
- `mdfiles/*.md`, `docs/*.md`, `openspec/*`: design docs and planning docs, not runtime
- `test_*.py`: small validation scripts / tests

For onboarding, new engineers should start with:

1. `app/webhook.py`
2. `app/steps/pipeline.py`
3. `app/steps/step_generation.py`
4. `app/steps/preflight.py`
5. `app/execution/step_runner.py`
6. `app/steps/step_execution.py`
7. `app/steps/dom_crawler.py`
8. `app/browser/agent_browser_cli.py`
9. `app/storage.py`
10. `observability/*`

## 10. Final mental model

The cleanest way to think about this system is:

- **GitHub + preview layer** decides when and where to run.
- **Diff + contract layer** decides what changed and what proof the video must show.
- **DOM + planning layer** turns that into a bounded, grounded execution plan.
- **Execution layer** tries to realize the plan while proving each action changed the UI in the expected way.
- **Render layer** only publishes evidence-backed frames.

This is not a generic video generator. It is a **proof-constrained PR demo engine**.

That distinction explains nearly every design choice in the repo.
