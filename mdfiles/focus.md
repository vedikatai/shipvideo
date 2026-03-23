Here it is:

---

# CURRENT FOCUS

**Last updated:** March 2026
**Status:** Active — do not deviate from this

---

## What this product does

Every time new code is pushed, the system deploys it to staging, automatically clicks through the new feature, records the real UI, generates a polished video, and attaches it to the PR.

The only metric that matters: **would a PM send this video to a customer without embarrassment?**

That is it. Nothing else.

---

## What is broken right now

The system cannot reliably tell whether a click actually worked. It treats any page state change as success. This means videos get generated showing wrong clicks, stuck states, and broken flows. The video looks broken. A PM would not send it.

Everything else in this repo is secondary until this is fixed.

---

## What you are building — in this exact order

---

### Fix 1 — Structured success validation
**Files: `app/execution/step_runner.py`, `app/steps/step_execution.py`, `app/browser/agent_browser_types.py`**

After every click, check that the expected outcome actually happened before marking the step successful.

Expected outcome must be one of:
- `url_match` — current URL matches expected pattern
- `text_present` — specific string is visible in post-click snapshot
- `element_present` — specific ref role/label exists in post-click snapshot

Rules:
- If expected outcome is not met, the step failed. Do not advance. Do not record that frame as success.
- `wrong_click` — click executed but expected state was not reached
- `click_failed` — click action itself did not execute
- `stale_ref` — ref no longer exists in DOM at click time
- These three must be distinct. Never collapse them into a generic error.

In `agent_browser_types.py`, add `ValidationCondition`:
- `type`: one of `url_match`, `text_present`, `element_present`
- `value`: string or pattern to check

Add `validation_result` to step result object:
- `passed`: bool
- `condition`: the `ValidationCondition` checked
- `actual`: what was observed

Pass validation condition into `run_ab_stepwise()` as optional parameter per step. If no condition is provided, log it as `unvalidated` but do not block execution. We are not breaking existing flows, we are adding the layer on top.

---

### Fix 2 — Snapshot normalization
**Files: `app/browser/agent_browser_cli.py`, `app/browser/ref_selector.py`, `app/browser/agent_browser_types.py`**

Right now the snapshot feeds non-clickable roles like `heading` and `navigation` into the selector. This causes wrong clicks when labels are similar.

`interactive_elements` must contain only:
- `button`
- `link`
- `textbox`
- `checkbox`
- `radio`
- `tab`
- `menuitem`
- `option`

Everything else goes into `context_elements`. Keep it for debugging. Do not feed it into the selector.

In `ref_selector.py`, selection runs only against `interactive_elements`. No flag needed for the opposite. Do not add it preemptively.

---

### Fix 3 — Stale ref detection and single retry
**Files: `app/browser/agent_browser_cli.py`, `app/execution/step_runner.py`**

Stale ref failures are currently invisible — folded into generic `click_failed`. This makes them unrecoverable and makes the video silently wrong.

- Parse CLI error output for stale ref signatures after a failed click
- Classify as `stale_ref`, not `click_failed`
- On `stale_ref`: re-snapshot once, re-run ref selection against fresh snapshot, retry click once
- If retry fails: `stale_ref_unrecovered`, stop
- Log stale ref frequency in step result

One re-snapshot. One retry. That is all. No CSS selector fallback on stale ref. No second retry.

---

## Valid step outcomes — exactly these, nothing else

`success` `wrong_click` `click_failed` `stale_ref` `stale_ref_unrecovered` `unvalidated`

---

## Definition of done

A run completes end to end. The video plays. Someone watching it can follow the feature being demonstrated. No wrong clicks. No stuck states. No frame where the system clicked a heading instead of a button and kept going.

---

## Do not touch

- Benchmark harness
- Paired run / Phase 5 logic
- Promotion decision defaults
- Dual-mode runtime
- Failure replay system
- `experiment_logger.py` beyond adding `validation_result` and `stale_ref` to step output
- CSS selector fallback anywhere in this flow

If it is not in Fix 1, Fix 2, or Fix 3 above — do not touch it.

---

## After this is done

Run 10 real PRs on real staging URLs. Score each video: `usable`, `usable with edits`, `unusable`. Only after that do we decide what to build next.

**Start with Fix 1. Show changes to `step_runner.py` and `agent_browser_types.py` first.**

---
