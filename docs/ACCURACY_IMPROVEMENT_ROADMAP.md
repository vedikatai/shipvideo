# Accuracy Improvement Roadmap — shipvideo-engine

**Created:** 2026-03-20  
**Source analysis:** Deep comparative study against `third_party/git-glimpse`  
**Philosophy:** Each phase is a single, independently shippable PR that can be tested in isolation. Phases are ordered strictly by accuracy ROI.

---

## Quick Reference

| Phase | Title | Accuracy Impact | Effort |
|-------|-------|-----------------|--------|
| 1 | Unify the DOM element schema | High — removes contract drift causing locator failures | Small |
| 2 | Multi-route DOM crawl | High — fixes root grounding gap | Medium |
| 3 | Structured navigation-change detection | Medium-High — eliminates spurious replans | Small |
| 4 | Runtime selector existence validation | High — prevents wrong-element clicks that pass pre-checks | Small |
| 5 | Smarter diff budgeting | Medium-High — preserves signal on larger PRs | Medium |
| 6 | Repair-path robustness parity | Medium — makes LLM repair loop reliable on all deployments | Small |
| 7 | Dead-code removal + capture config wiring | Low-Medium — config drift and misleading surface area | Small |
| 8 | Trigger filtering and config discipline | Medium — precise run selection, safer config loading | Medium |

---

## Phase 1 — Unify the DOM Element Schema

### Goal

Right now `dom_crawler.py`, `dom_extractor.py`, `step_normalizer.py`, `selector_validator.py`, and `script_generator.py` all work from different shapes for the same concepts. Buttons leave the crawler as `{text, selector}`, but `_build_action_menu()` in the script generator tries to read `.testid`, `.aria`, and `.label` fields that are not present. Similarly, `dom_extractor.py` stores `label` where the validator and step generator expect `aria`. These mismatches silently degrade locator quality throughout the pipeline.

This phase introduces a single canonical `DomElement` TypedDict used everywhere, with no shape-narrowing in consumers.

### Exact Files

| File | Change |
|------|--------|
| `app/dom_schema.py` | **New** — canonical `ButtonCandidate`, `LinkCandidate`, `InputCandidate`, `TestIdCandidate`, `DomSnapshot` TypedDicts |
| `app/steps/dom_crawler.py` | Emit `ButtonCandidate` instead of `{text, selector}` |
| `app/context/dom_extractor.py` | Align button field names (`aria`/`label` unification) to schema |
| `app/policy/selector_validator.py` | Remove field-aliasing; read canonical field names |
| `app/generator/script_generator.py` | `_build_action_menu()` reads canonical fields |
| `app/llm/step_generator.py` | `available_buttons` construction reads canonical fields |
| `app/steps/step_normalizer.py` | Minor — `validate_against_dom` already mostly reads `selector`/`text`; verify consistency |

### Data Structures to Introduce

Create `app/dom_schema.py`:

```python
from typing import TypedDict, List, Optional

class ButtonCandidate(TypedDict):
    text: str          # visible innerText, stripped, max 100 chars
    testid: str        # data-testid value or ""
    aria: str          # aria-label value or ""
    id: str            # element id or ""
    role: str          # button / submit / etc.
    selector: str      # best CSS selector derived from testid > aria > id > tag

class LinkCandidate(TypedDict):
    text: str
    href: str          # raw href attribute
    testid: str
    aria: str
    id: str

class InputCandidate(TypedDict):
    placeholder: str
    name: str
    input_type: str    # "text" | "email" | "password" | etc.
    testid: str
    aria: str
    id: str

class TestIdCandidate(TypedDict):
    testid: str
    tag: str
    text: str

class DomSnapshot(TypedDict):
    current_path: str               # window.location.pathname
    routes: List[str]               # all known internal routes
    buttons: List[ButtonCandidate]
    links: List[LinkCandidate]
    inputs: List[InputCandidate]
    data_testids: List[TestIdCandidate]
```

### Step-by-Step Implementation Tasks

1. **Create `app/dom_schema.py`** with the TypedDicts above. No logic — schema only.

2. **Update `dom_crawler._collect_ui_elements`**: Change the button output loop to emit the full `ButtonCandidate` dict, preserving all raw fields from the JS eval (they are already being collected from the DOM; they were just being stripped before storage):

   ```python
   # Before (strips testid/aria/id before return)
   for meta in buttons[:MAX_ITEMS]:
       out["buttons"].append({
           "text": meta.get("text", ""),
           "selector": _short_selector(meta, "button"),
       })

   # After (keep all fields)
   for meta in buttons[:MAX_ITEMS]:
       out["buttons"].append({
           "text":     meta.get("text", ""),
           "testid":   meta.get("testid", ""),
           "aria":     meta.get("aria", ""),
           "id":       meta.get("id", ""),
           "role":     "button",
           "selector": _short_selector(meta, "button"),
       })
   ```

3. **Update `dom_extractor.extract_dom_context`**: Rename the `label` field in the JS eval to `aria` so it matches the schema. The JS already collects `aria-label`; it is stored as `label` right now which confuses consumers:

   ```js
   // Before
   label: e.getAttribute('aria-label') || e.getAttribute('title') || "",
   // After
   aria: e.getAttribute('aria-label') || e.getAttribute('title') || "",
   ```

4. **Update `selector_validator._allowed_raw_css`**: It already reads `.id` correctly. Confirm `_known_button_texts` reads `.text` — no change needed there. Confirm no code reads `.label` for aria checks — replace any with `.aria`.

5. **Update `script_generator._build_action_menu`**: It currently does `b.get("label") or b.get("aria")`. After the schema is unified, use only `b.get("aria")`.

6. **Update `step_generator.generate_next_steps`**: The `available_buttons` construction reads `b.get("label") or b.get("aria")`. Change to `b.get("aria")` only.

7. **Update `step_normalizer.validate_against_dom`**: Confirm it reads `btn.get("selector")` and `btn.get("text")` — both present in the new schema. No logic change needed.

8. **Add return type hints** on `crawl_dom_data` and `extract_dom_context` pointing to `DomSnapshot` to get static type-check coverage.

### Risks / Edge Cases

- **`_short_selector` output**: The `selector` field continues to be a CSS shorthand derived by `_short_selector`. Ensure it is still computed and stored. Do not delete it — `step_normalizer` validates against it.
- **`dom_extractor` is sync, `dom_crawler` is async**: They operate independently. The schema change is purely structural. Do not merge the two functions.
- **Partial partial match in validator**: `selector_validator.py` lines 109–110 allow partial text match. That is intentionally lenient and unrelated to this phase — do not change it here.

### Verification

**Unit tests** (add to a new `tests/test_dom_schema.py`):

```python
def test_button_candidate_has_all_fields():
    # Simulate what dom_crawler emits after Phase 1
    btn = {"text": "Sign in", "testid": "btn-signin", "aria": "", "id": "", "role": "button", "selector": "[data-testid='btn-signin']"}
    assert btn["testid"] == "btn-signin"
    assert "selector" in btn

def test_build_action_menu_uses_testid():
    dom = {"buttons": [{"text": "Go", "testid": "go-btn", "aria": "", "id": "", "role": "button", "selector": "[data-testid='go-btn']"}], "links": [], "routes": ["/"]}
    menu = _build_action_menu(dom)
    assert menu["clickable_elements"][0]["use"] == "page.get_by_test_id('go-btn')"
```

**Manual check**: After deploying, trigger a PR demo against a page with `data-testid` attributes. Confirm that generated scripts use `get_by_test_id()` rather than falling through to raw `page.locator(selector_val)`.

---

## Phase 2 — Multi-Route DOM Crawl

### Goal

`crawl_dom_data()` currently visits **one page** (the home URL) and caps each category at 20 items. When a PR adds or changes a route that is not linked from the homepage, the LLM receives zero grounding for that route and generates hallucinated selectors. This is the single biggest accuracy gap.

This phase adds a bounded BFS crawl seeded from diff-inferred routes, the `routeMap` config, and homepage links. It crawls at most `MAX_CRAWL_ROUTES` pages (default 6) per run.

### Exact Files

| File | Change |
|------|--------|
| `app/steps/dom_crawler.py` | Replace single-page `crawl_dom_data` with multi-route version |
| `app/steps/step_generation.py` | Pass `seed_routes` argument into `crawl_dom_data` |
| `app/steps/step_normalizer.py` | No logic change; `valid_routes` already merges all routes from `dom_data` |

### Data Structures to Introduce / Change

`crawl_dom_data` gains a new parameter and the return type gains per-route snapshots:

```python
# New signature
async def crawl_dom_data(
    staging_url: str,
    *,
    seed_routes: Optional[List[str]] = None,  # extra routes to prioritize
    max_routes: int = 6,
) -> DomSnapshot: ...
```

The return value gains one optional field (backward-compatible):

```python
class DomSnapshot(TypedDict):
    ...                               # existing fields unchanged
    route_snapshots: Dict[str, RouteSnapshot]  # keyed by path

class RouteSnapshot(TypedDict):
    buttons: List[ButtonCandidate]
    links: List[LinkCandidate]
    inputs: List[InputCandidate]
    data_testids: List[TestIdCandidate]
```

The top-level `buttons`, `links`, `inputs`, and `data_testids` on `DomSnapshot` remain and are the **union** across all crawled routes (deduped by testid/text), so all existing consumers continue working unchanged.

### Step-by-Step Implementation Tasks

1. **Add `_build_seed_routes` helper** in `dom_crawler.py`:

   ```python
   def _build_seed_routes(
       staging_url: str,
       discovered: List[str],
       seed_routes: Optional[List[str]],
       config_route_map: Dict[str, Any],
       diff_files: Optional[List[Dict[str, str]]] = None,
   ) -> List[str]:
       """
       Priority order:
         1. seed_routes (from caller — diff-inferred + routeMap hits)
         2. discovered homepage links
         3. config routeMap values
       Deduped, normalised to leading /, max MAX_CRAWL_ROUTES entries.
       """
   ```

2. **Refactor `crawl_dom_data`** to:
   - Launch one browser/context for all pages (reuse session/cookies).
   - Visit `/` first to discover links (existing `_discover_routes` logic).
   - Build the ordered seed list via `_build_seed_routes`.
   - Visit each seed route (up to `max_routes`) using a shared `Page`.
   - Per-route: call `_collect_ui_elements` and store in `route_snapshots[path]`.
   - After all routes: merge all buttons/links/inputs/testids into union lists (deduped), build unified `routes` list.

3. **Update `generate_steps_from_diff`** to compute seed routes before calling `crawl_dom_data`:

   ```python
   # Extract diff-inferred routes (already done inline via step_normalizer._extract_routes_from_diff)
   from app.steps.step_normalizer import _extract_routes_from_diff
   diff_routes = list(_extract_routes_from_diff(diff_files))

   # routeMap hits
   mapped = list(mapped_routes)  # existing logic

   seed = list(dict.fromkeys([*mapped, *diff_routes]))  # preserve priority order, dedup

   dom_data = await crawl_dom_data(staging_url, seed_routes=seed)
   ```

4. **Respect session/auth**: If `project_config.json` contains `auth.cookies` or `auth.localStorage`, inject into the browser context before visiting any page. (Scaffold the hook in this phase; full auth injection is Phase 8 scope.)

5. **Timeout budget**: Each route visit is capped at 12 s (`networkidle` with 12 000 ms timeout). Total crawl timeout is `max_routes * 12 s + 5 s` overhead. Log per-route timing.

6. **Graceful degradation**: If a seed route returns a non-200 or times out, skip and continue — never fail the whole crawl. Fall back to home-only snapshot for that route.

### Risks / Edge Cases

- **Authentication walls**: Routes behind login will return a login page, not the target UI. Guard against this by checking if the route's discovered buttons look identical to the home page (sign-in form detected) and skipping that route's elements.
- **Infinite redirect loops**: Cap redirects to 3 per page. Playwright's `goto` follows redirects by default.
- **Preview URLs with slow cold starts**: The existing `wait_for_preview_ready` poll is separate and upstream of this crawl. The crawl itself should not extend total wall-clock time beyond ~75 s on a 6-route budget.
- **Duplicate elements across routes**: Dedupe buttons by `(testid or text.lower(), role)` pair; dedupe links by `href`; dedupe testids by `testid` value.
- **`route_snapshots` field is new**: Consumers that pattern-match on `DomSnapshot` keys must not break. Since it is additive and all existing code reads top-level keys, this is safe.

### Verification

**Unit test** (`tests/test_dom_crawler.py`):

```python
@pytest.mark.asyncio
async def test_crawl_visits_seed_routes(mock_playwright):
    # Mock: homepage has links to /billing and /settings
    # seed_routes = ["/billing"]
    # Expect: /billing is visited before /settings
    ...

async def test_crawl_dedupes_buttons():
    # Buttons present on both / and /billing should appear once in union
    ...

async def test_crawl_skips_failed_routes():
    # If /admin returns 403, crawl continues and does not raise
    ...
```

**Manual end-to-end check**: Trigger a demo on a PR that modifies a non-home route (e.g., `/settings/billing`). Confirm the generated steps reference selectors from that route rather than defaulting to homepage elements.

---

## Phase 3 — Structured Navigation-Change Detection

### Goal

`_dom_signature()` in `navigation_detector.py` hashes the first 4000 characters of `document.body.innerText`. This fires a `detect_major_change` → full replan on any text update: counters incrementing, notification dots, toast messages, animated copy. Each false positive costs one LLM repair call and risks plan drift.

This phase replaces the raw text hash with a composite fingerprint of stable, structural page signals.

### Exact Files

| File | Change |
|------|--------|
| `app/execution/navigation_detector.py` | Replace `_dom_signature` with `_page_fingerprint` |
| `app/execution/step_runner.py` | No change to call sites — `detect_major_change` signature unchanged |

### Data Structures to Introduce / Change

```python
@dataclass
class PageFingerprint:
    path: str           # window.location.pathname
    title: str          # document.title
    heading_set: str    # sorted, joined h1+h2 texts (max 5 each, 60 chars each)
    landmark_count: int # count of [role=main], <main>, <nav>, <header>, <footer>
    testid_set: str     # sorted, joined data-testid values (max 20)

# NavigationState becomes:
@dataclass
class NavigationState:
    path: str
    fingerprint: PageFingerprint
    dom_hash: str  # kept for wait_stable_after_navigation stability loop only
```

`detect_major_change` uses a two-tier check:

```python
def detect_major_change(before: NavigationState, after: NavigationState) -> bool:
    # Tier 1: path change → always a major change
    if before.path != after.path:
        return True
    fp_b, fp_a = before.fingerprint, after.fingerprint
    # Tier 2: structural change — title or headings changed significantly
    if fp_b.title != fp_a.title:
        return True
    if fp_b.heading_set != fp_a.heading_set:
        return True
    if fp_b.testid_set != fp_a.testid_set:
        return True
    # Tier 3: landmark layout changed (modal opened, panel added, etc.)
    if abs(fp_b.landmark_count - fp_a.landmark_count) >= 2:
        return True
    return False
```

`wait_stable_after_navigation` continues to use the raw `dom_hash` loop because it only needs self-consistency (is the page still changing?), not semantic meaning.

### Step-by-Step Implementation Tasks

1. **Add `_collect_page_fingerprint(page: Page) -> PageFingerprint`** in `navigation_detector.py`:

   ```python
   def _collect_page_fingerprint(page: Page) -> PageFingerprint:
       result = page.evaluate("""() => {
           const hs = [...document.querySelectorAll('h1,h2')]
               .slice(0, 5).map(e => (e.innerText||"").trim().slice(0,60));
           const tids = [...document.querySelectorAll('[data-testid]')]
               .slice(0, 20).map(e => e.getAttribute('data-testid')||"");
           const landmarks = document.querySelectorAll(
               '[role=main],[role=dialog],[role=navigation],main,nav,header,footer,aside'
           ).length;
           return {
               path:    window.location.pathname || "/",
               title:   document.title || "",
               headings: hs,
               testids:  tids,
               landmarks: landmarks,
           };
       }""") or {}
       return PageFingerprint(
           path=result.get("path", "/"),
           title=result.get("title", ""),
           heading_set=" | ".join(sorted(result.get("headings", []))),
           landmark_count=int(result.get("landmarks", 0)),
           testid_set=" | ".join(sorted(result.get("testids", []))),
       )
   ```

2. **Update `capture_state`** to populate `PageFingerprint` and store it on `NavigationState`:

   ```python
   def capture_state(page: Page) -> NavigationState:
       fp = _collect_page_fingerprint(page)
       dom_hash = _dom_signature(page)  # keep for stability loop
       return NavigationState(path=fp.path, fingerprint=fp, dom_hash=dom_hash)
   ```

3. **Update `detect_major_change`** with the two-tier logic above. Keep `_dom_signature` and `dom_hash` for `wait_stable_after_navigation` only — do not remove it.

4. **Update `NavigationState` dataclass** to add the `fingerprint` field.

5. Confirm `step_runner.py` call sites pass through unchanged — they only see `NavigationState` and call `detect_major_change(prev, now)`.

### Risks / Edge Cases

- **SPAs that change `document.title` on tab focus / async load**: Add a 200 ms settle wait inside `capture_state` before fingerprinting to let async title updates land. Alternatively, gate the title change on it being more than 5 characters different.
- **Modals**: A modal's `[role=dialog]` increments `landmark_count` — that is intentional and correct; modals represent meaningful state change the LLM should be aware of.
- **testid_set on pages with many dynamic testids** (e.g., per-row testids like `row-42`): Cap at 20 and sort, which normalises most volatile cases. If still noisy, add a filter to skip testids containing digits only.
- **Backward compatibility**: `NavigationState.dom_hash` is still present. No external callers exist outside `navigation_detector.py` and `step_runner.py`. The `fingerprint` field is additive.

### Verification

**Unit tests** (`tests/test_navigation_detector.py`):

```python
def test_path_change_is_major():
    before = make_nav_state(path="/", title="Home")
    after  = make_nav_state(path="/billing", title="Home")
    assert detect_major_change(before, after) is True

def test_counter_change_is_not_major():
    # title and headings identical; only body text counter changed
    before = make_nav_state(path="/", title="Dashboard", headings=["Overview"])
    after  = make_nav_state(path="/", title="Dashboard", headings=["Overview"])
    assert detect_major_change(before, after) is False

def test_modal_open_is_major():
    before = make_nav_state(path="/", landmark_count=3)
    after  = make_nav_state(path="/", landmark_count=5)  # dialog + overlay
    assert detect_major_change(before, after) is True

def test_heading_change_is_major():
    before = make_nav_state(path="/", headings=["Product List"])
    after  = make_nav_state(path="/", headings=["Checkout"])
    assert detect_major_change(before, after) is True
```

**Manual check**: Run a demo on a page that has a live counter or notification badge. Confirm the replan is not triggered for counter increments.

---

## Phase 4 — Runtime Selector Existence Validation

### Goal

`validate_step_against_dom` in `selector_validator.py` passes `[data-testid='x']` and `[aria-label='x']` selectors unconditionally (lines 88–93), and passes Playwright engine strings (`role=`, `text=`) without checking whether any matching element currently exists on the page. A step can pass validation and then fail or click the wrong element at execution time.

This phase adds a presence check: for semantic selectors, verify at least one matching element exists on the live page before accepting.

### Exact Files

| File | Change |
|------|--------|
| `app/policy/selector_validator.py` | Add `_selector_count_on_page` helper; add optional `page` parameter to `validate_step_against_dom` |
| `app/execution/step_runner.py` | Pass `page` into `validate_step_against_dom` calls |
| `app/llm/retry_engine.py` | Pass `page` into `validate_step_against_dom` inside `regenerate_with_feedback` |

### Data Structures to Introduce / Change

`validate_step_against_dom` gains an optional `page` parameter. When `page` is `None`, behavior is unchanged (static validation only). When `page` is provided, semantic selectors are resolved on the live page:

```python
def validate_step_against_dom(
    step: Dict[str, Any],
    dom_ctx: Dict[str, Any],
    *,
    page: Optional[Page] = None,   # NEW — live page for existence check
) -> Tuple[bool, str]:
```

### Step-by-Step Implementation Tasks

1. **Add `_selector_count_on_page(page, selector: str) -> int`** helper:

   ```python
   def _selector_count_on_page(page: Page, selector: str) -> int:
       try:
           return page.locator(selector).count()
       except Exception:
           return 0
   ```

2. **Modify `validate_step_against_dom`**: After the existing structural checks pass, when `page` is provided, perform an existence check for `click` steps:

   ```python
   if action == "click" and page is not None:
       if selector:
           count = _selector_count_on_page(page, selector)
           if count == 0:
               return False, f"selector_not_found_on_page:{selector}"
           if count > 10:
               # Too many matches — selector is not unique enough
               # Still allow but downgrade to a warning in the reason string
               return True, f"ok:selector_ambiguous_count={count}"
       if text:
           # Existence already confirmed by _known_button_texts check above
           pass
   ```

3. **Update `step_runner.run_stepwise`**: The pre-execution validation call at line 78 gains the live page:

   ```python
   ok, reason = validate_step_against_dom(step, dom_ctx, page=page)
   ```

4. **Update `retry_engine.regenerate_with_feedback`**: The post-generation validation also gains access to the page. Thread the page through the signature:

   ```python
   def regenerate_with_feedback(
       *,
       objective,
       dom_context,
       error_context,
       max_attempts: int = 3,
       page: Optional[Page] = None,  # NEW
   ) -> Tuple[List[Dict], List[Dict]]:
       ...
       for s in steps:
           ok, reason = validate_step_against_dom(s, dom_context, page=page)
   ```

5. **Update `step_runner`** to pass `page` into `regenerate_with_feedback`.

6. **Imports**: Add `from typing import Optional` and `from playwright.sync_api import Page` to `selector_validator.py`.

### Risks / Edge Cases

- **Lazy-loaded / below-fold elements**: A legitimate selector may not be in DOM yet. Guard: if `count == 0` and selector is `data-testid`-based, do a scroll + re-check before rejecting:

  ```python
  if count == 0 and selector.startswith("[data-testid="):
      page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
      time.sleep(0.3)
      count = _selector_count_on_page(page, selector)
  ```

- **Dynamic loading**: Some SPAs render buttons only after API calls complete. Set `page.wait_for_selector(selector, state='attached', timeout=2000)` inside the helper with a short timeout before counting.
- **Backward compatibility**: The `page` parameter defaults to `None`. All existing callers in `step_normalizer.validate_against_dom` (batch validation, no page access) are unchanged.
- **Performance**: The existence check adds one `page.locator().count()` call per step (~5–30 ms in headless Chromium). Acceptable given the savings from avoiding mis-clicks.

### Verification

**Unit tests** with mock page:

```python
def test_testid_selector_not_found_on_page_is_rejected(mock_page):
    mock_page.locator.return_value.count.return_value = 0
    step = {"action": "click", "selector": "[data-testid='missing-btn']", "text": ""}
    ok, reason = validate_step_against_dom(step, {}, page=mock_page)
    assert ok is False
    assert "selector_not_found_on_page" in reason

def test_testid_selector_found_passes(mock_page):
    mock_page.locator.return_value.count.return_value = 1
    step = {"action": "click", "selector": "[data-testid='go-btn']", "text": ""}
    ok, reason = validate_step_against_dom(step, {}, page=mock_page)
    assert ok is True
```

**Manual check**: Introduce a step with a valid `data-testid` value that does not exist on the page. Confirm it is now caught at validation rather than silently failing at execution time.

---

## Phase 5 — Smarter Diff Budgeting

### Goal

`pr_extraction.py` truncates every file's patch to 3000 characters regardless of relevance. `step_generation.py` then caps the aggregate to 8000 characters with a blunt string slice. A large PR with many changed files reduces the LLM's window for the files that actually contain the UI changes, causing generic or fallback steps.

This phase introduces a ranked budget that:

1. Classifies files as UI-primary, UI-secondary, or non-UI.
2. Allocates per-file token budget proportionally (primaries get more).
3. Replaces blunt string truncation with structured summarisation for overflow files.

### Exact Files

| File | Change |
|------|--------|
| `app/steps/pr_extraction.py` | Add `score_file` classifier; no API change — `fetch_pr_diff` unchanged |
| `app/steps/diff_budget.py` | **New** — `budget_diff_files()` function |
| `app/steps/step_generation.py` | Replace inline truncation block with `budget_diff_files()` call |

### Data Structures to Introduce

Add `app/steps/diff_budget.py`:

```python
from __future__ import annotations
from typing import List, Dict, Tuple

# Tier definitions
UI_PRIMARY_EXTS    = {".tsx", ".jsx", ".vue", ".svelte"}
UI_SECONDARY_EXTS  = {".ts", ".js", ".css", ".scss", ".html"}
NON_UI_PATTERNS    = {".test.", ".spec.", "__tests__", ".md", ".json", "package.json", "tsconfig"}

UI_PRIMARY_DIRS    = {"app/", "src/components/", "src/pages/", "src/routes/", "pages/", "routes/"}
UI_SECONDARY_DIRS  = {"src/", "lib/", "utils/"}

MAX_BUDGET_CHARS   = 10_000   # replaces the 8000 hard cap
PRIMARY_CHARS      = 4_000    # budget per primary file
SECONDARY_CHARS    = 1_200    # budget per secondary file
NON_UI_CHARS       = 200      # summary line only

def score_file(path: str) -> int:
    """Return 2 = UI primary, 1 = UI secondary, 0 = non-UI."""
    ...

def budget_diff_files(
    diff_files: List[Dict[str, str]],
    *,
    total_char_budget: int = MAX_BUDGET_CHARS,
) -> Tuple[List[Dict[str, str]], bool]:
    """
    Re-allocate patch content across files by relevance tier.

    Returns:
        (budgeted_files, was_truncated)
    where budgeted_files is the same list structure as the input but with
    patch strings replaced by tier-appropriate excerpts.
    """
```

### Step-by-Step Implementation Tasks

1. **Implement `score_file(path: str) -> int`** in `diff_budget.py`:

   ```python
   def score_file(path: str) -> int:
       if any(p in path for p in NON_UI_PATTERNS):
           return 0
       ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
       if ext in UI_PRIMARY_EXTS and any(path.startswith(d) for d in UI_PRIMARY_DIRS):
           return 2
       if ext in UI_SECONDARY_EXTS:
           return 1
       return 0
   ```

2. **Implement `budget_diff_files`**:
   - Sort `diff_files` by score descending.
   - Assign budgets: score 2 → `PRIMARY_CHARS`, score 1 → `SECONDARY_CHARS`, score 0 → `NON_UI_CHARS`.
   - Walk the sorted list, accumulating chars. For each file:
     - If remaining total budget allows, include up to its tier budget.
     - If tier budget exceeds available total, truncate to available.
     - If score 0, replace patch with `"# {path} changed (non-UI, omitted from budget)"`.
   - Return `(budgeted_list, total_budget < original_total)`.

3. **Update `step_generation.generate_steps_from_diff`**: Replace the current inline truncation:

   ```python
   # BEFORE
   diff_text = json.dumps(diffs_for_prompt, ensure_ascii=False)
   if len(diff_text) > MAX_DIFF_CHARS:
       diff_text = diff_text[:MAX_DIFF_CHARS]

   # AFTER
   from app.steps.diff_budget import budget_diff_files
   budgeted, was_truncated = budget_diff_files(diffs_for_prompt)
   diff_text = json.dumps(budgeted, ensure_ascii=False)
   if was_truncated:
       print("[steps.step_generation] diff budget applied", flush=True)
   ```

4. **Update `should_skip_llm_for_size`** threshold check: After budgeting, the diff text will be at most `MAX_BUDGET_CHARS` + JSON overhead (~2 KB). The skip-LLM guard at `12_000` chars in `llm_guards.py` remains correct and does not need changing.

5. **Keep `MAX_PATCH_CHARS = 3000` in `pr_extraction.py`** as an upper bound on individual raw patches fetched from GitHub. The budget layer operates downstream and further reshapes the allocation.

### Risks / Edge Cases

- **Monorepos**: Many non-UI files could crowd out the primary files if scoring is too permissive. The sort-by-score-descending approach ensures primaries always consume their budget first.
- **Small PRs (1–3 files)**: `budget_diff_files` is a no-op if total content is under `MAX_BUDGET_CHARS`. The original patches pass through unchanged.
- **Renamed files**: `status == "renamed"` files should inherit the score of their new path. Handle in `score_file` by inspecting the `path` key.
- **Zero-UI PRs**: If all files score 0, `budget_diff_files` returns non-UI summary lines. The LLM will likely produce minimal or fallback steps — this is correct behavior.

### Verification

**Unit tests** (`tests/test_diff_budget.py`):

```python
def test_primary_file_gets_full_budget():
    files = [{"path": "app/pricing/page.tsx", "status": "modified", "patch": "x" * 5000}]
    budgeted, _ = budget_diff_files(files)
    assert len(budgeted[0]["patch"]) <= 4000

def test_non_ui_file_is_summarized():
    files = [{"path": "package.json", "status": "modified", "patch": "x" * 500}]
    budgeted, _ = budget_diff_files(files)
    assert "non-UI" in budgeted[0]["patch"] or len(budgeted[0]["patch"]) <= 200

def test_primary_before_secondary_in_output():
    files = [
        {"path": "lib/utils.ts",        "status": "modified", "patch": "a" * 1000},
        {"path": "app/page.tsx",         "status": "modified", "patch": "b" * 1000},
    ]
    budgeted, _ = budget_diff_files(files)
    assert budgeted[0]["path"] == "app/page.tsx"

def test_total_output_within_budget():
    files = [{"path": f"app/page{i}.tsx", "status": "modified", "patch": "z" * 5000} for i in range(10)]
    budgeted, was_truncated = budget_diff_files(files)
    total = sum(len(f["patch"]) for f in budgeted)
    assert total <= 10_000 + 200  # small margin for JSON overhead
    assert was_truncated is True
```

---

## Phase 6 — Repair-Path Robustness Parity

### Goal

`app/llm/step_generator.py`'s `generate_next_steps()` calls Azure OpenAI with `response_format: json_schema` but has no fallback if the deployment does not support structured output. The main generation path (`step_generation._call_llm`) has a `BadRequestError` → `json_object` fallback. The repair path is missing the same safety net.

This phase brings `generate_next_steps()` to full parity with `_call_llm` and also adds missing spend tracking.

### Exact Files

| File | Change |
|------|--------|
| `app/llm/step_generator.py` | Add `json_object` fallback; add spend recording |
| `app/llm_guards.py` | No change — `record_spend` already works; just needs to be called |

### Step-by-Step Implementation Tasks

1. **Extract `_call_with_fallback` helper** in `step_generator.py` (mirrors the existing pattern in `step_generation.py`):

   ```python
   def _call_with_fallback(client, deployment: str, messages, max_tokens: int, schema: dict) -> dict:
       try:
           from openai import BadRequestError  # type: ignore
       except ImportError:
           BadRequestError = Exception  # type: ignore

       try:
           completion = client.chat.completions.create(
               model=deployment,
               messages=messages,
               temperature=0.2,
               max_tokens=max_tokens,
               response_format={"type": "json_schema", "json_schema": schema},
           )
           return completion, json.loads(completion.choices[0].message.content or "{}")
       except BadRequestError:
           pass
       except Exception as e:
           err = str(e).lower()
           if not any(kw in err for kw in ("json_schema", "response_format", "unsupported", "invalid_request_error")):
               raise

       # Fallback to json_object
       completion = client.chat.completions.create(
           model=deployment,
           messages=messages,
           temperature=0.2,
           max_tokens=max_tokens,
           response_format={"type": "json_object"},
       )
       content = (completion.choices[0].message.content or "{}").strip()
       s, e = content.find("{"), content.rfind("}")
       if s != -1 and e > s:
           content = content[s:e+1]
       return completion, json.loads(content)
   ```

2. **Replace the direct `.create()` call** in `generate_next_steps` with `_call_with_fallback`.

3. **Add spend tracking** after the call:

   ```python
   from app.llm_guards import record_spend
   ...
   completion, data = _call_with_fallback(client, deployment, messages, 700, schema)
   usage = getattr(completion, "usage", None)
   if usage:
       record_spend(
           getattr(usage, "prompt_tokens", 0) or 0,
           getattr(usage, "completion_tokens", 0) or 0,
       )
   ```

4. **Add empty-response guard**: If `data.get("steps")` is empty or missing after both attempts, raise `RuntimeError("generate_next_steps: LLM returned no steps after fallback")` so `retry_engine.regenerate_with_feedback` records the failure rather than silently returning `[]`.

### Risks / Edge Cases

- **`json_object` mode + `minItems: 1` schema**: The `json_object` fallback does not enforce the schema. The returned `steps` array may be empty or malformed. Guard with the existing `if not steps: return []` check and the new empty-response guard above.
- **Spend double-counting on fallback**: The helper makes at most two API calls. Record spend for whichever call succeeds — do not record both. The current implementation already does this correctly since the fallback path only fires if the first call raises.

### Verification

**Unit test**:

```python
def test_generate_next_steps_falls_back_on_bad_request(monkeypatch):
    # First call raises BadRequestError, second returns valid json_object
    ...
    steps = generate_next_steps(objective={}, dom_context=minimal_ctx)
    assert len(steps) > 0

def test_spend_is_recorded(monkeypatch):
    with patch("app.llm_guards.record_spend") as mock_spend:
        generate_next_steps(objective={}, dom_context=minimal_ctx)
    mock_spend.assert_called_once()
```

---

## Phase 7 — Dead-Code Removal and Capture Config Wiring

### Goal

Two long-standing issues in `step_execution.py` waste developer mental bandwidth and mask real config behaviour:

1. `run_capture()` returns at line 737 after the stepwise path. Everything below that return — about 250 lines of retry logic, visual fallback, and stability checks — is **unreachable dead code**. Maintainers reading those comments assume the system behaves as documented there.
2. The active stepwise path in `step_runner.run_stepwise` hardcodes `viewport={"width": 1366, "height": 900}` and `full_page=False`, ignoring the `CaptureSettings` loaded from `project_config.json`.

### Exact Files

| File | Change |
|------|--------|
| `app/steps/step_execution.py` | Remove unreachable block after line 737; preserve type stubs if needed |
| `app/execution/step_runner.py` | Accept `CaptureSettings` as a parameter; use for viewport and screenshot mode |

### Step-by-Step Implementation Tasks

1. **In `step_execution.run_capture`**: Delete everything from line 739 (`for old_shot in out_dir.glob(...)`) to the end of the function. The two `return` statements at lines 724–737 already cover both outcomes. Add a comment explaining the legacy block was removed.

2. **Update `run_stepwise` signature** to accept `CaptureSettings`:

   ```python
   from app.steps.step_execution import CaptureSettings  # avoid circular: move CaptureSettings to app/config_types.py

   def run_stepwise(
       *,
       preview_url: str,
       initial_steps,
       objective,
       screenshot_dir,
       max_retries_per_failure: int = 3,
       capture_settings: Optional[CaptureSettings] = None,  # NEW
   ) -> Dict[str, Any]:
   ```

   Move `CaptureSettings` to `app/config_types.py` to break the potential circular import between `step_execution` and `step_runner`.

3. **Use `capture_settings` in `run_stepwise`**:

   ```python
   cs = capture_settings or CaptureSettings()
   page = browser.new_page(viewport={"width": cs.viewport_width, "height": cs.viewport_height})
   ...
   # In _execute_one for screenshot action:
   page.screenshot(path=str(path), full_page=cs.full_page_screenshots)
   ```

   Thread `capture_settings` into `_execute_one` as a parameter.

4. **In `run_capture`**: Pass the loaded settings:

   ```python
   capture_settings = _load_capture_settings()
   stepwise = run_stepwise(
       preview_url=preview_url,
       initial_steps=steps,
       objective=objective,
       screenshot_dir=out_dir,
       max_retries_per_failure=MAX_STEP_RETRIES,
       capture_settings=capture_settings,     # NEW
   )
   ```

5. **Move `CaptureSettings` dataclass** from `step_execution.py` to a new `app/config_types.py`. Update the single import in `step_execution.py`.

### Risks / Edge Cases

- **Dead-code removal is irreversible** if the visual-fallback logic is ever needed again. Before deleting, confirm via `git log` that the block has not been actively modified in recent history. Archive it in a `docs/archive/legacy_executor.py` comment file if preservation of the approach is desired.
- **Circular import**: `step_execution` currently imports `run_stepwise` from `step_runner`. Moving `CaptureSettings` to `config_types.py` breaks the cycle cleanly.
- **`full_page=False` hardcode in `_execute_one`**: After this phase, screenshots will use `full_page_screenshots=True` as set in `project_config.json`. This will affect frame sizes in `render.py`. Confirm the slideshow renderer handles full-page screenshots (FFmpeg `-vf scale` step).

### Verification

```python
def test_stepwise_uses_config_viewport(monkeypatch):
    cs = CaptureSettings(viewport_width=1920, viewport_height=1080, full_page_screenshots=True)
    with patch("playwright.sync_api.Browser.new_page") as mock_page:
        run_stepwise(preview_url="http://localhost:3000", initial_steps=[], objective={},
                     screenshot_dir=Path("/tmp"), capture_settings=cs)
    mock_page.assert_called_with(viewport={"width": 1920, "height": 1080})
```

**Manual check**: Set `"full_page_screenshots": true` in `project_config.json`. Run a demo. Confirm output screenshots capture below-the-fold content.

---

## Phase 8 — Trigger Filtering and Config Discipline

### Goal

`project_config.json` is loaded by `load_config()` without schema validation. An invalid or partially-filled config silently falls through to defaults scattered across modules. Glimpse's `zod`-backed config schema catches these at startup.

Additionally, the trigger `include`/`exclude` patterns in `project_config.json` are applied in `generate_steps_from_diff` but not in the initial webhook filter, meaning the pipeline runs for non-UI changes and wastes crawl time.

This phase adds:

1. Config schema validation with clear error messages.
2. A `should_run_for_diff(diff_files, config)` function that applies trigger rules before any crawl or LLM call.

### Exact Files

| File | Change |
|------|--------|
| `app/config.py` | Add `validate_config()` and typed config loading |
| `app/trigger.py` | **New** — `evaluate_trigger()` port of Glimpse's logic in Python |
| `app/steps/pipeline.py` | Call `evaluate_trigger()` before `generate_steps_from_diff` |
| `app/steps/step_generation.py` | Remove inline `trigger.include/exclude` checking (moved to `trigger.py`) |

### Data Structures to Introduce

```python
# app/trigger.py
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TriggerDecision:
    should_run: bool
    reason: str
    matched_files: List[str]
    general_demo: bool = False  # True when config/workflow file changed

UI_PRIMARY_EXTS = {".tsx", ".jsx", ".vue", ".svelte"}
UI_SECONDARY_EXTS = {".ts", ".js", ".css", ".scss", ".html"}

def is_ui_file(path: str) -> bool: ...

def evaluate_trigger(
    diff_files: List[Dict[str, str]],
    config: Dict[str, Any],
    *,
    force: bool = False,
) -> TriggerDecision: ...
```

### Step-by-Step Implementation Tasks

1. **Port `isUIFile` heuristic** from `third_party/git-glimpse/packages/core/src/analyzer/diff-parser.ts` to Python in `app/trigger.py`. Use the same extension and directory lists.

2. **Implement `evaluate_trigger`** using the same three-tier logic as Glimpse:
   - `force=True` → always run.
   - Mode `on-demand` → skip unless forced.
   - Mode `smart` → count additions+deletions in matched UI files; skip if below `threshold`.
   - Default mode → run if any UI file matched.
   - Return `TriggerDecision`.

3. **Add `validate_config(cfg: dict) -> dict`** in `app/config.py`:
   - Check required keys: `project_name`, `preview_url_template`.
   - Validate `capture.viewport.width/height` are positive ints.
   - Validate `trigger.mode` is one of `{"auto", "on-demand", "smart"}`.
   - Validate `routeMap` values are strings or lists of strings.
   - Raise `ConfigValidationError` (new exception class in `app/config.py`) with a human-readable message listing all failures.

4. **Call `validate_config` in `load_config()`** after loading the JSON. Catch `ConfigValidationError` and log a clear warning (do not raise — keep the system running with defaults so a config typo does not brick the webhook handler).

5. **Call `evaluate_trigger` in `pipeline.py`** before `generate_steps_from_diff`:

   ```python
   from app.trigger import evaluate_trigger, TriggerDecision

   decision = evaluate_trigger(diff_files, config, force=webhook_payload.get("force", False))
   if not decision.should_run:
       print(f"[pipeline] skipping: {decision.reason}", flush=True)
       return {"skipped": True, "reason": decision.reason}
   ```

6. **Remove redundant `trigger.include/exclude` filtering** currently done inline in `step_generation.py` (since it now happens earlier and consistently).

### Risks / Edge Cases

- **`on-demand` mode**: Ensure the `/glimpse` comment webhook path bypasses `evaluate_trigger` or sets `force=True`. Do not apply the skip logic to explicit comment triggers.
- **Config migration**: Existing `project_config.json` files may be missing the `trigger.mode` field. Default to `"auto"` if absent — do not raise.
- **`general_demo` flag**: When a diff changes only config files, `evaluate_trigger` returns `general_demo=True`. Wire this into `generate_steps_from_diff` to skip DOM-grounding on diff-specific routes and use a homepage-only crawl.

### Verification

**Unit tests** (`tests/test_trigger.py`):

```python
def test_no_ui_files_skips():
    files = [{"path": "README.md", "status": "modified", "patch": ""}]
    d = evaluate_trigger(files, {"trigger": {"mode": "auto"}})
    assert d.should_run is False

def test_primary_ui_file_runs():
    files = [{"path": "app/pricing/page.tsx", "status": "modified", "patch": ""}]
    d = evaluate_trigger(files, {"trigger": {"mode": "auto"}})
    assert d.should_run is True

def test_smart_mode_below_threshold_skips():
    files = [{"path": "app/page.tsx", "status": "modified", "patch": "+a\n+b\n+c\n"}]
    cfg = {"trigger": {"mode": "smart", "threshold": 10}}
    d = evaluate_trigger(files, cfg)
    assert d.should_run is False

def test_force_always_runs():
    files = [{"path": "README.md", "status": "modified", "patch": ""}]
    d = evaluate_trigger(files, {"trigger": {"mode": "auto"}}, force=True)
    assert d.should_run is True

def test_config_validation_bad_mode():
    with pytest.raises(ConfigValidationError):
        validate_config({"trigger": {"mode": "invalid"}})
```

---

## Dependency Graph

```
Phase 1 (Schema)
    └── Phase 2 (Multi-route crawl)      — depends on unified button shape
    └── Phase 4 (Selector existence)     — depends on canonical field names
    └── Phase 6 (Repair parity)          — depends on canonical field names in step_generator

Phase 3 (Nav detection)                  — independent; can merge any time after Phase 1

Phase 5 (Diff budgeting)                 — independent; no schema dependency

Phase 7 (Dead code / config wiring)
    └── Phase 4                          — viewport feeds into screenshot mode

Phase 8 (Trigger + config)              — independent; best done last to not block earlier phases
```

Phases 3, 5, and 8 are fully independent of each other and of the schema phases. Phases 1 → 2 → 4 form the primary accuracy chain and should land in order.

---

---

## Critical Review — Hidden Dependencies, Reordering, and Precision Issues

> This section documents every concrete defect found during code-level review of the roadmap. Each item includes the exact source location, the actual problem, and the precise fix. Apply these corrections before executing any phase.

---

### D1 — Phase 5 and Phase 8 independently define the same `is_ui_file` scoring logic (hidden dependency)

**Severity: High. Will produce two diverging implementations of the same rule.**

Phase 5 introduces `app/steps/diff_budget.py` with `score_file()` using `UI_PRIMARY_EXTS`, `UI_SECONDARY_EXTS`, and `NON_UI_PATTERNS`. Phase 8 introduces `app/trigger.py` with `is_ui_file()` using the same extension/directory constants. If Phase 5 lands first, Phase 8 will reinvent the same wheel with slightly different thresholds, and the two will silently diverge over time.

**Fix:** Move Phase 8 before Phase 5 in execution order. In Phase 5, import `is_ui_file` from `app/trigger.py` rather than re-declaring the extension sets. The `score_file` function becomes a thin wrapper that calls `is_ui_file` for tier 1/2 classification. This also means Phase 8 must land before Phase 5 can be opened as a PR.

**Updated order:** 1 → 8 → 2 → 4 → 5 → 6 → 3 → 7 (see Corrected Execution Order section below).

---

### D2 — Phase 6's `RuntimeError` guard silently breaks `retry_engine.py` (hidden dependency)

**Severity: High. Will cause unhandled exceptions in the active repair loop.**

Phase 6, Task 4 says:

> "If `data.get('steps')` is empty or missing after both attempts, raise `RuntimeError`."

But `retry_engine.regenerate_with_feedback` (line 29–32 of `app/llm/retry_engine.py`) handles empty steps with a `continue`:

```python
if not steps:
    attempts.append({"attempt": i, "status": "empty_steps"})
    previous_error = {"error": "LLM returned empty steps"}
    continue
```

If `generate_next_steps` raises instead of returning `[]`, the `if not steps:` guard is never reached. The exception propagates uncaught through `regenerate_with_feedback` and up into `run_stepwise`, where there is no `try/except` around the `regenerate_with_feedback` call. The repair loop fails hard instead of exhausting retries gracefully.

**Fix:** Phase 6 must also update `retry_engine.regenerate_with_feedback` to wrap the `generate_next_steps(...)` call in a `try/except RuntimeError` and treat the exception as an empty-steps case:

```python
try:
    steps = generate_next_steps(...)
except RuntimeError as e:
    attempts.append({"attempt": i, "status": "generation_error", "error": str(e)})
    previous_error = {"error": str(e)}
    continue
```

Add `app/llm/retry_engine.py` to the Phase 6 "Exact Files" table.

---

### D3 — Phase 7 task order is inverted: the `CaptureSettings` move is listed as Task 5 but referenced in Task 2 (implementation blocker)

**Severity: High. Phase 7 is not executable in the stated order.**

Task 2 of Phase 7 contains this inline comment:

> "Move `CaptureSettings` to `app/config_types.py` to break the potential circular import between `step_execution` and `step_runner`."

Task 2 also immediately uses `from app.steps.step_execution import CaptureSettings` — which creates the very circular import it is trying to avoid if the move has not happened yet. Task 5 is where the move is actually described, but by then Task 2 has already written broken import code.

**Fix:** Reorder Phase 7 tasks:

1. (**New Task 1**) Create `app/config_types.py`. Move `CaptureSettings` dataclass into it. Update `step_execution.py` to import from `app/config_types`.
2. (**Old Task 2**) Update `run_stepwise` signature to accept `capture_settings: Optional[CaptureSettings] = None` — now importable from `app/config_types` without circularity.
3. (**Old Task 3**) Use settings in `run_stepwise` / thread into `_execute_one`.
4. (**Old Task 4**) Update `run_capture` to pass settings.
5. (**Old Task 1 → now Task 5**) Delete dead code block in `run_capture`.

---

### D4 — Phase 4 incorrectly claims text-based clicks are already live-validated (accuracy hazard)

**Severity: Medium. Leaves a gap the roadmap claims to close.**

Phase 4, Task 2 says:

> "if text: # Existence already confirmed by `_known_button_texts` check above"

This is false. `_known_button_texts(dom_ctx)` in `selector_validator.py` (line 14–25) reads from the `dom_ctx` dictionary passed in memory — it is static, not live. It reflects what the DOM looked like during the last `extract_dom_context` call, which may be stale if the page changed between the last re-anchor and the current step.

The same argument that justifies the selector existence check applies equally to text-based clicks: the text that was in `dom_ctx` when the context was captured may no longer be present if the SPA has re-rendered.

**Fix:** Remove the pass-through comment. When `page` is provided, add a parallel live text check:

```python
if text and page is not None:
    try:
        count = page.get_by_text(text, exact=True).count()
    except Exception:
        count = 0
    if count == 0:
        return False, f"text_not_found_on_live_page:{text}"
```

---

### D5 — Phase 1 silently drops the `title` attribute fallback when renaming `label` → `aria` (semantic regression)

**Severity: Medium. Affects buttons that use `title` instead of `aria-label`.**

Phase 1, Task 3 says to change the JS in `dom_extractor.py` from:

```js
label: e.getAttribute('aria-label') || e.getAttribute('title') || "",
```

to:

```js
aria: e.getAttribute('aria-label') || e.getAttribute('title') || "",
```

This is a rename, not a semantic fix. It still conflates `aria-label` with `title` into one field. That conflation is already a problem — `title` is a tooltip, not an accessible label, and passing it as `[aria-label='...']` in a selector will fail if the element only has a `title` attribute.

**Fix:** Separate the two sources during collection and store them distinctly. Add a `title` field to `ButtonCandidate`:

```python
class ButtonCandidate(TypedDict):
    ...
    aria: str    # aria-label only
    title: str   # title attribute only (tooltip fallback)
```

Update the JS eval in `dom_extractor`:

```js
aria:  e.getAttribute('aria-label') || "",
title: e.getAttribute('title') || "",
```

In `_build_action_menu`, prefer `aria` for selector generation; use `title` only for display text, never for a selector.

---

### D6 — Phase 3 JS selector list is inconsistent with its own comment (silent fingerprint narrowing)

**Severity: Medium. The fingerprint will miss native HTML landmark elements.**

Phase 3's `_collect_page_fingerprint` implementation queries:

```js
document.querySelectorAll('[role=main],[role=dialog],[role=navigation],main,nav,header,footer,aside')
```

But the `PageFingerprint.landmark_count` docstring says:

> "count of [role=main], `<main>`, `<nav>`, `<header>`, `<footer>`"

The implementation actually does include native elements, but the comment omits `aside` and `[role=dialog]`, making it unclear what the threshold of `>= 2` is calibrated against. More importantly, apps that use only native `<nav>` and `<header>` without ARIA roles will be correctly counted, but apps that use `<div role="banner">` will not — `role=banner` is a valid landmark but absent from the list.

**Fix:** Extend the landmark selector to include all ARIA landmark roles that indicate structural page change:

```js
const LANDMARK_SEL = '[role=main],main,[role=dialog],[role=alertdialog],'
    + '[role=navigation],nav,[role=banner],header,[role=contentinfo],footer,'
    + '[role=complementary],aside,[role=region]';
const landmarks = document.querySelectorAll(LANDMARK_SEL).length;
```

Update the docstring to match. Recalibrate the `>= 2` threshold in a unit test using this extended selector.

---

### D7 — Phase 4 proposes two conflicting lazy-load strategies in the same Risks block

**Severity: Low-Medium. Implementer must guess which to use.**

The "Lazy-loaded / below-fold elements" risk entry proposes a scroll + `time.sleep(0.3)` re-check. The very next bullet ("Dynamic loading") proposes `page.wait_for_selector(selector, state='attached', timeout=2000)` instead. These are mutually exclusive patterns for the same problem.

**Fix:** Replace both with a single ordered strategy inside `_selector_count_on_page`:

```python
def _selector_count_on_page(page: Page, selector: str) -> int:
    try:
        # Fast path: already in DOM
        count = page.locator(selector).count()
        if count > 0:
            return count
        # Slow path: wait briefly for late-rendering elements
        page.wait_for_selector(selector, state="attached", timeout=1500)
        return page.locator(selector).count()
    except Exception:
        return 0
```

Drop the `window.scrollTo` approach entirely — it is side-effectful (changes page scroll position) and unreliable for virtualized lists.

---

### D8 — Phase 7 identifies a `render.py` risk but gives no fix (vague / dangling)

**Severity: Medium. The change will break `render_video()` silently on full-page screenshots.**

Phase 7 notes:

> "Confirm the slideshow renderer handles full-page screenshots (FFmpeg `-vf scale` step)."

Reading `app/render.py` directly: there is **no** `-vf scale` step. The FFmpeg concat command at lines 44–62 sends all input frames into a concat filter assuming they have the same dimensions. Once `full_page_screenshots: true` is active via Phase 7, `shot1.png` might be 1366×900 and `shot2.png` might be 1366×4200 (a long scrollable page). FFmpeg's concat filter requires identical dimensions; mismatched heights produce the error `Input link in0:v0 parameters (size 1366x4200...) do not match the corresponding output link`.

**Fix:** Add the concrete remedy to Phase 7's tasks:

> Task 6 (new): In `render.py`, add a scale + pad filter to normalize all frames to the viewport dimensions before concat:
> ```python
> scale_filter = f"[{i}:v]scale={cs.viewport_width}:{cs.viewport_height}:force_original_aspect_ratio=decrease,pad={cs.viewport_width}:{cs.viewport_height}:(ow-iw)/2:(oh-ih)/2[v{i}]"
> ```
> Replace the bare `concat_inputs` approach with scaled pads before the concat. Add `app/render.py` to the Phase 7 Exact Files table.

---

### D9 — Phase 5 `score_file` misses `src/app/` paths for Next.js apps (scoring bug)

**Severity: Medium. Primary files will be scored as secondary for apps using the `src/` directory convention.**

The `score_file` function uses:

```python
if ext in UI_PRIMARY_EXTS and any(path.startswith(d) for d in UI_PRIMARY_DIRS):
    return 2
```

where `UI_PRIMARY_DIRS = {"app/", "src/components/", "src/pages/", ...}`.

For a Next.js project using the `src/app/` router convention, a file like `src/app/pricing/page.tsx` starts with `src/` — which is in `UI_SECONDARY_DIRS`, not `UI_PRIMARY_DIRS`. It will be scored 1 (secondary) instead of 2 (primary), halving its character budget.

**Fix:** Add `"src/app/"` and `"src/pages/"` to `UI_PRIMARY_DIRS`:

```python
UI_PRIMARY_DIRS = {
    "app/", "src/app/",
    "src/components/", "components/",
    "src/pages/", "pages/",
    "src/routes/", "routes/",
}
```

Also add a unit test that covers this:

```python
def test_src_app_scores_primary():
    assert score_file("src/app/pricing/page.tsx") == 2
```

---

### D10 — Phase 6 `_call_with_fallback` return type annotation is wrong (type error)

**Severity: Low. Will cause type-checker failures on CI if mypy/pyright is run.**

The function signature in Phase 6 is declared as:

```python
def _call_with_fallback(client, deployment: str, messages, max_tokens: int, schema: dict) -> dict:
```

But the function returns a **tuple** `(completion, parsed_dict)`, not a bare dict. The call site in Phase 6 Task 3 also destructures it correctly with `completion, data = _call_with_fallback(...)`.

**Fix:** Change the return annotation to `Tuple[Any, Dict[str, Any]]` and add the import `from typing import Tuple, Any, Dict`.

---

### D11 — Phase 2 "auth wall detection" heuristic is undefined (vague)

**Severity: Medium. The risk is noted but the implementation is not actionable.**

Phase 2 Risk section says:

> "Guard against this by checking if the route's discovered buttons look identical to the home page (sign-in form detected)."

"Look identical" is not implementable. There is no definition of what "identical" means (same testid set? same button text set? same URL after redirect?).

**Fix:** Replace with a concrete two-step guard:

1. After `page.goto(route)`, check `page.url`. If the response URL contains `/login`, `/signin`, `/auth`, or `/unauthorized`, skip the route: `if any(p in page.url for p in ("/login", "/signin", "/auth", "/unauthorized")): continue`.
2. Additionally, compare the response URL's path against the requested path: `if page.url.rstrip("/") != (staging_url.rstrip("/") + route).rstrip("/"):`  — a redirect has occurred, skip.

---

### D12 — Phase 8 `general_demo` flag has no implementation task (incomplete spec)

**Severity: Medium. The flag is defined but never wired.**

Phase 8, Risks section says:

> "Wire this into `generate_steps_from_diff` to skip DOM-grounding on diff-specific routes and use a homepage-only crawl."

There is no corresponding numbered implementation task in Phase 8. The `TriggerDecision.general_demo` field is defined, `evaluate_trigger` returns it, but nothing consumes it. `generate_steps_from_diff` does not accept a `general_demo` parameter.

**Fix:** Add Task 7 to Phase 8:

> **Task 7**: Add `general_demo: bool = False` parameter to `generate_steps_from_diff` in `app/steps/step_generation.py`. When `True`, call `crawl_dom_data(staging_url, seed_routes=None, max_routes=1)` (homepage only) and skip `_extract_routes_from_diff`. Propagate from `pipeline.py`: `generate_steps_from_diff(..., general_demo=decision.general_demo)`.

---

### D13 — Dependency graph incorrectly labels Phase 3 as dependent on Phase 1

**Severity: Low. Causes unnecessary sequencing.**

The dependency graph states:

```
Phase 3 (Nav detection) — independent; can merge any time after Phase 1
```

Phase 3 only modifies `app/execution/navigation_detector.py` and describes no change to field reading from `DomSnapshot`. It uses `page.evaluate()` directly, not the schema types. There is no actual dependency on Phase 1.

**Fix:** Remove the "after Phase 1" constraint:

```
Phase 3 (Nav detection) — fully independent; can merge at any point
```

This opens Phase 3 as a safe quick win that can be shipped in parallel with Phase 1 review.

---

### D14 — Phase 2 "shared Page" approach has an undocumented SPA state risk

**Severity: Low-Medium. Worth noting before implementation.**

Phase 2, Task 2 says "visit each seed route using a shared `Page`". Navigating a single `Page` object between routes via `page.goto()` clears JavaScript heap state, localStorage events, and any in-memory Redux/Zustand store. For most SPAs this is fine (they reinitialise on navigation), but apps that rely on sessionStorage for auth tokens will lose them on cross-origin navigations or on some same-origin soft-nav implementations.

**Fix:** Add an explicit note to the task:

> Use a shared `BrowserContext` but **create a new `Page` per route** (`await context.new_page()`, then `await page.close()` after collection) to avoid sessionStorage bleed between visits. The context retains cookies and localStorage across pages.

---

### D15 — Phase 8 and Phase 5 both modify `step_generation.py` — merge conflict risk not called out

**Severity: Low. Practical risk during execution.**

Phase 5, Task 3 replaces the inline truncation block in `step_generation.py`. Phase 8, Task 6 removes the inline `trigger.include/exclude` filtering in the same file. If these PRs are open simultaneously or if Phase 5 lands while Phase 8 is in review, both touch adjacent lines in `generate_steps_from_diff` and will require a manual merge resolution.

**Fix:** Add to both phases' Risks sections:

> "This phase modifies `step_generation.generate_steps_from_diff`. If Phase [5/8] is also in review simultaneously, coordinate merge order to avoid conflicts on the same function body."

---

### Corrected Execution Order

Based on the above, the safe execution order with resolved dependencies is:

```
Phase 1  (Schema unification)
    └── Phase 8  (Trigger + config — must precede Phase 5 to avoid is_ui_file duplication)
        └── Phase 2  (Multi-route crawl — uses Phase 1 schema)
            └── Phase 4  (Selector existence — modifies step_runner.py before Phase 7 does)
                └── Phase 5  (Diff budgeting — imports is_ui_file from Phase 8's trigger.py)
                └── Phase 6  (Repair parity — also updates retry_engine.py per D2)
                └── Phase 7  (Config wiring + dead code — modifies step_runner.py last)

Phase 3 is fully independent — open as a PR any time.
```

Revised Quick Reference table:

| New Order | Phase | Key Change from Original |
|-----------|-------|--------------------------|
| 1 | Unify DOM schema | Unchanged; add `title` field to `ButtonCandidate` (D5) |
| 2 | Trigger + config (was Phase 8) | Moved before Phase 5; provides `is_ui_file` for reuse |
| 3 | Multi-route crawl (was Phase 2) | Use per-route `new_page()`, not shared Page (D14); concrete auth redirect guard (D11) |
| 4 | Selector existence (was Phase 4) | Add live text check too (D4); single lazy-load strategy (D7) |
| 5 | Diff budgeting (was Phase 5) | Import `is_ui_file` from trigger.py (D1); fix `src/app/` scoring (D9) |
| 6 | Repair parity (was Phase 6) | Fix return type (D10); update retry_engine.py for RuntimeError (D2) |
| 7 | Nav detection (was Phase 3) | Extend landmark selector (D6); remove Phase 1 dependency (D13) |
| 8 | Config wiring + dead code (was Phase 7) | Fix task order — move CaptureSettings first (D3); fix render.py (D8) |

---

## Rollback Plan for Each Phase

| Phase | How to roll back |
|-------|------------------|
| 1 | Revert `dom_schema.py` and per-file field changes; all consumers tolerate old field names |
| 2 | Revert `crawl_dom_data` to single-page version; `seed_routes` param unused by callers |
| 3 | Revert `navigation_detector.py` to text-hash; no upstream changes required |
| 4 | Remove `page` param from `validate_step_against_dom`; default is `None` so no callsite breaks |
| 5 | Remove `diff_budget.py`; restore inline truncation in `step_generation.py` |
| 6 | Revert `step_generator.py`; no external callers depend on spend tracking |
| 7 | Revert dead-code deletion from git history; restore `CaptureSettings` import |
| 8 | Revert `trigger.py` and `pipeline.py` changes; restore inline trigger checks in step_generation |
