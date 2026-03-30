# Agent Browser Command Mapping to Current Failures

This document maps the `agent-browser` commands we can actually use in `shipvideo-engine` today, and highlights where they can replace custom logic in the MVP pipeline. The main goal stays video accuracy, so the priority is command-level validation and deterministic recovery before any heuristic fallback.

## Current Failure Modes Observed

- `no_match` / `unrecoverable` ref selection for dynamic or conditional UI
- late failure at `assert_terminal` (`terminal_not_reached`) after many unvalidated clicks
- many click outcomes marked `unvalidated` (weak early correctness signal)
- flaky state transitions due to fixed waits
- weak debugging artifacts when a run fails (hard to root-cause)
- custom UI diff/narration heuristics that duplicate built-in command output

## Current State

These are already wired in the codebase:

- `open`, `set viewport`, `snapshot`, `click`, `wait`, `scrollintoview`, `scroll`
- `wait --load`, `wait --text`, `wait --url`
- `is visible`, `is enabled`
- `get url`, `get text`, `get count`, `get attr`
- `find testid`, `find role`, `find label`, `find text`
- `console`, `errors`, `network requests`
- `screenshot`, `close`

These are still mostly custom and should be reduced only if the command gives a clear accuracy win:

- ref waterfall selection in `ref_selector.py`
- terminal fallback matching by snapshot text
- state-change heuristics from snapshot diffing
- recovery regeneration in `llm/retry_engine.py`

## Command-to-Problem Mapping

## Core Commands

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `open <url>` | `run_ab_stepwise` start, route transitions | deterministic navigation | already used correctly |
| `click <sel>` | step execution | performs interaction | already used correctly |
| `dblclick <sel>` | fallback when single click no-op | elements requiring double-click | avoids custom repeated click heuristics |
| `focus <sel>` | before keyboard input flows | focus-dependent UIs | less brittle than ad-hoc focus assumptions |
| `type <sel> <text>` | form flows | text input | avoids converting text flows into click-only plans |
| `fill <sel> <text>` | form flows | deterministic field set | avoids custom input simulation |
| `press <key>` / `keydown` / `keyup` / `keyboard type` / `keyboard inserttext` | modal confirm, Enter-to-submit, shortcuts | keyboard-driven transitions | replaces custom JS/eval-based key hacks |
| `hover <sel>` | hover menus/tooltips | hidden-menu exposure | reduces custom "try click then retry" loops |
| `select <sel> <val>` | dropdown state setting | deterministic option selection | avoids fragile click chains on dropdowns |
| `check` / `uncheck` | checkbox/radio state | explicit boolean state set | less error-prone than click toggles |
| `scroll` / `scrollintoview` | offscreen targets | visibility issues | reduces false `no_match` due to viewport |
| `drag <src> <tgt>` | drag workflows | unsupported action types | removes need for custom action extensions |
| `upload <sel> <files>` | file upload flows | upload interaction | avoids custom filesystem + JS injection |
| `screenshot [path]` | evidence capture | artifacts | already used; keep as primary visual proof |
| `snapshot` | ref discovery and state capture | target grounding | already used; keep as primary grounding source |
| `eval <js>` | last-resort diagnostics only | runtime introspection | not currently wrapped; avoid unless no native command exists |
| `connect <port>` | debug against existing browser | repro in local browser state | avoids reproducing via daemon-only state |
| `stream enable/status/disable` | live troubleshooting | real-time operator visibility | faster diagnosis than log-only runs |
| `close` | teardown | cleanup | already used |

## Get Info

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `get text <sel>` | terminal checks, post-click validation | explicit text assertion | stronger than snapshot substring only |
| `get html <sel>` | diagnostics | DOM-level verification | better postmortem detail |
| `get value <sel>` | form verification | correct input state | avoids guessing from screenshot |
| `get attr <sel> <attr>` | terminal id/testid checks | attribute-level assertions | better than name-only match |
| `get title` / `get url` | navigation validation | route/title confirmation | already partly used |
| `get cdp-url` | deep debugging | connect DevTools tools | easier profiling/debug attachment |
| `get count <sel>` | ambiguity checks | multiple candidates detection | replace custom ambiguous heuristics |
| `get box` / `get styles` | visual state diagnostics | layout/visibility bugs | richer debug context |

## Check State

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `is visible <sel>` | pre-click gating | hidden/covered element clicks | prevents false click success |
| `is enabled <sel>` | pre-click gating | disabled CTA clicks | avoids no-op clicks |
| `is checked <sel>` | post-action validation | toggle flows | explicit correctness signal |

## Semantic Find Commands

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `find role <role> <action> --name ...` | Mode B fallback path | `no_match` for dynamic refs | likely replaces custom LLM fallback in many cases |
| `find text <text> <action>` | text-driven dynamic UI | label variations | avoids ref staleness |
| `find label <label> <action>` | form inputs | robust field targeting | better than inferred selectors |
| `find placeholder`, `find alt`, `find title` | secondary targeting | accessibility/text variations | reduces custom fuzzy logic |
| `find testid <id> <action>` | stable QA ids | deterministic interaction | better than free-form label match |
| `find first/last/nth` | known repeated elements | list/grid interactions | avoids custom candidate tie-breakers |

## Wait Commands

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `wait <ms>` | minimal fallback | fixed delay | already used but should be minimized |
| `wait <selector>` | pre-click and post-click stabilization | race conditions | better than blind sleep |
| `wait --text "<...>"` | click validation and terminal assertion | early semantic validation | better than late terminal-only failures |
| `wait --url "<pattern>"` | navigation boundaries | wrong-route clicks | stronger than snapshot diff only |
| `wait --load domcontentloaded/networkidle` | after click/goto | async page settling | reduces flakiness |
| `wait --fn "<js>"` | specialized conditions | app-specific readiness | cleaner than custom Python polling |

## Batch Execution

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `batch --json` | micro-sequences (`snapshot -> click -> wait -> snapshot`) | command overhead, timing drift | simplifies orchestration loops and reduces process chatter |
| `batch --bail` | strict stop-on-failure flows | cascading bad actions | cleaner fail-fast behavior |

## Browser Settings / Session State

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `set viewport` / `set device` | run init | cross-run layout variance | improves reproducibility |
| `set headers` / `set credentials` | auth-gated paths | login friction | avoids custom auth pre-steps |
| `set media`, `set geo`, `set offline` | scenario testing | env-dependent UI | cleaner than manual mocks |
| `cookies` + `storage` commands | session prep and diagnostics | auth/session drift | better than opaque failures |
| `state save/load/list/...` | persistent login/test state | repeated login and inconsistency | replace custom session bootstrap logic |

## Network Commands

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `network requests` + filters | failure diagnostics | unknown backend failures | richer than current debug_preview |
| `network request <id>` | post-failure triage | identify exact API error | precise root cause |
| `network route/unroute` | deterministic mocks | flaky dependencies | less custom mock plumbing |
| `network har start/stop` | run artifact capture | replayable network context | much better postmortem than logs alone |

## Tabs/Frames/Dialogs/Navigation

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `tab`, `tab new`, `tab close`, `window new` | OAuth/popups | lost-control after new tab | avoids custom "same page only" assumptions |
| `frame <sel>`, `frame main` | iframe apps | target not found in main doc | cleaner than selector hacks |
| `dialog status/accept/dismiss` | JS confirm/alert flows | blocked commands due to dialogs | avoids silent hangs |
| `back/forward/reload` | retry and recovery strategies | stuck state recovery | safer than rebuilding whole session |

## Diff / Debug Commands

| Command | Where to Use | Solves | Better Than Current |
|---|---|---|---|
| `console` / `errors` | post-failure diagnostics | hidden frontend exceptions | already used; should remain part of every failure path |
| `network requests` | post-failure diagnostics | backend/API failures | already used; good enough for MVP without HAR plumbing |

## Which Custom Logic Can Be Reduced

## Candidate Removals / Simplifications

- **Custom Mode B LLM fallback in `ref_selector.py`**
  - Replace first with semantic `find testid`, `find role`, `find label`, and `find text`.
  - Keep LLM as final fallback only if command-based recovery fails.

- **Custom fixed wait strategy (`WAIT_AFTER_CLICK_MS`)**
  - Replace primary wait with `wait --load` and `wait --text` per step.
  - Keep a short fixed wait only as a last fallback for animated pages.

- **Custom terminal detection by snapshot name/text only**
  - Use `wait --text`, `wait --url`, `get attr`, `is visible`, `get count`, and `find testid`.

- **Custom UI diff extraction from snapshot names**
  - Prefer snapshot-based diffs only if they are used to explain video accuracy.
  - Keep the current `compare_snapshots` helper until a real native diff command is wrapped.

- **Sparse diagnostics on failures**
  - Keep `console`, `errors`, and `network requests` on every failure path.
  - Add more only if the command exists in the shipped agent-browser binary and is stable in CI.

## Keep (for now)

- contract extraction + preflight gating
- typed integrity errors / pipeline abort policy
- run metrics writing and observability

## Recommended Integration Plan

## Phase 1

- Keep the current command-first path in `step_runner.py`.
- Make sure every click step prefers `find testid`, `find role`, `find label`, and `find text` before any LLM fallback.
- Keep post-click validation on `wait --text` and `wait --url` when the step provides a real success condition.
- Keep `console`, `errors`, and `network requests` attached to failure results.

## Phase 2

- Remove any remaining fixed-wait dependence unless animation makes a short fallback necessary.
- Reduce custom terminal text matching only where `wait --text`, `wait --url`, or `get count` can prove the same condition.
- Keep snapshot diffs in code until the agent-browser binary exposes a stable native diff command we can rely on in CI.

## Phase 3

- Remove duplicated selector heuristics only after we confirm the native `find` commands cover the same accuracy cases.
- Keep the LLM fallback as a safety net, not the primary selector strategy.

## Practical Mapping to Current Files

- `app/execution/step_runner.py`
  - already uses `find testid/role/label/text`, `wait --load/--text/--url`, `is visible`, `is enabled`, `get count`, `get text`, `console`, `errors`, and `network requests`
  - keep pruning the last custom branches around terminal fallback and recovery

- `app/browser/agent_browser_cli.py`
  - add thin wrappers for:
    - `find role/text/testid`
    - `wait --text/--url/--load/--fn`
    - `console`, `errors`
    - `network requests`, `network har start/stop`
    - `diff snapshot` only if the binary actually supports it

- `app/steps/metrics.py`
  - include command-level debug artifact paths/ids:
    - trace path, HAR path, console/error counts, network error counts

## Bottom Line

The biggest safe wins for MVP accuracy are semantic target recovery, command-level validation, and failure diagnostics. We should keep the custom logic only where the agent-browser command set does not yet cover the same reliability signal.
