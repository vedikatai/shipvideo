# Production-Readiness Audit: 5 Architectural Fixes for Video Accuracy

**Date:** 2026-03-25  
**Spec:** [architectural-fixes-video-accuracy.md](file:///Users/sourabhligade/shipvideo-engine/mdfiles/architectural-fixes-video-accuracy.md)

---

## Final Verdict: **RISKY**

The five fixes are structurally present and the critical paths are wired. However, there are **missing spec items, silent-failure paths, security concerns, and zero automated test coverage** that make this risky for production without remediation.

---

## 1. Critical Issues (Must Fix Before Production)

### 🔴 C1 — `extract_contract_llm` is entirely unimplemented

The spec explicitly defines an optional `extract_contract_llm(diff_files)` as a **separate JSON-only extraction call** (Fix 2, item 2). This function does not exist anywhere in the codebase. The merge strategy (static + LLM agreement → confidence scoring) is therefore non-functional.

- `source_extraction_llm` on `DemoContract` is always `False`
- `agreement_score` never reflects LLM agreement — it is simply `completeness / 3.0`
- **Impact:** Low-confidence contracts will be more frequent than necessary, causing many PRs to fall back to screenshot-only.

> **File:** [contract_extraction.py](file:///Users/sourabhligade/shipvideo-engine/app/steps/contract_extraction.py)

### 🔴 C2 — Silent exception swallowing in `generate_steps_from_diff`

The broad `except Exception` at [step_generation.py:572-585](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L572-L585) catches **all** exceptions (except `ContractIntegrityError`), prints them, and returns a fallback result with `generation_context: None`. This includes:

- Network errors (API timeout, DNS failure)
- Authentication failures
- Unexpected data shape issues

There is **no way for the caller to distinguish** between "budget exceeded" and "catastrophic LLM failure." Both return silently with a fallback screenshot step. This violates the spec's mandate for typed integrity errors and stage-level failure attribution (Fix 5).

### 🔴 C3 — `run_ab_stepwise` does not raise `ContractIntegrityError` internally

The spec (Fix 4, item 3) states: *"Feed replan failures into typed integrity/reporting errors (Fix 5)."* The `run_ab_stepwise` function never raises or records `ContractIntegrityError`. It is only raised after the fact in [step_execution.py:244-251](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_execution.py#L244-L251) and [step_execution.py:282-289](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_execution.py#L282-L289), meaning the typed error is constructed from _indirect_ signals, not from the runner's own divergence detection.

- **Risk:** The `ContractIntegrityError` raised in `step_execution.py` for "unrecoverable" checks whether `failure_reason == "unrecoverable"` but the runner might set `failure_reason` to other fatal values (e.g., `"stale_ref_unrecovered"`, `"click_failed"`) that also represent replan exhaustion without triggering the integrity error.

### 🔴 C4 — `RunMetrics` reconstruction from dict is fragile

In [pipeline.py:384](file:///Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py#L384) and [pipeline.py:394-395](file:///Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py#L394-L395):

```python
updated = RunMetrics(**rm)
```

`RunMetrics` has fields with `field(default_factory=list)`. Reconstructing from a dict that went through JSON serialization means list fields may have already been populated. `RunMetrics(**rm)` **does not re-initialize default factories** — it passes the existing lists from the dict. This works, but:

- If `rm` is malformed (missing keys), this will crash with a `TypeError`
- If `rm` has extra keys (from a newer schema), this will crash with a `TypeError`
- There is no validation — any garbage dict will be accepted

### 🔴 C5 — Zero automated test coverage

There are **no tests** for any of the five fix modules:

| Module | Tests |
|--------|-------|
| `demo_contract.py` | ❌ None |
| `contract_extraction.py` | ❌ None |
| `preflight.py` | ❌ None |
| `errors.py` | ❌ None |
| `metrics.py` | ❌ None |
| `step_normalizer.py` (new logic) | ❌ None |
| `step_generation.py` (integrity check) | ❌ None |
| `pipeline.py` (contract/preflight/repair) | ❌ None |
| `step_runner.py` (replan, terminal assert) | ❌ None |

Only two test files exist in the repo (`test_azure_openai_client.py`, `test_budget_status.py`), neither related to these fixes. **This is a major production-readiness gap.**

---

## 2. Major Gaps (Spec Mismatches / Missing Logic)

### 🟠 M1 — Preflight `start_route` check does not handle full URL normalization

[preflight.py:140](file:///Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L140) compares `first_goto.url` against `start_route` with strict equality. But `generate_steps_from_diff` may inject full URLs (e.g., prepending staging_url) while `start_route` is a path (`/billing`). This mismatch can cause false preflight failures.

### 🟠 M2 — Repair path does not use a **different prompt template** as spec mandates

Spec Fix 3, item 5 explicitly states: *"second attempt must use a different prompt template than first attempt"*. The `regenerate_steps_from_preflight` function does use a different `system_msg` and reduced context, which is good. But it sends `dom_hints` which may be empty if generation_context was sparse. The spec also requires:

- Exclude: *raw diff payload, broad DOM dumps* ✅ Done
- Include: *DemoContract, preflight failures, reconciled DOM match summary* ✅ Done
- Require *explicit "fix list" acknowledgement* ✅ Done (validated at [step_generation.py:256-263](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L256-L263))

**Partial compliance** — the key gap is that `dom_hints` can be `{}` when `preflight_dom_hints` was not populated.

### 🟠 M3 — Contract target coverage check in preflight uses substring matching

[preflight.py:161](file:///Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L161):
```python
if target_label in lbl or lbl in target_label or target_label in matched or matched in target_label:
```

This is overly permissive. Example: target label `"Go"` would match step label `"Google Login"`. The spec requires *acceptable confidence* matching, not raw substring inclusion.

### 🟠 M4 — `regenerate_ab_remaining_steps` ignores `remaining_targets` properly

In [retry_engine.py:78](file:///Users/sourabhligade/shipvideo-engine/app/llm/retry_engine.py#L78):
```python
"remaining_targets": (contract or {}).get("targets") or [],
```

This sends **all** contract targets, not just the ones remaining after `step_idx`. The spec (Fix 4) says *"replan remaining steps from … contract remainder"*, meaning only targets not yet reached should be included.

### 🟠 M5 — `ContractIntegrityError` raised for preflight target missing causes instant abort

In [pipeline.py:148-155](file:///Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py#L148-L155), when any preflight error starts with "Contract target missing", the code **immediately raises** `ContractIntegrityError` instead of attempting the constrained repair path first. The spec says: *"if preflight fails, call a separate repair-generation path one time."* This short-circuits the repair for the very case it was designed for.

### 🟠 M6 — No `plan_diverged` tracking during execution

`RunMetrics.plan_diverged` field exists but is **never set to `True`** anywhere in the codebase. It stays `False` permanently.

---

## 3. Edge Case Failures

### 🟡 E1 — `extract_contract_static` can produce empty `contract_id` for edge-case inputs

If `start_route` is empty, `targets` is empty, and `terminal` is None, the digest input is `"|,|"` — still produces a hash, but semantically meaningless. Two different "completely empty" contracts from different PRs will share the same contract_id.

### 🟡 E2 — `_REPAIR_FLOW_JSON_SCHEMA` requires `"condition"` as `{"type": "object"}` with no properties

The condition field in the repair schema is an unconstrained `object` type. If the LLM returns `{"condition": null}` or `{"condition": "text"}`, `json_schema` strict mode will reject it. But the `additionalProperties: False` rule combined with a bare `type: object` means **no keys are valid inside condition**. This schema is broken for `assert_terminal` steps that need `{"type": "...", "value": "..."}` inside condition.

### 🟡 E3 — `match_label` overlap threshold of 0.6 is arbitrary

[preflight.py:77](file:///Users/sourabhligade/shipvideo-engine/app/steps/preflight.py#L77) — The threshold `0.6` seems reasonable but has no documented justification. Short labels (2-3 chars) will frequently match unrelated buttons. Combined with M3 (substring matching in preflight), this creates a false-positive hazard for short target labels.

### 🟡 E4 — `preflight_gate` accepts `contract` as `Optional[Dict]`

The function signature accepts `None`, in which case it immediately returns `PreflightResult(False, ["Missing demo contract"], "regenerate")`. But callers in `pipeline.py` always convert `DemoContract -> dict` first. If serialization fails silently (e.g., `asdict` raises), the type of the error is lost.

### 🟡 E5 — `assert_terminal` failure outcome is labeled `"wrong_click"` in step_runner

At [step_runner.py:1034](file:///Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L1034), a failed terminal assertion sets `outcome: "wrong_click"` — a misleading label since no click was involved. This will pollute the `wrong_click_count` metric.

---

## 4. Performance Risks

### ⚡ P1 — Two LLM calls per failed preflight

A failed first preflight triggers `regenerate_steps_from_preflight`, which is an additional LLM call. This doubles LLM cost for any plan that doesn't pass preflight on the first attempt. No cost tracking or budgeting is applied to the repair call (only `check_budget()` is called, not `record_spend`).

### ⚡ P2 — `reconcile_steps_with_dom` iterates all steps × all buttons × all testids

For a plan with many steps and a large DOM, this is O(S × (B + T)). Not a concern for the current cap of ~8 steps, but if step limits increase this could become a bottleneck.

### ⚡ P3 — `compute_summary` reads ALL metrics files from disk every time

[metrics.py:58-68](file:///Users/sourabhligade/shipvideo-engine/app/steps/metrics.py#L58-L68) — `glob("*.json")` with no pagination or time-range filter. Over time, this will degrade as metrics accumulate.

---

## 5. Code Quality Problems

### 📝 Q1 — `validate_against_dom` is dead code in the new flow

The function is still defined in `step_normalizer.py` (136 lines) and has a compatibility note, but is **no longer called** by any module. It should be deprecated explicitly or removed.

### 📝 Q2 — Inconsistent contract representation (dataclass vs dict)

`DemoContract` is a dataclass, but it's immediately converted to a dict via `.to_dict()` before being passed through the pipeline. Every consumer then accesses it with `.get()` instead of attributes. This defeats the purpose of having a typed contract.

### 📝 Q3 — Private function imported across module boundaries

[step_generation.py:30](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L30):
```python
from app.steps.step_normalizer import ..., _VALIDATION_PASSTHROUGH_FIELDS
```

Importing a private (`_`-prefixed) constant from another module breaks encapsulation. If `step_normalizer` changes this tuple, `step_generation` will silently break.

### 📝 Q4 — `locals().get("run_metrics")` is a code smell

[pipeline.py:233](file:///Users/sourabhligade/shipvideo-engine/app/steps/pipeline.py#L233):
```python
existing = locals().get("run_metrics")
```

Using `locals()` to check if a variable was defined is fragile and hard to maintain. Initialize `run_metrics` early in the function instead.

### 📝 Q5 — `_FATAL_OUTCOMES` includes `"stale_ref"` but this is a recoverable state

[step_runner.py:931-936](file:///Users/sourabhligade/shipvideo-engine/app/execution/step_runner.py#L931-L936) — `"stale_ref"` is listed as a fatal outcome, but the retry loop above already handles stale refs with retry logic. If a stale ref is retried and succeeds, `outcome` would be changed. But if only one retry was used and the outcome is still `"stale_ref"`, it's treated as fatal even though the spec says stale-ref single retry is a normal case.

### 📝 Q6 — Inconsistent `outcome` naming conventions

The codebase mixes outcome labels:
- `"success"` vs `"ok"` (status field vs outcome field)
- `"click_failed"` used for: no intent, goto failures, unknown actions, snapshot failures, and actual click API errors
- `"wrong_click"` used for both validation failures AND terminal assertion failures

This makes metrics unreliable for failure attribution (the core goal of Fix 5).

---

## 6. Security Concerns

### 🔒 S1 — `user_msg` in LLM calls includes raw diff content

[step_generation.py:442-454](file:///Users/sourabhligade/shipvideo-engine/app/steps/step_generation.py#L442-L454) — The entire diff payload (which may contain secrets, API keys, or sensitive code) is passed directly to the LLM. No scrubbing of `.env` files, credential patterns, or sensitive content.

### 🔒 S2 — No input sanitization on `contract.targets[].label`

Labels extracted from diff JSX (e.g., `<button>DROP TABLE ...</button>`) flow directly into:
- LLM prompts (potential prompt injection)
- Log messages (log injection)
- JSON file metrics (stored as-is)

---

## 7. Audit Summary Table

| Fix | Core Implementation | Spec Compliance | Edge Cases | Production-Ready |
|-----|-------------------|-----------------|------------|-----------------|
| **Fix 1** (Validation metadata preservation) | ✅ Done | ✅ Good — fields preserved, integrity check present | ⚠️ Integrity check raises, no graceful degradation option | 🟡 Mostly Ready |
| **Fix 2** (DemoContract) | ✅ Structurally done | ⚠️ `extract_contract_llm` missing; merge strategy non-functional | ⚠️ Empty contracts get degenerate IDs | 🟡 Partial |
| **Fix 3** (Reconcile + Preflight Gate) | ✅ Done | ⚠️ Preflight short-circuits repair for target-missing. Substring matching too loose. | ⚠️ Repair schema for condition is broken | 🟡 Partial |
| **Fix 4** (AB Adaptive Replan) | ✅ Done | ⚠️ Sends all targets instead of remaining. No `ContractIntegrityError` from runner itself. | ⚠️ `stale_ref` in fatal outcomes may cause false fatals | 🟡 Partial |
| **Fix 5** (Metrics + Typed Errors) | ✅ Done | ⚠️ `plan_diverged` never set. Outcome labels inconsistent. | ⚠️ Metrics dir grows unbounded | 🟡 Partial |

---

## 8. Specific Recommendations (Priority-Ordered)

1. **Write unit tests** for `preflight.py`, `contract_extraction.py`, `metrics.py`, `errors.py`, and the normalization passthrough logic — these are all easily testable pure functions
2. **Fix the `_REPAIR_FLOW_JSON_SCHEMA`** — `condition` needs to allow `type` and `value` properties, not be a bare `object`
3. **Remove the short-circuit `ContractIntegrityError` raise** in `pipeline.py:148-155` — let the repair path handle target-missing errors as designed
4. **Implement `extract_contract_llm`** or explicitly remove it from `DemoContract.source_extraction_llm` field
5. **Fix `remaining_targets` calculation** in `regenerate_ab_remaining_steps` to filter by step index
6. **Add `from_dict` classmethod to `RunMetrics`** that validates input
7. **Remove dead `validate_against_dom` function** or mark it with `@deprecated`
8. **Rename `"wrong_click"` outcome for `assert_terminal`** to `"terminal_not_reached"` to keep metrics honest
9. **Add sensitive content scrubbing** before LLM calls
10. **Initialize `run_metrics` early** in `pipeline.analyze_pr()` to eliminate `locals().get()` hack
