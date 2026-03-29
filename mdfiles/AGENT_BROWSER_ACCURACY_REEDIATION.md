# Agent Browser Accuracy Remediation (Backlog)https://github.com/vercel-labs/agent-browser

## Purpose
This document captures **Part 1 only**: the concrete issues found in the Agent Browser integration review and the **remediation backlog** to fix them (validation, experiment logic, logging, normalization, benchmarks, defaults).

For **how to use Agent Browser at a higher level** — code-aware planning, replanning, grounded ranking, paths toward very high demo accuracy — see **[`AGENT_BROWSER_OPTIMIZATION_PLAN.md`](AGENT_BROWSER_OPTIMIZATION_PLAN.md)**.

That companion doc also defines the **system mental model**: **step generation vs. step execution** (two failure modes), **one planner / multiple capture paths** (stepwise Agent Browser vs Playwright vs optional script-first), and why Agent Browser targets **click grounding**, not bad LLM plans.

The target is not to blindly claim "99% accuracy" today. The realistic goal is to build a path that can approach very high accuracy on a fixed, well-defined UI test suite and improve safely over time with measurable evidence.

## Current State Summary
The current integration is partially successful:

- The Agent Browser CLI wrapper is implemented and works in real runs
- The end-to-end path `open -> snapshot -> ref selection -> click -> re-snapshot` is wired and runnable
- The system can complete real demo flows

But the current experiment and validation stack is not strong enough yet to support a trustworthy "Agent Browser is better" conclusion.

The main problems are:

- success is currently inferred from generic state change rather than explicit expected outcome validation
- experiment summaries can produce misleading go/no-go decisions
- instrumentation is incomplete relative to the plan
- snapshot normalization is too loose
- selector fallback helps runtime success but muddies the experiment signal
- the planner still does not fully exploit Agent Browser as a grounded decision engine (see optimization doc for that roadmap)

## Part 1: Remediation Backlog

### Priority 1: Structured Success Validation

#### Problem and Goal
Problem:
The runner currently treats any URL change or snapshot-text change as a successful click.

Goal:
Only count a step as successful when the expected product outcome is reached.

#### How We Will Solve It
- Add structured success conditions to the Agent Browser execution path:
  - `url_match`
  - `text_present`
  - `element_present`
- Pass test-case or step-level expectations into `run_ab_stepwise()`
- After every click, validate the post-click state against that explicit expectation
- Treat failures as:
  - `wrong_click` when the click technically succeeded but expected state was not reached
  - `click_failed` when the action itself failed
  - `stale_ref` when the ref became invalid between snapshot and click

#### What Impact It Will Bring
- Removes false positives
- Makes experiment results trustworthy
- Prevents accidental promotion of the Agent Browser path based on weak evidence
- Improves debugging because "success" now means the same thing for both backends

#### What Files Will Be Affected
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/browser/experiment_logger.py`
- `app/browser/agent_browser_types.py`
- `app/dom_schema.py`

#### What Accuracy Increase It Will Do
- Estimated measurement-quality improvement: very high
- Expected reduction in false-success reporting: `30-60%` on dynamic flows
- Expected true click-accuracy gain: indirect but significant, because bad actions will no longer be counted as wins

---

### Priority 2: Fix Broken Phase 5 Decision Logic

#### Problem and Goal
Problem:
The aggregate experiment summary can report `regressed` or `no_go` even when there is no valid paired Playwright baseline.

Goal:
Only issue go/no-go decisions when paired baseline data exists and the threshold logic is valid.

#### How We Will Solve It
- Require paired runs before computing promotion decisions
- If no matching Playwright baseline exists, classify the result as `inconclusive`
- Update threshold logic so `0 vs 0` target-selection failures is not treated as a regression
- Separate:
  - single-run health
  - paired benchmark result
  - repo-level promotion recommendation

#### What Impact It Will Bring
- Stops misleading experiment summaries
- Makes Phase 5 artifacts usable for real engineering decisions
- Keeps the repo from over-promoting Agent Browser before evidence exists

#### What Files Will Be Affected
- `app/browser/experiment_logger.py`
- `app/steps/step_execution.py`
- `AGENT_BROWSER_INTEGRATION_PLAN.md`

#### What Accuracy Increase It Will Do
- Runtime click accuracy: none directly
- Decision accuracy: very high
- Expected reduction in false negative rollout decisions: `80-100%`

---

### Priority 3: Complete Experiment Logging

#### Problem and Goal
Problem:
The saved traces do not fully include the information promised by the plan:

- raw snapshot paths are missing
- candidate refs are not recorded
- candidate counts are not real
- snapshot diff detection is not tracked independently

Goal:
Make every Agent Browser run explainable from artifacts alone.

#### How We Will Solve It
- Add these fields to per-step runner results:
  - `raw_snapshot_path`
  - `candidates`
  - `candidate_count`
  - `snapshot_diff_detected`
  - `click_target_type` (`ref`, `css_selector`, `xpath`)
  - `validation_condition`
  - `validation_result`
- Persist those fields unchanged into `run_trace.json`
- Save comparison artifacts only when both backends ran the same test case

#### What Impact It Will Bring
- Makes bad clicks debuggable after the fact
- Lets the team tell whether Agent Browser won because of grounding, fallback, or luck
- Makes benchmark review much faster and less subjective

#### What Files Will Be Affected
- `app/execution/step_runner.py`
- `app/browser/ref_selector.py`
- `app/browser/experiment_logger.py`
- `app/context/dom_extractor.py`

#### What Accuracy Increase It Will Do
- Runtime click accuracy: low direct impact
- Root-cause and iteration accuracy: very high
- Expected speedup in fixing failures: `2-4x`

---

### Priority 4: Tighten Snapshot Normalization

#### Problem and Goal
Problem:
The wrapper currently normalizes all returned refs into `interactive_elements`, including non-clickable roles like `heading` and `navigation`.

Goal:
Only feed truly actionable elements into selection unless the caller explicitly wants broader context.

#### How We Will Solve It
- Split snapshot output into:
  - `interactive_elements`
  - `context_elements`
- Keep only actionable roles in `interactive_elements` by default:
  - `button`
  - `link`
  - `textbox`
  - `checkbox`
  - `radio`
  - `tab`
  - `menuitem`
  - `option`
  - cursor-clickable surfaced by the CLI
- Preserve broader roles separately for debugging and planning
- Add normalization tests against real saved snapshots

#### What Impact It Will Bring
- Reduces ambiguity and wrong matches
- Makes deterministic selection more meaningful
- Lowers the chance of clicking headings or container nodes by mistake

#### What Files Will Be Affected
- `app/browser/agent_browser_cli.py`
- `app/browser/agent_browser_types.py`
- `app/dom_schema.py`
- `app/browser/ref_selector.py`

#### What Accuracy Increase It Will Do
- Expected reduction in selection ambiguity/noise: `10-25%`
- Expected gain on repeated-label pages: moderate

---

### Priority 5: Reclassify And Handle Stale Refs Properly

#### Problem and Goal
Problem:
Stale refs are currently folded into generic `click_failed` behavior.

Goal:
Distinguish stale-ref failures from other click failures and recover from them intentionally.

#### How We Will Solve It
- Parse CLI error text for stale-ref signatures
- Classify those failures as `stale_ref`
- Re-snapshot and re-select once for stale-ref recovery
- Record stale-ref frequency in experiment summaries

#### What Impact It Will Bring
- Better failure taxonomy
- Better retry behavior
- More trustworthy comparison against Playwright

#### What Files Will Be Affected
- `app/browser/agent_browser_cli.py`
- `app/execution/step_runner.py`
- `app/browser/experiment_logger.py`

#### What Accuracy Increase It Will Do
- Expected recovery improvement on dynamic pages: `5-15%`

---

### Priority 6: Separate "Experiment Purity" From "Runtime Robustness"

#### Problem and Goal
Problem:
CSS selector fallback improves runtime success but contaminates deterministic Mode A benchmark results.

Goal:
Allow runtime robustness without corrupting the experiment signal.

#### How We Will Solve It
- Add an explicit policy mode:
  - `strict_ref_only`
  - `ref_then_selector_fallback`
- Run Phase 4 baseline comparisons in `strict_ref_only`
- Allow selector fallback in production capture only when explicitly enabled
- Log every fallback as a degraded success, not a clean ref-grounded success

#### What Impact It Will Bring
- Preserves clean accuracy measurements
- Retains practical capture reliability in production
- Makes benchmark results honest

#### What Files Will Be Affected
- `app/execution/step_runner.py`
- `app/steps/step_execution.py`
- `app/browser/experiment_logger.py`
- `AGENT_BROWSER_INTEGRATION_PLAN.md`

#### What Accuracy Increase It Will Do
- Benchmark accuracy and interpretability: high
- Runtime capture success on messy pages: moderate

---

### Priority 7: Add A Real Paired Benchmark Harness

#### Problem and Goal
Problem:
The repo has pieces of the experiment framework, but no true paired runner that executes both backends on the same fixed test suite and produces comparison artifacts systematically.

Goal:
Create one benchmark command that produces trustworthy A/B results.

#### How We Will Solve It
- Add a benchmark entry point that:
  - loads the fixed test suite
  - runs Playwright stepwise
  - runs Agent Browser stepwise
  - writes paired artifacts
  - calls `compare_runs()`
- Refuse Phase 5 promotion decisions unless the paired suite exists

#### What Impact It Will Bring
- Turns the current work into a real experiment
- Makes go/no-go decisions defensible
- Gives a durable accuracy dashboard for future iterations

#### What Files Will Be Affected
- `app/browser/experiment_logger.py`
- `app/steps/step_execution.py`
- `scripts/ci_pipeline.py`
- `app/steps/pipeline.py`
- new file: `scripts/run_agent_browser_benchmark.py`

#### What Accuracy Increase It Will Do
- No direct click gain
- Very high confidence gain in measurement and rollout safety

---

### Priority 8: Keep Default Promotion Conservative

#### Problem and Goal
Problem:
The repo currently defaults capture to Agent Browser before the benchmark evidence is complete.

Goal:
Make the default backend choice reflect actual evidence, not optimism.

#### How We Will Solve It
- Keep explicit feature flags
- Distinguish:
  - recommended benchmark backend
  - default production backend
  - local developer override
- Only promote Agent Browser by default after paired benchmark thresholds are met

#### What Impact It Will Bring
- Safer production behavior
- Fewer silent regressions
- Clearer operational expectations

#### What Files Will Be Affected
- `app/steps/step_execution.py`
- `app/steps/pipeline.py`
- `run.sh`
- `.github/workflows/shipvideo-engine-ci.yml`

#### What Accuracy Increase It Will Do
- Accuracy itself: neutral
- Production reliability: meaningful

---

## Recommended implementation order (this backlog)

Execute in roughly this order:

1. Priority 1 — Structured success validation  
2. Priority 2 — Fix Phase 5 decision logic  
3. Priority 3 — Complete experiment logging  
4. Priority 4 — Tighten snapshot normalization  
5. Priority 6 — Separate experiment purity vs runtime robustness  
6. Priority 7 — Paired benchmark harness  
7. Priority 5 — Stale ref classification  
8. Priority 8 — Conservative default promotion  

For the **full system roadmap** (including replanning, ref ranking, replay, and optimization steps), see **[`AGENT_BROWSER_OPTIMIZATION_PLAN.md`](AGENT_BROWSER_OPTIMIZATION_PLAN.md)**.

---

## See also
- [`AGENT_BROWSER_OPTIMIZATION_PLAN.md`](AGENT_BROWSER_OPTIMIZATION_PLAN.md) — Part 2: Agent Browser power, hybrid planning, and maximum grounded accuracy
- [`AGENT_BROWSER_INTEGRATION_PLAN.md`](AGENT_BROWSER_INTEGRATION_PLAN.md) — original integration and phase plan
