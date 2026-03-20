Zero linter errors. Here is the complete audit report and summary of every fix.

---

## Audit Results

### What Was Correctly Implemented

- Multi-route BFS with `BrowserContext` + new page per route — correct
- `_merge_snapshots` deduplication across routes — correct logic
- `seed_routes` priority ordering (diff-inferred first, then routeMap) — correct per EXECUTION_PLAN.md
- `route_snapshots` as a backward-compatible extra key — correct
- `validate_against_dom` building `valid_selectors` from both `buttons[].selector` and `data_testids[]` — correct
- `general_demo` flag gating seed route computation — correct
- Post-crawl `mapped_routes` merge to ensure LLM's `real_routes` is complete — correct
- Per-route exception handling with crawl continuation — correct
- 12 s `networkidle` timeout — correct

---

### Bugs Found and Fixed

**1. Auth wall false positives — Critical (silent route drops)**

`_is_auth_wall(url)` used substring matching on the full URL, so `/authors`, `/authenticate`, and `?authToken=...` were incorrectly treated as auth walls and silently discarded.

**Fix:** `urlparse(url).path` extracts path only, then checks the first path segment against a frozenset. `/authors` → first segment `authors` → not in frozenset → passes. `/auth/callback` → first segment `auth` → blocked correctly.

---

**2. `"/"` not guaranteed when seed_routes fills `max_routes` — Critical**

When 6 diff-inferred routes filled the cap, the `return order` early exit ran before the `"/" not in seen` guarantee at the bottom. The homepage was never crawled. Since the homepage contains the global navigation menu (the most commonly clicked elements), the LLM had no grounding for those elements.

**Fix:** Changed `return` to `break` in the seed loop and introduced `effective_max = max_routes - 1` when `"/"` is not already a seed, reserving one slot. `"/"` is unconditionally appended at the end if not already visited.

---

**3. `validate_against_dom` selector quote mismatch — Critical (silent step rejections)**

The crawler's `_short_selector` generates `[data-testid='btn']` (single quotes). The LLM's JSON output uses `[data-testid="btn"]` (double quotes). The set membership check `selector in valid_selectors` was an exact string comparison, so valid steps were silently dropped.

**Fix:** Added `_normalize_selector_quotes()` in `step_normalizer.py` using a regex to canonicalize double-quote attribute selectors to single-quote form before the set lookup.

---

**4. JS slice(50) + Python cap(20) double cap — Medium**

Per route: JS fetched top-50 elements, Python kept only 20. Feature pages with many UI elements (toolbars, sidebars, modals) lost buttons in positions 21–50. The button selector CSS also excluded `input[type=submit]`.

**Fix:** JS slice raised to 100 for buttons/testids, 60 for links. Python cap raised to 40 for buttons and testids, 30 for links. Added `input[type='button'], input[type='submit']` to the button CSS selector.

---

**5. Discovered routes included query strings and fragments — Medium**

`/page?tab=settings` and `/page#section` passed the `l.startswith("/")` filter. These became duplicate crawl targets, wasting slots.

**Fix:** `l.split("?")[0].split("#")[0]` strips query strings and fragments before adding to the route set.

---

**6. `all_routes` non-deterministic — Medium**

`list(set(discovered_routes) | set(seed_routes))` has random iteration order in CPython (despite the insertion-order guarantee for `dict`, plain `set` has no order). The `routes` field in `DomSnapshot` varied between runs, making log comparison and test assertions unreliable.

**Fix:** `sorted(set(...) | set(...))`.

---

**7. `allowed_routes_override` context inconsistency — Medium**

When `start_route="/billing"` was set, `seed_routes` still contained all diff-inferred routes (e.g., 6 routes). The crawl visited all of them, and the merged `real_buttons` in the LLM prompt contained buttons from `/pricing`, `/settings`, etc. — but `real_routes` showed only `["/", "/billing"]`. The LLM saw buttons it could not associate with any listed route.

**Fix:** When `allowed_routes_override` is set, `seed_routes` is filtered to only include routes in the override set before passing to `crawl_dom_data`. This constrains the crawl to `"/"` (guaranteed) + the override route, keeping buttons and routes consistent in the prompt.

---

**8. Per-route timing missing (roadmap spec)**

The roadmap explicitly required timing logs. `elapsed_ms` is now captured from `time.monotonic()` and logged in all three branches (success, auth wall, exception).