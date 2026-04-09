# Video Accuracy Implementation Plan

This document lists the implementation work that directly improves the two core problems:

1. Problem 1 — What to record
2. Problem 2 — How to record it reliably

Everything here is selected for direct impact on video accuracy. This is not a general roadmap.

## Goal

Produce sendable demo videos that:

- record the correct user-visible flow
- execute the intended steps reliably
- prove the intended UI state was reached

## Non-Goals

Do not spend time on these until the two core problems are solved:

- better narration quality
- prettier rendering
- broader PR coverage
- autonomous full-agent exploration
- generic diff-to-demo automation without a manifest

## Current Reality

The current system has two structural weaknesses:

1. We still infer too much from the diff.
2. We do not yet have a single deterministic source of truth for recorded flows and proof.

That means:

- the planner can generate the wrong flow
- discovery mode can produce `goto + screenshot` placeholder plans
- proof can fail for the wrong reason because the flow never really executed
- execution is forced to compensate for weak planning

## Core Strategy

The right architecture is:

1. choose the flow from a manifest or explicit developer intent
2. execute a deterministic stored flow
3. validate each step with explicit proof
4. only use AI for bounded resolution or repair

## Problem 1 — What To Record

### P1.1 Create a real manifest system

Implement a first-class manifest for each app/environment.

Minimum manifest fields:

- app id
- base url
- routes
- named flows
- start route
- ordered structured steps
- per-step success conditions
- terminal condition
- flow metadata

Each flow must be stored as structured data, not freeform text.

Example shape:

```json
{
  "name": "Recharge account from settings",
  "start_route": "/settings",
  "steps": [
    {
      "action": "click",
      "label": "₹2000",
      "success_condition": {
        "type": "text_present",
        "value": "Recharge Now"
      }
    },
    {
      "action": "click",
      "label": "Recharge Now",
      "success_condition": {
        "type": "text_present",
        "value": "Proceed"
      }
    }
  ],
  "terminal_condition": {
    "type": "text_present",
    "value": "Recharge Successful"
  }
}
```

### P1.2 Use `shipvideodemo.json` as the first real manifest

Do not keep it as a passive reference file.

Implement:

- manifest loader
- manifest validator
- manifest-to-step-plan conversion
- terminal condition parser

Direct accuracy benefit:

- removes diff-only flow guessing
- gives the pipeline one deterministic truth source

### P1.3 Add developer intent as a manifest selector, not a flow generator

Support:

```text
/demo show the security verification flow in settings
```

This should:

- map to a manifest flow
- or narrow to a small set of candidate flows
- or ask one targeted follow-up if ambiguous

Do not let developer intent generate arbitrary new multi-step flows by default.

Direct accuracy benefit:

- developer points at the right experience
- system resolves to a known executable flow

### P1.4 Add flow selection confidence and hard failure rules

Implement selection outcomes:

- `selected_exact_flow`
- `selected_candidate_flow`
- `ambiguous_flow_request`
- `no_matching_flow`

If the system cannot select a flow with high confidence:

- fail before execution
- post a clear request for clarification

Direct accuracy benefit:

- avoids recording the wrong thing

### P1.5 Stop treating diff inference as the primary source of truth

Keep diff analysis only for:

- trigger decisions
- suggesting likely flows
- extracting changed testids
- optional fallback hints

Do not use diff analysis to invent the full user journey as the default production path.

Direct accuracy benefit:

- avoids a failure mode that will never be consistently reliable on arbitrary PRs

### P1.6 Build a manifest builder job

Implement a separate job that:

- crawls known routes
- snapshots pages
- extracts buttons, links, inputs, testids, headings, surfaces
- stores them in the manifest

This job should be independent from the PR recording run.

Direct accuracy benefit:

- planner and executor stop operating on ad hoc one-run context

### P1.7 Store successful runs back into the manifest

When a flow executes successfully:

- persist the final proven step sequence
- persist the proof conditions that passed
- persist the runtime locator evidence that worked

Direct accuracy benefit:

- successful execution becomes reusable ground truth

## Problem 2 — How To Record It Reliably

### P2.1 Make explicit deterministic flows the only success path

The recorder should treat these as required for a sendable run:

- start route
- click steps
- per-step proof
- terminal proof

Do not allow:

- screenshot-only plans
- discovery placeholders
- sendable output from `goto + screenshot`

Direct accuracy benefit:

- avoids videos that never actually executed the feature

### P2.2 Keep the strict element matching priority order

Element resolution should stay in this order:

1. exact `data-testid`
2. exact `aria-label`
3. exact visible text
4. position + element type as last resort

Never reintroduce fuzzy generic matching as the primary path.

Direct accuracy benefit:

- reduces wrong clicks and hidden ambiguity

### P2.3 Add real position-aware fallback

Current fallback is not real geometry.

Implement:

- element bounding box retrieval
- relative ordering by visible layout
- role-aware positional fallback

Direct accuracy benefit:

- makes the last-resort fallback real instead of pseudo-positional

### P2.4 Re-query the browser state after every action

After each click:

- re-snapshot
- re-evaluate available targets
- capture snapshot diff
- capture screenshot diff when useful

Do not rely on stale pre-click assumptions for the next step.

Direct accuracy benefit:

- each step is grounded in the current UI state

### P2.5 Require explicit proof after every meaningful click

Every click step must have a `success_condition`.

Allowed types:

- `text_present`
- `url_match`
- `element_present`
- later: `function_true`

If a click has no proof condition:

- it is not production-ready
- it should fail validation

Direct accuracy benefit:

- transforms clicks from guesses into validated state transitions

### P2.6 Add stronger wait primitives for proof

Replace generic settling with condition-specific waits:

- wait for text
- wait for url
- wait for element
- wait for app-specific DOM function

Do not rely on `networkidle` as the main proof of correctness.

Direct accuracy benefit:

- reduces timing-based false failures

### P2.7 Use Agent Browser as an evidence engine, not just a click wrapper

Expand the Agent Browser wrapper to support:

- annotated screenshots
- snapshot diff
- screenshot diff
- bounding boxes
- trace capture
- richer wait conditions
- batch command execution where appropriate

Direct accuracy benefit:

- improves debugging
- improves proof quality
- improves position-aware fallback

### P2.8 Capture full failure evidence on proof failure

On any proof failure, store:

- before screenshot
- after screenshot
- annotated screenshot
- raw snapshot
- snapshot diff
- current url
- console messages
- page errors
- network requests
- matched target markers
- failed proof condition

Direct accuracy benefit:

- makes false failures diagnosable
- speeds correction of broken steps and proofs

### P2.9 Separate generation failure from execution failure

Implement distinct failure classes:

- `flow_selection_failed`
- `manifest_resolution_failed`
- `generation_failed`
- `execution_failed`
- `proof_failed`

Do not collapse these into one generic pipeline failure.

Direct accuracy benefit:

- prevents debugging the wrong layer

### P2.10 Make proof gating aware of plan type

If a plan is discovery-only or placeholder:

- fail before capture
- never allow proof/sendability checks to classify it as a valid execution attempt

Direct accuracy benefit:

- eliminates misleading proof failures for flows that never executed

### P2.11 Add deterministic replay benchmarks

For every production flow, add a benchmark that runs the same flow repeatedly.

Initial exit criterion:

- explicit deterministic flow passes at least 19 out of 20 runs on the demo app

Track:

- success rate
- wrong clicks
- unvalidated clicks
- proof failures
- average latency

Direct accuracy benefit:

- gives a hard reliability target instead of intuition

### P2.12 Store known-good locator history

For each step, store:

- successful selector or locator type
- matched runtime element metadata
- surface or section
- proof that passed

Use this as the first replay path before any repair logic.

Direct accuracy benefit:

- turns successful runs into a reusable stable execution layer

## Immediate Implementation Order

This is the order that gives the highest direct improvement to accuracy.

### Phase 1 — Stop Wrong Recording Decisions

Implement first:

- manifest loader
- manifest validator
- use `shipvideodemo.json` as the first real manifest
- developer intent as flow selector
- hard fail on ambiguous or missing flow selection
- remove diff inference as the primary flow source

Expected effect:

- the system records the intended flow more often

### Phase 2 — Stop Placeholder And Fake Success Paths

Implement:

- no sendable capture from screenshot-only or discovery-only plans
- explicit failure type for generation/discovery placeholder plans
- per-step proof required

Expected effect:

- the system stops pretending a non-executed flow is a valid run

### Phase 3 — Harden Execution

Implement:

- strict locator priority
- real position-aware fallback
- re-snapshot after every click
- explicit waits for proof
- stronger Agent Browser evidence capture

Expected effect:

- explicit flows execute more reliably

### Phase 4 — Add Repeatability

Implement:

- 20-run replay benchmark
- stored known-good step history
- benchmark dashboard or run summary

Expected effect:

- reliability becomes measurable and enforceable

## Work That Should Not Be Prioritized Yet

These are lower value until the above is done:

- more advanced LLM prompting
- more diff heuristics
- broader autonomous discovery
- script narration improvements
- video styling improvements
- generalized multi-app inference without a manifest

## Recommended Success Criteria

The system is materially improved when all of these are true:

- flow selection comes from manifest or explicit developer intent
- no discovery placeholder plan is treated as a real demo flow
- every click has explicit proof
- proof failures are diagnosable from stored evidence
- known demo flows pass 19/20 repeated runs
- successful runs update the manifest and locator history

## Highest-Value Single Principle

Do not guess both the flow and the execution at the same time.

Choose one source of truth for the flow, then make execution and proof deterministic around it.
