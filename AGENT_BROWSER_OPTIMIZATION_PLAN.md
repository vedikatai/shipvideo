# Agent Browser Optimization & Maximum Grounded Accuracy

## Purpose
This document is the **Part 2** companion to [`AGENT_BROWSER_ACCURACY_REMEDIATION.md`](AGENT_BROWSER_ACCURACY_REMEDIATION.md).

- **Remediation doc** — concrete backlog to fix integration gaps (validation, logging, Phase 5, normalization, benchmarks).
- **This doc** — how to use Agent Browser closer to its full strength for **more accurate steps and video demos**, including code-aware planning, replanning, grounded ranking, and paths toward very high measured accuracy.

---

## System mental model (ideology)

This is the framing we use so **step generation**, **execution**, and **backend choice** do not get conflated.

### Two different failure modes (measure them separately)

| Layer | What it does | Typical failure | What improves it |
| ----- | ------------- | ----------------- | ----------------- |
| **Step generation** | LLM + DOM/PR context → ordered steps (`goto`, `click`, `screenshot`) | Wrong route, invented label, bad selector, plan from stale page | Stronger `generation_context`, schema + validation, replanning after navigation, code-aware hints — **not** “switch to Agent Browser” by itself |
| **Step execution (stepwise)** | Turn each step into real clicks on the live preview | No match, wrong element, flaky selector | **Agent Browser** (snapshot + refs + optional selector fallback); **Playwright** stepwise when `agent-browser` is unavailable (e.g. CI) |

**Agent Browser does not fix bad plans.** It improves **grounding**: what is actually on the page and what to click. If the plan says the wrong thing, execution can still “succeed” at the wrong action unless you add explicit post-click validation (see remediation doc).

### One planner, multiple ways to run capture

- **Same step list** is produced by analyze / step generation regardless of executor.
- **Stepwise capture** (default in this repo): `run_capture` → either **`run_ab_stepwise`** (Agent Browser CLI) or **`run_stepwise`** (Playwright), controlled by `BROWSER_BACKEND`. Same JSON steps; different execution engine.
- **Script-first** (`VIDEO_PIPELINE=script_first`): optional **Playwright** path that runs a **generated script** for smoother video — a different shape than stepwise; not the same as “Playwright stepwise.”

So: we are **not** “only Agent Browser everywhere.” We use **Agent Browser as the default for stepwise execution** where the binary exists, because it matches the **click accuracy** problem best; Playwright remains the **portable fallback** and the **script-first** recorder.

### Where to look in code

- Steps: `app/steps/step_generation.py`, `app/llm/step_generator.py`, `app/steps/pipeline.py` (`analyze_pr`).
- Stepwise execution: `app/steps/step_execution.py` (`run_capture`), `app/execution/step_runner.py` (`run_ab_stepwise` / `run_stepwise`).
- Integration plan defaults: `AGENT_BROWSER_INTEGRATION_PLAN.md` (current repo defaults section).

---

## Part 2: How To Use Agent Browser More Fully

## What "Using Agent Browser's Power" Actually Means
Right now the system uses Agent Browser as:

- a good snapshot provider
- a grounded click executor
- a deterministic ref-based side path

That is useful, but it is still a narrow usage pattern.

To use Agent Browser closer to its full strength, the system should let Agent Browser provide the grounding surface for:

- what is clickable now
- what changed after the click
- which next action is likely correct
- whether the current state matches the intended user journey

The current pipeline still relies too much on:

- pre-generated steps that may be stale
- text/selector assumptions made before the live snapshot
- generic state change instead of product-aware validation

The most accurate version is a hybrid system:

1. code-aware planning determines what changed and what user journey matters
2. Agent Browser supplies the live action surface and state evidence
3. a grounded policy chooses the next action from real refs
4. an explicit validator confirms expected product outcome before continuing
5. the system replans after each meaningful UI transition

## Target Accuracy Strategy
Reaching a real `99%` across arbitrary modern web apps is not realistic with the current architecture.

What is realistic:

- `95%+` on a fixed suite of well-defined core flows
- near-`99%` on constrained, repeatedly tested demo journeys with explicit success checks, route hints, and strong artifacts

The plan below is how to move toward that range.

## Improvement Plan For Maximum Grounded Accuracy

### Step 1: Make Planning Code-Aware Before The Browser Runs

#### Concrete Steps
- Expand PR analysis so it maps changed files to likely affected routes, components, and user actions
- Extract symbols and UI labels from changed frontend files
- Feed that context into step generation so the planner starts with strong hypotheses

#### What It Will Improve
- Better initial steps
- Fewer irrelevant clicks
- Better route targeting

#### How We Will Solve It
- Enrich `generation_context` with:
  - changed routes
  - changed component names
  - changed CTA labels
  - likely user journeys
- Use that data to constrain the first browser actions

#### What Files We Can Change To Reach That Accuracy
- `app/steps/pipeline.py`
- `app/steps/step_generation.py`
- `app/steps/dom_crawler.py`
- `app/context/dom_extractor.py`
- `app/llm/step_generator.py`

#### Expected Accuracy Increase
- `10-20%` better first-action precision on PR-driven flows

---

### Step 2: Replan After Every Major UI Transition

#### Concrete Steps
- After each successful click that changes route or opens a modal, regenerate only the remaining steps using the fresh Agent Browser snapshot
- Stop treating the original step list as fully authoritative

#### What It Will Improve
- Better recovery after navigation
- Better handling of modals, drawers, tabs, and conditional UI
- Less drift between planned steps and live page state

#### How We Will Solve It
- Add a replan boundary in the Agent Browser runner similar to the Playwright stepwise re-anchor pattern
- Feed the new snapshot plus journey goal back into the planner

#### What Files We Can Change To Reach That Accuracy
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/llm/step_generator.py`
- `app/llm/retry_engine.py`

#### Expected Accuracy Increase
- `10-25%` on multi-step and post-navigation flows

---

### Step 3: Upgrade Ref Selection From String Matching To Grounded Ranking

#### Concrete Steps
- Keep deterministic exact matching first
- Then add a grounded reranker using:
  - accessible name
  - role
  - nearby context
  - route context
  - changed-code hints
  - historical success on similar flows

#### What It Will Improve
- Better selection when labels are close but not identical
- Better handling of repeated buttons and non-obvious CTA naming

#### How We Will Solve It
- Extend `SelectionResult` with ranked candidates and scores
- Add a second-pass ranking policy for non-exact matches
- Keep ambiguity as a valid outcome when confidence is still too low

#### What Files We Can Change To Reach That Accuracy
- `app/browser/ref_selector.py`
- `app/browser/agent_browser_types.py`
- `app/context/dom_extractor.py`
- new file: `app/browser/ref_ranker.py`

#### Expected Accuracy Increase
- `10-20%` on real-world messy UIs

---

### Step 4: Replace Generic Success Detection With Product-Aware Validators

#### Concrete Steps
- Add validation for:
  - expected route
  - expected text
  - expected element
  - expected panel/modal open state
  - expected breadcrumb/page heading

#### What It Will Improve
- Higher true success precision
- Fewer wrong clicks counted as wins
- Stronger benchmark fidelity

#### How We Will Solve It
- Define one validator interface
- Attach validation metadata to every planned click
- Run validation after every step before advancing

#### What Files We Can Change To Reach That Accuracy
- `app/execution/step_runner.py`
- `app/browser/experiment_logger.py`
- `app/dom_schema.py`
- new file: `app/browser/step_validator.py`

#### Expected Accuracy Increase
- Not just raw accuracy; this sharply improves correctness of reported accuracy

---

### Step 5: Use Agent Browser For More Than Clicking

#### Concrete Steps
- Read live visible text and page structure after each action
- Use snapshots to detect whether the intended feature really appeared
- Use element-presence checks and textual confirmations as first-class signals

#### What It Will Improve
- Better state awareness
- Better branching decisions
- Better handling of partial UI changes without route change

#### How We Will Solve It
- Use `snapshot_text`
- use `get_text()`
- use follow-up snapshot element existence checks
- store those signals in step results and experiment traces

#### What Files We Can Change To Reach That Accuracy
- `app/browser/agent_browser_cli.py`
- `app/execution/step_runner.py`
- `app/browser/experiment_logger.py`

#### Expected Accuracy Increase
- `5-15%` on modal-heavy or client-side navigation flows

---

### Step 6: Introduce A Dual-Mode Runtime

#### Concrete Steps
- Add two modes:
  - `benchmark_strict`
  - `production_resilient`
- `benchmark_strict`:
  - no selector fallback
  - explicit ambiguity failures
  - paired artifact generation
- `production_resilient`:
  - controlled selector fallback
  - extra recovery attempts
  - stronger pragmatic completion bias

#### What It Will Improve
- Honest experiment results
- Better production success without confusing the benchmark

#### How We Will Solve It
- Add explicit runtime policies to the step runner
- Log degraded success categories clearly

#### What Files We Can Change To Reach That Accuracy
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/browser/experiment_logger.py`

#### Expected Accuracy Increase
- Benchmark trustworthiness: high
- Production completion rate: `5-15%`

---

### Step 7: Build A Closed-Loop Benchmark And Failure Replay System

#### Concrete Steps
- Save every failed run with:
  - raw snapshots
  - screenshots
  - chosen ref
  - candidate list
  - post-action state
- Add replay scripts to rerun those failures after each change

#### What It Will Improve
- Stable iteration toward high accuracy
- Faster debugging
- Defensible claims about improvement over time

#### How We Will Solve It
- Build a fixed benchmark suite
- Add replay commands in CI/local tooling
- Track failure categories across runs

#### What Files We Can Change To Reach That Accuracy
- `app/browser/experiment_logger.py`
- `scripts/ci_pipeline.py`
- new file: `scripts/replay_ab_failures.py`
- new file: `scripts/run_agent_browser_benchmark.py`

#### Expected Accuracy Increase
- Long-term sustained improvement: very high

---

## Recommended Implementation Order (End-To-End Roadmap)

This order spans **remediation** items (see companion doc) and **optimization** steps above.

1. Fix structured success validation ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md))
2. Fix Phase 5 decision logic ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md))
3. Complete experiment logging ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md))
4. Tighten snapshot normalization ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md))
5. Separate strict benchmark mode from resilient runtime mode ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md) + Step 6 here)
6. Add paired benchmark harness ([remediation](AGENT_BROWSER_ACCURACY_REMEDIATION.md))
7. Add grounded reranking and replan-after-transition flow (Steps 2–3 here)
8. Add failure replay and benchmark automation (Step 7 here)

---

## Recommended Definition Of "99% Accuracy"
Do not define `99%` as "the command ran without throwing."

Define it as:

- correct target clicked
- expected state reached
- validated by explicit rule
- reproducible across repeated runs
- backed by saved artifacts

A run should only count as accurate when all of those are true.

## Final Recommendation
The current integration is a strong starting point, but it is still a hybrid prototype rather than a fully trustworthy grounded automation system.

To get the most value from Agent Browser:

- keep using it for live grounding
- make validation explicit
- replan from fresh snapshots
- distinguish benchmark purity from production robustness
- connect code-change understanding to live snapshot-based action selection

That is the path most likely to yield very high real-world accuracy for demo video generation in this repo.

---

## See also
- [`AGENT_BROWSER_ACCURACY_REMEDIATION.md`](AGENT_BROWSER_ACCURACY_REMEDIATION.md) — Part 1 remediation backlog (priorities 1–8)
- [`AGENT_BROWSER_INTEGRATION_PLAN.md`](AGENT_BROWSER_INTEGRATION_PLAN.md) — original integration and phase plan
