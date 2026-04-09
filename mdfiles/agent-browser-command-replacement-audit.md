# Agent Browser Command Replacement Audit

## Goal

This document maps custom browser logic in our codebase to deterministic `agent-browser` commands we can use instead. The focus is MVP video accuracy: fewer custom heuristics, fewer mismatches between planning and execution, and more behavior grounded in the browser runtime itself.

## Recommendation Summary

The highest-value direction is to standardize on Agent Browser semantics for:

1. Click target resolution
2. Waiting and validation
3. Terminal assertions
4. Route and DOM grounding for replanning
5. Change detection

This will reduce the amount of custom selection, fuzzy matching, DOM scraping, and fallback logic we currently maintain.

## Replacement Matrix

| Area | Current custom logic | Recommended Agent Browser command(s) | Files / code to replace | Expected effect | Accuracy impact |
|---|---|---|---|---|---|
| Click target resolution | Multi-step custom lookup across testid, role, label, snapshot ref matching, and fallback fuzzy matching | `find role <role> click --name "<name>"`, `find label "<label>" click`, `find testid "<id>" click`, `click "<selector>"` only for explicit stable selectors | `app/execution/step_runner.py:197-339`, `app/browser/ref_selector.py:86-209`, `app/browser/agent_browser_cli.py:349-413` | Simpler click pipeline with fewer branches and fewer stale ref cases | High positive impact. Removes fuzzy custom matching and shifts targeting to runtime semantic locators |
| Scroll recovery for hidden targets | Manual loop: resolve, scroll, wait, retry | Prefer `find ... click`; fallback to `scrollintoview <sel>` then click; only use `scroll down <px>` in known infinite-scroll flows | `app/execution/step_runner.py:225-260`, `app/execution/step_runner.py:1491-1508` | Less bespoke retry behavior and fewer false recoveries | Medium to high positive impact. Better for deterministic visible-target interaction |
| Page settle after actions | Custom settle sequence with `networkidle`, `domcontentloaded`, then optional text/url waits | `wait --load networkidle`, `wait --load domcontentloaded`, `wait --text`, `wait --url`, `wait --fn` | `app/execution/step_runner.py:150-194` | Cleaner action-specific waits instead of one generic settle function | High positive impact. Better alignment with real post-action success signals |
| Terminal validation | Mixed checks: wait, count selectors, semantic lookup, then snapshot text fallback | `wait --text`, `wait --url`, `wait <selector>`, `find testid <id> text`, `get text <sel>` | `app/execution/step_runner.py:969-1045` | Stronger terminal checks with less custom fallback logic | High positive impact. Terminal success should become more deterministic and explainable |
| Runtime click validation | Compare before/after snapshot names and custom validation condition matching | `wait --text`, `wait --url`, `wait --fn`, optionally `get url`, `get text`, `is visible` | `app/execution/step_runner.py:847-883`, `app/execution/step_runner.py:1212-1217` | Validation becomes tied to explicit conditions instead of indirect snapshot diffing | High positive impact. Less risk of counting unrelated UI changes as success |
| UI change detection | Custom snapshot name-set diff | `diff snapshot`, `diff screenshot` | `app/browser/agent_browser_cli.py:40-61`, `app/browser/agent_browser_cli.py:521-526`, `app/execution/step_runner.py:543` | Better built-in diffing, especially for visual and scoped changes | Medium positive impact. Useful for detecting whether the intended page state really changed |
| DOM context extraction for replanning | Playwright scrapes buttons, links, testids, routes into a custom schema | `snapshot`, `get url`, `find ...`, `get count`, optional `diff snapshot` between steps | `app/context/dom_extractor.py:16-81`, `app/execution/step_runner.py:1103`, `app/execution/step_runner.py:1175` | Planning and execution use the same browser truth source | High positive impact. Avoids planner/executor disagreement |
| Route crawling for step generation | Playwright route discovery and per-page UI extraction | `open <url>`, `snapshot`, `get url`, `tab`, optional `batch --json` for route crawling | `app/steps/dom_crawler.py:34-60`, `app/steps/dom_crawler.py:129-219`, `app/steps/dom_crawler.py:293-397` | Replaces a separate crawler path with the same runtime used in execution | High positive impact if we commit fully. Improves consistency across pipeline stages |
| Selector validation policy | Custom validator for raw CSS, Playwright engines, DOM presence, and label heuristics | Validate that a step is representable as one of: `find role`, `find label`, `find testid`, `wait`, `get`, `click` | `app/policy/selector_validator.py:68-138`, `app/llm/retry_engine.py:1-46` | Much smaller validator surface and fewer unsupported selector shapes | Medium to high positive impact. Reduces invalid plans generated around unsupported selector styles |

## Detailed Replacements

### 1. Replace custom click target resolution

### Current custom code

- `app/execution/step_runner.py:197-222`
- `app/execution/step_runner.py:263-339`
- `app/browser/ref_selector.py:86-209`
- `app/browser/agent_browser_cli.py:349-413`

### Current behavior

We currently:

- parse selectors for `data-testid`
- try `find_role_ref("button", intent)`
- try `find_role_ref("link", intent)`
- try snapshot-based exact, case-insensitive, and partial name matching
- try label lookup
- try generic text lookup
- sometimes scroll and retry

### Better Agent Browser replacement

Use deterministic semantic commands directly:

```bash
agent-browser find role button click --name "Create"
agent-browser find role link click --name "Settings"
agent-browser find label "Email" fill "user@test.com"
agent-browser find testid "save-button" click
```

### What custom code this replaces

- Most of `select_ref`
- Most of `_resolve_ab_ref_with_commands`
- Most of `_resolve_ab_click_target`
- Most of the need for `find_*_ref` wrappers to return refs first and click later

### Effect

- Fewer stale refs
- Fewer fuzzy matches
- Fewer clicks on the wrong similarly named element
- Easier logs because command intent maps directly to action

### Accuracy impact

Very positive. Semantic runtime targeting is more reliable than snapshot-only fuzzy matching, especially when there are repeated labels or changing refs.

## 2. Replace manual scroll retry loops

### Current custom code

- `app/execution/step_runner.py:225-260`
- `app/execution/step_runner.py:1491-1508`

### Better Agent Browser replacement

Primary path:

```bash
agent-browser find role button click --name "Load more"
```

Secondary path for known off-screen elements:

```bash
agent-browser scrollintoview "[data-testid='load-more']"
agent-browser click "[data-testid='load-more']"
```

### What custom code this replaces

- `_scroll_to_find`
- Part of the retry path inside `run_ab_stepwise`

### Effect

We stop treating scrolling as a generic recovery mechanism and instead use it only when the target is known but off-screen.

### Accuracy impact

Positive. Generic scroll-and-guess loops can create accidental state changes and noisy captures.

## 3. Replace generic settle heuristics with explicit waits

### Current custom code

- `app/execution/step_runner.py:150-194`

### Better Agent Browser replacement

```bash
agent-browser wait --load networkidle
agent-browser wait --text "Settings saved"
agent-browser wait --url "**/dashboard"
agent-browser wait --fn "window.appReady === true"
```

### What custom code this replaces

- Most of `_settle_ab_page`

### Effect

Each step waits for the condition that actually means success for that step.

### Accuracy impact

Very positive. This is one of the cleanest ways to improve video accuracy because screenshots happen after the meaningful UI state arrives.

## 4. Replace terminal checks with native waits and semantic checks

### Current custom code

- `app/execution/step_runner.py:969-1045`

### Better Agent Browser replacement

For text terminal:

```bash
agent-browser wait --text "Invite sent"
```

For URL terminal:

```bash
agent-browser wait --url "**/billing"
```

For element terminal:

```bash
agent-browser wait "[data-testid='success-banner']"
agent-browser find testid "success-banner" text
```

### What custom code this replaces

- `find_testid_ref` fallback in terminal checks
- `get_count` selector probing
- generic `find_ref` for terminal proof
- some snapshot text fallback usage

### Effect

Terminal assertions become much more binary and less interpretation-heavy.

### Accuracy impact

Very positive. This reduces false positives where snapshot text happened to contain a similar word but the intended state was not reached.

## 5. Replace custom diffing with Agent Browser diff commands

### Current custom code

- `app/browser/agent_browser_cli.py:40-61`
- `app/browser/agent_browser_cli.py:521-526`
- `app/execution/step_runner.py:543`

### Better Agent Browser replacement

```bash
agent-browser diff snapshot
agent-browser diff screenshot --baseline before.png -o diff.png
```

### What custom code this replaces

- Name-set based snapshot comparison

### Effect

Built-in diffing should give a better signal about whether the UI actually changed meaningfully.

### Accuracy impact

Moderately positive. Useful for validation and diagnostics, but less important than semantic click and wait replacement.

## 6. Replace Playwright DOM extraction for Agent Browser runs

### Current custom code

- `app/context/dom_extractor.py:16-81`
- `app/execution/step_runner.py:1103-1119`
- `app/execution/step_runner.py:1171-1189`

### Better Agent Browser replacement

Use:

- `snapshot`
- `get url`
- `find role`
- `find label`
- `find testid`
- `get count`

instead of building a separate page-derived DOM context when the active backend is already Agent Browser.

### What custom code this replaces

- `extract_dom_context` for Agent Browser-oriented flows

### Effect

The planner, validator, and executor all operate on the same browser representation.

### Accuracy impact

Very positive. This removes one of the biggest sources of mismatch between what the planner believes exists and what the execution engine actually interacts with.

## 7. Replace Playwright route crawler with Agent Browser route snapshots

### Current custom code

- `app/steps/dom_crawler.py:34-60`
- `app/steps/dom_crawler.py:129-219`
- `app/steps/dom_crawler.py:293-397`

### Existing code already closer to the target

- `app/steps/dom_crawler.py:404-446`

### Better Agent Browser replacement

```bash
agent-browser open <url>
agent-browser snapshot
agent-browser get url
agent-browser batch --json
```

### What custom code this replaces

- Most of the async Playwright crawler used for route discovery and element extraction

### Effect

The route catalog becomes based on the same runtime we use for actual click execution.

### Accuracy impact

High positive impact, especially for SPAs and dynamic content where static DOM scraping can disagree with accessibility/runtime state.

## Priority Order

1. Click resolution and execution
2. Wait and terminal validation
3. DOM context / replanning grounding
4. Route crawling
5. Diffing and diagnostics

## Final Recommendation

For MVP accuracy, we should treat Agent Browser as the source of truth, not as a thin transport layer under our own browser heuristics. The custom code that is most worth replacing first is the click-resolution and wait-validation stack in `app/execution/step_runner.py`, because that code directly determines whether we click the right thing at the right time and capture the correct video state.
