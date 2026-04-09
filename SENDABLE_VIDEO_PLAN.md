# 5-Phase Plan For Accurate Sendable Videos

The only goal: every generated video must be sendable by a PM to a customer without embarrassment.

Ignore metrics, observability, narration quality, and infrastructure.

Focus only on the things that make videos wrong today.

## Phase 1: Demote Diff-Derived Plans From Truth To Hints

### Files to change
- `app/steps/pipeline.py`
- `app/steps/step_generation.py`
- `app/steps/contract_extraction.py`
- `app/steps/demo_contract.py`

### Delete
- The assumption that static contract extraction is a trustworthy source of the real demo flow
- Any path that treats extracted targets, start route, or terminal conditions as sufficient to drive a sendable video
- Any fallback that turns weak extraction into a runnable default plan beyond a guarded screenshot fallback

### Add
- A stricter contract model with confidence meaning:
  - `high`: directly grounded in changed route plus changed visible target plus proof condition
  - `medium`: partially grounded, usable only as hinting input
  - `low`: non-runnable
- A rule in pipeline generation:
  - low-confidence contracts cannot produce multi-step demo videos
  - medium-confidence contracts can only seed exploration
  - only high-confidence contracts can seed a direct planned flow
- A reduced contract payload centered on:
  - candidate routes
  - candidate changed targets
  - candidate proof conditions

### Passing run
- The system does not pretend the diff already told it the customer-safe flow.
- Generated steps are treated as hypotheses that must still be proven in the browser before video approval.

## Phase 2: Stop Accepting Unproven Actions

### Files to change
- `app/steps/preflight.py`
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`

### Delete
- The default `state_changed` success fallback added in `app/steps/preflight.py`
- Any execution path that treats a click as acceptable when it is only `unvalidated`
- Any success path that allows a run to continue to final video output after a `wrong_click`

### Add
- A strict rule that every click step must carry an explicit proof condition:
  - `url_match`
  - `text_present`
  - `element_present`
- A hard failure when a click finishes without proof
- A hard failure when validation says the click changed the page in the wrong way
- A final gate in execution that only returns success when all kept frames come from validated steps

### Passing run
- The run either produces a video where every shown interaction is explicitly proven correct, or it fails and produces no sendable video.

## Phase 3: Replace Full-Flow LLM Planning With Evidence-Backed Search

### Files to change
- `app/steps/step_generation.py`
- `app/llm/step_generator.py`
- `app/llm/retry_engine.py`
- `app/execution/step_runner.py`

### Delete
- Full-flow LLM generation as the primary source of the demo path
- LLM retry behavior that invents prerequisite steps from a thin DOM snapshot
- Any regeneration loop that can change the story of the demo without new browser evidence

### Add
- A two-layer flow:
  - deterministic route and target search first
  - LLM ranking only when choosing among browser-observed candidates
- The deterministic search works as follows:
  - 1. Extract changed testids from diff using regex on added lines only
  - 2. Open browser at start route
  - 3. Call `cli.find_testid(testid)` for each changed testid
  - 4. If found: `scrollintoview`, screenshot, click, screenshot. That is one milestone
  - 5. If not found: call `cli.snapshot()` and pass `interactive_elements` to LLM with one question only: what single action gets me closer to testid X
  - 6. Execute that one action, then repeat from step 3
  - 7. Cap at 8 total actions before declaring flow unreachable
- A browser-evidence payload for replanning that includes:
  - current route
  - visible interactive elements
  - recently changed UI text
  - available dialogs or drawers
  - candidate proof elements
- A restricted LLM task:
  - choose next action only from observed candidates
  - never invent routes
  - never invent labels
  - never invent prerequisite actions not present in browser evidence
- A rule that replanning can only continue toward the same target proof condition, never switch the demo objective mid-run

### Passing run
- When the original flow is incomplete, recovery only picks from things that are actually present in the browser and still ends at the same proven feature outcome.

## Phase 4: Upgrade Browser State Understanding Before Clicking

### Files to change
- `app/context/dom_extractor.py`
- `app/browser/ref_selector.py`
- `app/browser/agent_browser_types.py`
- `app/browser/agent_browser_cli.py`
- `app/execution/step_runner.py`

### Delete
- Flat, shallow element snapshots as the main understanding layer
- Selection behavior that relies mostly on exact or partial text match without enough structural context
- Any click targeting path that cannot explain why one matching element is safer than another

### Add
- Rich browser state capture for:
  - dialogs
  - drawers
  - tabs
  - forms
  - visible headings
  - testids
  - aria labels
  - current route
  - nearby text around interactive elements
- Ref selection scoring that prefers:
  - changed testids
  - exact accessible labels
  - elements inside the active surface
  - elements near changed copy
- Ambiguity handling that stops instead of guessing when multiple candidates remain plausible
- A pre-click safety check that confirms the chosen element belongs to the intended surface and expected route

### Passing run
- The runner either clicks the clearly right element in the clearly right UI surface, or refuses to proceed.

## Phase 5: Only Render Videos From Proven Milestones

### Files to change
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/steps/pipeline.py`
- `app/render.py`

### Delete
- Any path that allows screenshots or clips from unvalidated or wrong interactions into the final customer-facing video
- Any final assembly behavior that treats “runner completed” as good enough for sendability

### Add
- A milestone model where a frame is eligible for final render only if it comes after:
  - validated navigation
  - validated interaction
  - validated terminal proof
- A final approval gate before rendering:
  - target route reached
  - expected changed target shown
  - expected proof condition satisfied
  - no wrong click included in kept sequence
- A strict fallback behavior:
  - if proof is incomplete, do not render a sendable feature demo

### Passing run
- The final video contains only proven, customer-safe states and visibly demonstrates the changed feature without any guessed or embarrassing interactions.
