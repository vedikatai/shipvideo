# Execution Plan — shipvideo-engine Accuracy Improvements

**6 phases · each is a single shippable PR · ordered by ROI**

Phases must land in order 1 → 2 → 3. Phases 4, 5, 6 depend on 1–3 being merged but are
independent of each other and can be parallelised.

---

## Phase 1 — Unified DOM Schema

**Goal:** Every module that reads or writes DOM data uses the same field names. Eliminates the
silent `label`/`aria`/`testid` mismatch that causes `_build_action_menu()` to fall through to
brittle raw CSS selectors.

**Files**

| File | Action |
|------|--------|
| `app/dom_schema.py` | **Create** — `ButtonCandidate`, `LinkCandidate`, `InputCandidate`, `TestIdCandidate`, `DomSnapshot` TypedDicts |
| `app/steps/dom_crawler.py` | Emit full `ButtonCandidate` (keep `testid`, `aria`, `id`, `role`; stop stripping to `{text, selector}` only) |
| `app/context/dom_extractor.py` | Rename JS eval field `label` → `aria`; add separate `title` field |
| `app/generator/script_generator.py` | `_build_action_menu()`: read `b["testid"]` / `b["aria"]` directly |
| `app/llm/step_generator.py` | `available_buttons` construction: read `b["aria"]` not `b.get("label") or b.get("aria")` |
| `app/policy/selector_validator.py` | Remove aliasing; read canonical field names |

**Key tasks**

- Create `app/dom_schema.py` with the five TypedDicts. No logic.
- In `dom_crawler._collect_ui_elements`, preserve `testid`, `aria`, `id`, `role` on every `ButtonCandidate` instead of collapsing to `{text, selector}`.
- In `dom_extractor`, split `aria-label` and `title` into two separate fields; never conflate them (`aria` → `[aria-label='x']` selector; `title` is display-only).
- Update all five consumers to read canonical field names; delete `b.get("label") or b.get("aria")` aliasing everywhere it appears.
- Add return type annotations (`-> DomSnapshot`) on `crawl_dom_data` and `extract_dom_context`.

**Success criteria**

- `_build_action_menu()` produces `page.get_by_test_id(...)` entries for all buttons that carry a `data-testid`, confirmed by unit test.
- `grep -r 'b.get("label")' app/` returns zero results.
- All existing pipeline tests pass unchanged (schema change is backward-compatible for callers that read `selector` or `text`).

---

## Phase 2 — Trigger Filtering + Config Validation

**Goal:** Stop wasting crawl time and LLM budget on non-UI diffs. Provide a single `is_ui_file`
classifier that Phase 3 (diff budgeting) and the trigger logic share — preventing the two from
diverging independently.

**Files**

| File | Action |
|------|--------|
| `app/trigger.py` | **Create** — `TriggerDecision`, `is_ui_file()`, `score_file()`, `evaluate_trigger()` |
| `app/config.py` | Add `validate_config()` with `ConfigValidationError`; call in `load_config()` |
| `app/steps/pipeline.py` | Call `evaluate_trigger()` before `generate_steps_from_diff`; short-circuit on `should_run=False` |
| `app/steps/step_generation.py` | Remove inline `trigger.include/exclude` block (superseded by `evaluate_trigger`) |

**Key tasks**

- Implement `is_ui_file(path)` using the extension/directory heuristic from `third_party/git-glimpse/packages/core/src/analyzer/diff-parser.ts`. Cover `src/app/` paths (Next.js `src/` convention) explicitly.
- Implement `score_file(path) -> int` returning 2/1/0 (primary UI / secondary UI / non-UI). `UI_PRIMARY_DIRS` must include both `app/` and `src/app/`.
- Implement `evaluate_trigger(diff_files, config, *, force=False) -> TriggerDecision` with three modes: `auto` (run if any UI file matched), `smart` (run only if additions+deletions ≥ threshold), `on-demand` (skip unless forced). Return `TriggerDecision(should_run, reason, matched_files, general_demo)`.
- Add `validate_config`: check `trigger.mode` ∈ `{auto, on-demand, smart}`, `capture.viewport` values are positive ints, `routeMap` values are `str | List[str]`. Log warning on invalid; do not raise (config typo must not break the webhook handler).
- In `pipeline.py`: call `evaluate_trigger` after fetching the diff; return `{"skipped": True, "reason": ...}` when `should_run=False`. Pass `general_demo` flag through to `generate_steps_from_diff` so homepage-only crawl is used when no feature files changed.

**Success criteria**

- A PR touching only `README.md` produces `skipped=True` in auto mode.
- A PR touching `src/app/pricing/page.tsx` produces `should_run=True`.
- `validate_config({"trigger": {"mode": "invalid"}})` logs a warning and does not crash the server.
- Unit tests cover all three trigger modes plus `force=True` override.

---

## Phase 3 — Multi-Route DOM Crawl

**Goal:** Fix the largest single accuracy gap. Replace the single homepage crawl with a bounded
BFS seeded by diff-inferred routes and `routeMap`. The LLM stops generating hallucinated
selectors for routes it has never seen.

**Files**

| File | Action |
|------|--------|
| `app/steps/dom_crawler.py` | Replace `crawl_dom_data` with multi-route version |
| `app/steps/step_generation.py` | Compute `seed_routes` before calling `crawl_dom_data` |

**Key tasks**

- Add `seed_routes: Optional[List[str]] = None` and `max_routes: int = 6` parameters to `crawl_dom_data`.
- In `crawl_dom_data`: launch one `BrowserContext` (not a bare `Browser`); create a **new `page` per route** via `context.new_page()` + `page.close()` after collection — this preserves cookies/localStorage across visits without carrying scroll or overlay state between pages.
- Build visit order: diff-inferred routes first (call `_extract_routes_from_diff` already in `step_normalizer`), then `seed_routes`, then homepage-discovered links. Dedupe. Cap at `max_routes`.
- Per-route: call `_collect_ui_elements`; store in `route_snapshots: Dict[str, RouteSnapshot]` on `DomSnapshot`. Merge all per-route buttons/links/inputs/testids into top-level union fields (deduped by `testid` value or `text.lower()`). Existing consumers read only top-level fields and are unaffected.
- Auth-wall guard: after `page.goto(route)`, if `page.url` contains `/login`, `/signin`, `/auth`, or `/unauthorized`, skip that route's elements. Do not fail the whole crawl.
- Each route visit is capped at 12 s (`networkidle` timeout). Catch all exceptions per-route and continue.

**Success criteria**

- `crawl_dom_data` called with `seed_routes=["/billing"]` visits `/billing` before any unrelated homepage link, confirmed by log output `[dom] collecting UI elements url=.../billing`.
- A PR modifying `app/billing/page.tsx` produces generated steps with selectors present on `/billing`, not fallbacks from the home page.
- Total crawl time on 6 routes ≤ 75 s on a live staging URL (measured end-to-end in CI).

---

## Phase 4 — Live Selector Validation + Stable Nav Detection

**Goal (part A):** Selector validation must prove the element actually exists on the current page,
not just that the CSS syntax is well-formed. Prevents the "valid selector, wrong element" class
of mis-clicks.

**Goal (part B):** Replace the brittle body-text hash with a structural page fingerprint so
counter increments and toast messages no longer trigger a full replan.

**Files**

| File | Action |
|------|--------|
| `app/execution/navigation_detector.py` | Replace `_dom_signature` in `detect_major_change` with `PageFingerprint` |
| `app/policy/selector_validator.py` | Add `page: Optional[Page] = None` param; add `_selector_count_on_page` |
| `app/execution/step_runner.py` | Pass `page` into `validate_step_against_dom` and `regenerate_with_feedback` |
| `app/llm/retry_engine.py` | Accept `page: Optional[Page] = None`; forward to `validate_step_against_dom` |

**Key tasks**

- **Nav detection**: Add `PageFingerprint(path, title, heading_set, landmark_count, testid_set)`. Collect via single `page.evaluate()`: h1/h2 texts (max 5, sorted), testid values (max 20, sorted), count of `[role=main],main,[role=dialog],[role=alertdialog],[role=navigation],nav,[role=banner],header,[role=contentinfo],footer,aside`. `detect_major_change` triggers on: path change, title change, heading set change, testid set change, or `|landmark_count_delta| >= 2`. `wait_stable_after_navigation` continues using the raw `_dom_signature` loop (stability, not semantics).
- **Selector existence**: Add `_selector_count_on_page(page, selector) -> int`. Strategy: try `page.locator(selector).count()`; if 0, call `page.wait_for_selector(selector, state="attached", timeout=1500)` and recount. Return 0 on any exception. When `count == 0`, return `False, f"selector_not_found_on_page:{selector}"`.
- **Text existence**: When `page` is provided, also check text-based clicks via `page.get_by_text(text, exact=True).count()`. The static `_known_button_texts` check is a pre-filter; the live count is the authoritative gate.
- Pass `page` from `run_stepwise` into both `validate_step_against_dom` (line 78) and `regenerate_with_feedback` (lines 81, 97, 118). All calls default `page=None` so batch validators in `step_normalizer` are unaffected.

**Success criteria**

- A step with `[data-testid='nonexistent']` is rejected at validation time rather than failing at execution. Confirmed via unit test with mock page returning `count=0`.
- Running a demo on a page with a live incrementing counter produces zero spurious replans. Confirmed by checking `navigation.reanchored` log events are absent during a controlled test.
- Unit test: `detect_major_change` returns `False` when only body text changes; returns `True` when path or h1 changes.

---

## Phase 5 — Smarter Diff Budgeting

**Goal:** Replace the blunt 8000-char string slice with a ranked allocation that gives primary UI
files their full budget first, ensuring the LLM always sees the most relevant change context
even on large PRs.

**Files**

| File | Action |
|------|--------|
| `app/steps/diff_budget.py` | **Create** — `budget_diff_files()` using `score_file` from Phase 2's `app/trigger.py` |
| `app/steps/step_generation.py` | Replace inline truncation with `budget_diff_files()` call |

**Key tasks**

- Import `score_file` from `app/trigger` (created in Phase 2). Do not redefine extension sets.
- Implement `budget_diff_files(diff_files, *, total_char_budget=10_000) -> Tuple[List[Dict], bool]`. Sort by score descending. Per-file budgets: score 2 → 4000 chars, score 1 → 1200 chars, score 0 → replace patch with `"# {path} changed (non-UI, omitted)"`. Walk sorted list accumulating against total budget; truncate to remaining when per-file budget would overflow. Return `(budgeted_files, was_truncated)`.
- In `step_generation.generate_steps_from_diff`, replace:
  ```python
  diff_text = json.dumps(diffs_for_prompt)[:MAX_DIFF_CHARS]
  ```
  with:
  ```python
  budgeted, _ = budget_diff_files(diffs_for_prompt)
  diff_text = json.dumps(budgeted)
  ```
- Keep `MAX_PATCH_CHARS = 3000` in `pr_extraction.py` as a hard upstream cap. The budget layer reshapes allocation downstream.

**Success criteria**

- `budget_diff_files([10 × app/page*.tsx with 5000-char patches])` produces total output ≤ 10 200 chars and `was_truncated=True`.
- `budget_diff_files([{"path": "src/app/pricing/page.tsx", ...}])` scores that file as primary (score 2) and allocates 4000 chars, confirmed by unit test.
- On a large PR (>20 files), the generated steps reference selectors from the changed component files rather than producing fallback screenshot-only steps.

---

## Phase 6 — Repair Loop Hardening + Config Wiring

**Goal:** Make the two remaining sources of silent failure robust: the LLM repair path that
crashes on older Azure deployments, and the screenshot config that is loaded but ignored. Bundle
with dead-code removal to keep the PR atomic.

**Files**

| File | Action |
|------|--------|
| `app/config_types.py` | **Create** — move `CaptureSettings` dataclass here from `step_execution.py` |
| `app/steps/step_execution.py` | Remove ~250-line unreachable block after `return` on line 737; import `CaptureSettings` from `app/config_types` |
| `app/execution/step_runner.py` | Accept `capture_settings: Optional[CaptureSettings] = None`; use for viewport + `full_page` in `_execute_one` |
| `app/llm/step_generator.py` | Add `json_schema → json_object` fallback; add `record_spend` call |
| `app/llm/retry_engine.py` | Wrap `generate_next_steps` call in `try/except RuntimeError`; treat as retry-able |
| `app/render.py` | Add scale+pad filter before FFmpeg concat to normalise full-page screenshot heights |

**Key tasks**

- **Task 1 first**: Create `app/config_types.py`, move `CaptureSettings` into it, update `step_execution.py` import. This must land before any other task in this phase to avoid the circular import.
- In `step_runner.run_stepwise`: accept `capture_settings`; use `cs.viewport_width/height` for `browser.new_page(viewport=...)` and `cs.full_page_screenshots` in `_execute_one`'s screenshot action. Default to `CaptureSettings()` if `None`.
- In `step_execution.run_capture`: pass the loaded `CaptureSettings` into `run_stepwise`. Delete all code after the existing `return` at line 737.
- In `step_generator.generate_next_steps`: extract `_call_with_fallback(client, deployment, messages, max_tokens, schema) -> Tuple[Any, Dict]`; on `BadRequestError` or format-error string, retry with `response_format={"type": "json_object"}` + brace extraction. Add `record_spend(prompt_tokens, completion_tokens)` after the successful call. If both attempts return empty steps, raise `RuntimeError` (not return `[]`).
- In `retry_engine.regenerate_with_feedback`: wrap `generate_next_steps(...)` in `try/except RuntimeError as e`; append `{"attempt": i, "status": "generation_error", "error": str(e)}` and `continue`.
- In `render.py`: add a scale+pad filter per input frame before the concat filter to normalise all frames to `(viewport_width, viewport_height)`. Use `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2` per input.

**Success criteria**

- Setting `"full_page_screenshots": true` in `project_config.json` and triggering a demo produces correctly-sized screenshots and a valid `out.mp4` (no FFmpeg dimension-mismatch error).
- Mocking the Azure deployment to return `400 BadRequest` on `json_schema` mode still produces valid steps via the `json_object` fallback path, confirmed by unit test.
- `record_spend` is called exactly once per `generate_next_steps` invocation (including the fallback path), confirmed by unit test asserting `mock_spend.call_count == 1`.
- `grep -rn "unreachable\|dead code" app/steps/step_execution.py` returns zero results after deletion.

---

## Execution Order and Dependencies

```
Phase 1  ──► Phase 3  ──► Phase 4
  │
  └──► Phase 2  ──► Phase 3
         │
         └──► Phase 5  (imports score_file from Phase 2)

Phase 6  requires Phase 1 (CaptureSettings field names) and Phase 4 (step_runner.py changes
         must not conflict). Open Phase 6 only after Phase 4 is merged.

Phases 4, 5, 6 are independent of each other once Phases 1–3 are merged.
```

**Minimum viable sequence for correctness:**
`1 → 2 → 3 → 4 → 5 → 6`

**Fast-track if parallelising review:** Open Phase 4 and Phase 5 simultaneously once Phase 3 merges. Open Phase 6 once Phase 4 merges.

---

## Rollback

| Phase | Rollback |
|-------|---------|
| 1 | Revert `dom_schema.py`; restore old field access patterns in the five consumers |
| 2 | Revert `trigger.py`, `config.py`; restore inline filtering in `step_generation.py` |
| 3 | Revert `crawl_dom_data` to single-page version; `seed_routes` param has no callers |
| 4 | Remove `page` param (defaults `None`); revert `navigation_detector.py` to text-hash |
| 5 | Delete `diff_budget.py`; restore `diff_text[:MAX_DIFF_CHARS]` in `step_generation.py` |
| 6 | Revert `step_runner.py` and `step_generator.py`; restore `CaptureSettings` import path |
