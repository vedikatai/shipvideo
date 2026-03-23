Based on Document 6 — the actual codebase, not the design docs — here are the five real fixes.

---

## Fix 1: Stop `normalize_steps` From Destroying Validation Metadata

### What is actually broken

This is the most damaging bug in the entire system and it is one function. Look at this:

```python
if action == "click":
    if label:
        normalized.append({"action": "click", "label": label})
```

Every `success_condition`, `validation_condition`, `terminal_check`, `testid` — anything the LLM emitted or the pipeline attached — is silently dropped here. The AB runner already implements post-click validation. It checks `validation_condition` after every click. But by the time steps reach the runner, that field is gone. The runner hits the `unvalidated` branch and marks every click `ok` regardless of what actually happened on screen.

You built a validation system and then deleted its inputs before they could be used.

### Exact code change

In `app/steps/step_normalizer.py`, change the click normalization block:

```python
# CURRENT — destroys everything
if action == "click":
    if label:
        normalized.append({"action": "click", "label": label})

# FIXED — preserve validation fields
if action == "click":
    normalized_step = {"action": "click"}
    if label:
        normalized_step["label"] = label
    elif text:
        normalized_step["label"] = text
    elif selector:
        normalized_step["selector"] = selector
    
    # Preserve these — never drop them
    for field in ["success_condition", "validation_condition", 
                  "validation_source", "expected_url", 
                  "expected_testid", "terminal"]:
        if step.get(field):
            normalized_step[field] = step[field]
    
    normalized.append(normalized_step)
```

Then in `app/steps/step_generation.py`, after injecting the terminal condition into the last click step, verify the field survives normalization by checking `normalized_steps[-1].get("validation_condition")` before returning. If it is missing, something dropped it. Treat that as a pipeline error, not a silent success.

### Why this is root cause not symptom

Every other validation improvement — pre-flight checks, contract enforcement, execution taxonomy — depends on validation conditions surviving into the runner. None of them work while this bug exists. This is the load-bearing wall everything else sits on.

### Impact

Eliminates the entire class of `unvalidated ok` outcomes on real PR runs. The AB runner's post-click validation — which you already built and which already works — activates for the first time on production steps. Every click now either passes an explicit check or fails explicitly. Silent wrong clicks become visible `wrong_click` failures immediately.

---

## Fix 2: Introduce a Typed `DemoContract` and Make It the Single Source of Truth

### What is actually broken

Right now the journey intent exists in four places simultaneously: the raw diff text in the LLM prompt, the `real_buttons` list from the DOM crawl, whatever the LLM decided to generate, and whatever survived `validate_against_dom`. None of these are the same thing. None of them are compared to each other. The system has no single authoritative answer to "what is this run supposed to do."

Precision note for the team: in the checked-in code, extraction and planning are a single LLM call over diff+DOM context. The "regex extraction is deterministic" critique applies to the design-doc path, not this runtime path. The real runtime failure is that extraction errors are hidden inside planning errors because they are not separated into distinct stages.

`validate_against_dom` makes this worse. It deletes steps that do not match the crawl — but it does not tell anyone what was deleted or why. A plan can lose two critical click steps silently and proceed with a degenerate four-step plan that will produce a broken video. The pipeline does not know. Execution does not know. You do not know until you watch the video.

### Exact code change

Create `app/steps/demo_contract.py`:

```python
from pydantic import BaseModel
from typing import List, Optional, Literal

class TargetRef(BaseModel):
    label: str
    testid: Optional[str] = None
    expected_url: Optional[str] = None
    
class TerminalCondition(BaseModel):
    type: Literal["element_present", "url_match", "text_present"]
    value: str

class TerminalAssertionStep(BaseModel):
    action: Literal["assert_terminal"] = "assert_terminal"
    condition: TerminalCondition
    inserted_by: Literal["contract", "llm"] = "contract"

class DemoContract(BaseModel):
    start_route: str
    targets: List[TargetRef]  # ordered clicks
    terminal: TerminalCondition
    unguided: bool = False
    confidence: Literal["high", "medium", "low"] = "low"
    
    def is_runnable(self) -> bool:
        return (
            bool(self.start_route) and
            len(self.targets) > 0 and
            self.terminal is not None and
            not self.unguided
        )
```

In `app/steps/pipeline.py`, build a `DemoContract` immediately after a dedicated extraction stage — before step planning. This object is the contract for the entire run. Pass it into `generate_steps_from_diff`. Pass it into `run_capture`. Pass it into `run_ab_stepwise`. It does not change after creation.

In `validate_against_dom`, change behavior: instead of silently deleting steps, compare the surviving steps against `contract.targets`. If a target from the contract has no matching surviving step, that is a `ContractViolation`, not a quiet deletion. Return a `ValidationResult` that includes which targets were lost and why.

### Why this is root cause not symptom

Without a single contract object, every layer makes its own local decisions about what the run is supposed to do. Those decisions diverge. There is no way to detect divergence. The contract creates one authoritative source that every layer reads from and reports against.

### Impact

Makes degenerate plans visible before they run. When `validate_against_dom` drops a critical click step, the pipeline now knows — it has the contract to compare against. Eliminates the silent plan corruption that currently happens between generation and execution. Every downstream fix — pre-flight validation, execution abort, metrics — becomes straightforward because they all read from one object.

---

## Fix 3: Replace `validate_against_dom` Filtering With a Pre-Flight Gate

### What is actually broken

`validate_against_dom` currently does two harmful things disguised as validation:

First, it uses exact string matching on labels. If the LLM writes "Continue to Phase 2" and the crawl saw "Continue to Phase 2 →" they do not match. The step gets deleted. The plan is now missing a click. Nobody is told.

Second, it widens `valid_routes` with diff-inferred routes — meaning it can accept `goto` steps to pages the crawler never actually confirmed exist on staging. The opposite of safety.

The function is called "validate" but it filters silently and accepts things it should not. It is not a gate. It is a lossy pipe.

### Exact code change

In `app/steps/step_normalizer.py` and `app/steps/step_generation.py`, replace the current validation flow with two separate functions with distinct jobs:

```python
# Function 1: confidence-scored DOM reconciliation — does NOT delete steps
@dataclass
class LabelMatch:
    matched: bool
    confidence: Literal["exact", "high", "low", "none"]
    matched_label: Optional[str]
    method: Literal["exact", "fuzzy_contains", "testid", "none"]

def match_label(label: str, dom_data: Dict) -> LabelMatch:
    real_buttons = [b for b in dom_data.get("real_buttons", [])]
    data_testids = [t for t in dom_data.get("data_testids", [])]

    # Exact match — always trust
    if label.lower() in [b.lower() for b in real_buttons]:
        return LabelMatch(True, "exact", label, "exact")

    # Explicit testid-ish match — high confidence
    testid_slug = label.lower().replace(" ", "-")
    testid = next((t for t in data_testids if testid_slug in str(t).lower()), None)
    if testid:
        return LabelMatch(True, "high", str(testid), "testid")

    # Fuzzy contains with overlap threshold; low-confidence remains blocking
    fuzzy = next(
        (
            b for b in real_buttons
            if label.lower() in b.lower()
            and (len(label) / max(len(b), 1)) > 0.6
        ),
        None,
    )
    if fuzzy:
        return LabelMatch(True, "high", fuzzy, "fuzzy_contains")

    return LabelMatch(False, "none", None, "none")

def reconcile_steps_with_dom(
    steps: List[Dict], 
    dom_data: Dict,
    contract: DemoContract
) -> ReconciliationResult:
    reconciled = []
    warnings = []
    
    for step in steps:
        if step["action"] != "click":
            reconciled.append(step)
            continue
            
        label = step.get("label", "")
        
        match = match_label(label, dom_data)
        if match.matched:
            step["dom_confirmed"] = True
            step["match_confidence"] = match.confidence
            step["match_method"] = match.method
            step["matched_label"] = match.matched_label
            reconciled.append(step)
        else:
            # Do NOT delete — flag it
            step["dom_confirmed"] = False
            step["dom_warning"] = f"Label '{label}' not found in crawled DOM"
            reconciled.append(step)
            warnings.append(step["dom_warning"])
    
    return ReconciliationResult(steps=reconciled, warnings=warnings)


# Function 2: pre-flight gate — this one actually blocks runs
def preflight_gate(
    steps: List[Dict],
    contract: DemoContract
) -> PreflightResult:
    errors = []
    
    # Gate 1: correct start
    first_goto = next((s for s in steps if s["action"] == "goto"), None)
    if not first_goto or first_goto.get("url") != contract.start_route:
        errors.append(f"Plan does not start at {contract.start_route}")
    
    # Gate 2: all contract targets covered
    click_labels = [s.get("label","").lower() 
                   for s in steps if s["action"] == "click"]
    for target in contract.targets:
        covered = any(
            target.label.lower() in c or c in target.label.lower() 
            for c in click_labels
        )
        if not covered:
            errors.append(f"Contract target missing from plan: '{target.label}'")
    
    # Gate 3: explicit terminal assertion required
    terminal_steps = [s for s in steps if s.get("action") == "assert_terminal"]
    if not terminal_steps:
        errors.append(
            "No terminal assertion step in plan. "
            "Contract requires explicit assert_terminal action."
        )
    elif terminal_steps[-1]["condition"]["value"] != contract.terminal.value:
        errors.append(
            "Terminal assertion value mismatch: "
            f"plan has '{terminal_steps[-1]['condition']['value']}', "
            f"contract requires '{contract.terminal.value}'"
        )
    
    # Gate 4: no degenerate plan
    click_count = sum(1 for s in steps if s["action"] == "click")
    if click_count == 0:
        errors.append("Degenerate plan: zero click steps after normalization")
    
    # Gate 5: only high-confidence matches pass
    low_conf = [
        s for s in steps
        if s.get("action") == "click"
        and s.get("dom_confirmed") is True
        and s.get("match_confidence") not in {"exact", "high"}
    ]
    if low_conf:
        errors.append("Low-confidence label reconciliation detected; regenerate required.")

    if errors:
        return PreflightResult(passed=False, errors=errors, action="regenerate")
    return PreflightResult(passed=True, errors=[], action="proceed")
```

In `pipeline.py`, call `preflight_gate` after normalization. If it returns `regenerate`, call `generate_steps_from_diff` once more with errors appended to the prompt. If the second attempt also fails pre-flight, return `plan_invalid` and do not open the browser.

### Why this is root cause not symptom

The current system has no hard stop between planning and execution. A broken plan always reaches the browser. This fix puts a real gate there. The browser only opens for plans that have been verified against the contract. Every plan that fails this gate would have produced a broken video. Now it produces a clear error instead.

### Impact

Eliminates the entire failure class of degenerate plans reaching the browser. On day one the pre-flight pass rate will probably be around 60-70%. That number tells you exactly how good your extraction and planning are — a real metric for the first time. Each improvement to upstream layers shows up immediately in this number. Videos that do get recorded are from plans that passed explicit verification.

---

## Fix 4: Give the AB Path the Same Adaptive Recovery as the Playwright Path

### What is actually broken

Document 6 is precise about this: you have two backends with completely different reliability models. The Playwright path has `regenerate_with_feedback` — when a step fails or navigation happens, it rewrites the remaining queue from the fresh DOM. The AB path does local retries for stale refs and then stops. On `no_match` or `ambiguous`, it fails fatally with no recovery.

This means the default backend — AB — is less reliable than the fallback backend. That is backwards.

The asymmetry also makes debugging impossible. A failure on the AB path might be recoverable on the Playwright path. You cannot tell if AB is actually better or if it just fails differently.

### Exact code change

In `app/execution/step_runner.py`, add a replan hook to `run_ab_stepwise`:

```python
async def _replan_remaining(
    remaining_steps: List[Dict],
    current_snapshot: ABSnapshot,
    contract: DemoContract,
    journey_goal: str,
    steps_completed: int
) -> List[Dict]:
    """
    Called when AB path hits no_match, ambiguous, or 
    repeated wrong_click. Regenerates only the remaining
    steps from current snapshot + contract.
    """
    fresh_context = {
        "interactive_elements": current_snapshot.interactive_elements,
        "current_url": current_snapshot.url,
        "completed_targets": contract.targets[:steps_completed],
        "remaining_targets": contract.targets[steps_completed:],
        "terminal": contract.terminal,
        "goal": journey_goal
    }
    
    # One LLM call — remaining steps only, not full plan
    return await regenerate_steps_from_snapshot(fresh_context)
```

Trigger this in `run_ab_stepwise` on:
- `no_match` — ref not found, replan from current state
- `ambiguous` — multiple refs matched, replan with disambiguation context
- Two consecutive `wrong_click` — plan has clearly diverged

Do not trigger on `stale_ref` — that already has its own single-retry recovery.

Cap replanning at two attempts per run. If still failing after two replans, abort with `unrecoverable` and report which contract target could not be reached.

### Why this is root cause not symptom

The AB path is the default. It is less reliable than the fallback. That means your production system runs on the weaker reliability model by default. Fixing this makes the default backend genuinely better, not just different. It also means failures on the AB path are now recoverable where the plan is good but the DOM shifted — which is the most common real-world failure on dynamic staging environments.

### Impact

Eliminates fatal failures on `no_match` and `ambiguous` that currently abort runs completely. On dynamic staging UIs where elements load asynchronously or labels render slightly differently than the crawl saw, replan-from-snapshot recovers the run instead of killing it. Estimated recovery rate on these failures: 40-60% of `no_match` cases that currently abort will complete successfully.

---

## Fix 5: First-Class Metrics Attached to the Contract

### What is actually broken

There are no metrics on the main PR path. Document 6 confirms this: experiment metadata exists when `test_case_id` is set, but normal PR runs produce nothing measurable. You cannot tell if your last change made things better or worse. Every debugging session starts from zero. You are making architectural decisions based on watching individual videos, which is the least reliable signal possible.

### Exact code change

Add a typed error surface so integrity failures never collapse into generic exceptions:

```python
class ContractIntegrityError(Exception):
    def __init__(
        self,
        stage: Literal["normalization", "preflight", "execution", "terminal"],
        field: str,
        expected: Any,
        actual: Any,
        contract_id: str,
    ):
        self.stage = stage
        self.field = field
        self.expected = expected
        self.actual = actual
        self.contract_id = contract_id
        super().__init__(
            f"ContractIntegrityError at {stage}: "
            f"field='{field}' expected={expected} actual={actual} contract={contract_id}"
        )

    def to_metric(self) -> Dict:
        return {
            "error_type": "contract_integrity",
            "stage": self.stage,
            "field": self.field,
            "contract_id": self.contract_id,
        }
```

Raise `ContractIntegrityError` when:
- normalization drops `validation_condition` from a click step
- preflight cannot match a required contract target
- execution ends without a valid `assert_terminal` evaluation

In `app/steps/pipeline.py`, create a `RunMetrics` object at the start of `analyze_pr` and pass it through every stage:

```python
@dataclass
class RunMetrics:
    run_id: str
    pr_number: int
    
    # Planning quality
    contract_confidence: str = "unknown"  # high/medium/low
    preflight_passed: bool = False
    preflight_errors: List[str] = field(default_factory=list)
    preflight_attempts: int = 0
    targets_matched_in_dom: int = 0
    targets_total: int = 0
    degenerate_plan: bool = False
    
    # Execution quality  
    steps_executed: int = 0
    steps_validated: int = 0
    steps_unvalidated: int = 0
    wrong_clicks: int = 0
    stale_refs: int = 0
    replans_triggered: int = 0
    plan_diverged: bool = False
    
    # Outcome
    terminal_condition_reached: bool = False
    backend_used: str = "unknown"
    execution_outcome: str = "unknown"
    contract_integrity_errors: List[Dict] = field(default_factory=list)
    
    # Human score — filled in manually or via webhook
    video_usable: Optional[str] = None  # usable/usable_with_edits/unusable
    
    def to_dict(self) -> Dict:
        return asdict(self)
```

At the end of every run, write this to `run_metrics/{run_id}.json`. After 10 runs, aggregate:

```python
def compute_summary(metrics_dir: str) -> Dict:
    runs = [load(f) for f in glob(f"{metrics_dir}/*.json")]
    return {
        "preflight_pass_rate": mean(r["preflight_passed"] for r in runs),
        "terminal_reached_rate": mean(r["terminal_condition_reached"] for r in runs),
        "unvalidated_click_rate": mean(
            r["steps_unvalidated"] / max(r["steps_executed"], 1) for r in runs
        ),
        "video_usable_rate": mean(
            r["video_usable"] == "usable" for r in runs 
            if r["video_usable"] is not None
        ),
        "degenerate_plan_rate": mean(r["degenerate_plan"] for r in runs)
    }
```

### Why this is root cause not symptom

Without metrics you are not engineering. You are guessing and hoping. Every fix you make — to extraction, normalization, pre-flight, execution — needs a signal that tells you it worked. `preflight_pass_rate` tells you if extraction and planning improved. `terminal_condition_reached` tells you if execution is completing flows. `unvalidated_click_rate` tells you if Fix 1 activated. `video_usable` is the ground truth. Without these four numbers you cannot tell the difference between a real improvement and a change that looks good in one test case and breaks three others.

### Impact

Turns every future change from a guess into a measurement. After 10 runs you will know exactly which layer is your biggest failure source. `degenerate_plan_rate` will probably shock you on day one — it will be higher than you expect and it will be invisible without this metric. Every subsequent architectural decision becomes evidence-based instead of intuition-based.

---

## Implementation Order

Do these in exactly this sequence. Each one unblocks the next.

**Fix 1 first** — because without validation metadata surviving normalization, Fixes 3 and 4 cannot enforce anything. This takes two hours.

**Fix 2 second** — because without a `DemoContract` object, there is nothing for the pre-flight gate to check against. This takes half a day.

**Fix 3 third** — because now you have a contract to validate against. The pre-flight gate becomes real. This takes one day.

**Fix 5 fourth** — instrument before you run real tests so you have data from the first run. Two hours.

**Fix 4 last** — because replan-from-snapshot is only useful once the contract exists and pre-flight is enforcing plan quality. Do not add recovery to a pipeline that is still producing degenerate plans. Fix the plans first, then add recovery for genuine runtime drift.

---

## What This System Looks Like After These Five Fixes

Every run now has a contract. The contract is verified before the browser opens. Validation metadata survives into the runner and activates post-click checks. The AB path can recover from runtime drift instead of aborting. Every run produces metrics that tell you exactly what happened and why.

The difference is not incremental. It is the difference between a system that sometimes produces good videos and a system that knows when it is going to produce a bad one and stops before it does.