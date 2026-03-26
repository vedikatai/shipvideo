# Agent Browser Command Mapping to Current Failures

This document maps available `agent-browser` commands to current pipeline failure modes in `shipvideo-engine`, and identifies custom logic that can be reduced or removed by using built-in commands directly.

## Current Failure Modes Observed

- `no_match` / `unrecoverable` ref selection for dynamic or conditional UI
- late failure at `assert_terminal` (`terminal_not_reached`) after many unvalidated clicks
- many click outcomes marked `unvalidated` (weak early correctness signal)
- flaky state transitions due to fixed waits
- weak debugging artifacts when a run fails (hard to root-cause)
- custom UI diff/narration heuristics that duplicate built-in diff capabilities

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
| `screenshot [path]` / `pdf <path>` | evidence capture | artifacts | already used; can standardize labels |
| `snapshot` | ref discovery and state capture | target grounding | already used; should tune options |
| `eval <js>` | last-resort diagnostics only | runtime introspection | useful but should be minimized |
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
| `diff snapshot` | click-effect verification | weak custom UI diff heuristics | built-in semantic diff, less custom parsing |
| `diff screenshot` | visual regressions | screenshot-only ambiguity | objective pixel-level evidence |
| `trace start/stop` | full-run artifact | hard-to-reproduce failures | richer than current partial debug logs |
| `profiler start/stop` | perf-related timeouts | slow-step ambiguity | actionable perf evidence |
| `console` / `errors` | post-failure diagnostics | hidden frontend exceptions | immediate root cause visibility |
| `highlight` / `inspect` | interactive debugging | selector uncertainty | faster local diagnosis |

## Which Custom Logic Can Be Reduced

## Candidate Removals / Simplifications

- **Custom Mode B LLM fallback in `ref_selector.py`**
  - Replace first with semantic `find role/text/testid ... click` fallback.
  - Keep LLM as final fallback only if semantic commands fail.

- **Custom fixed wait strategy (`WAIT_AFTER_CLICK_MS`)**
  - Replace primary wait with `wait --load` and/or `wait --text` per step.

- **Custom terminal detection by snapshot name/text only**
  - Use `wait --text`, `get attr`, `is visible`, `get count`, and optionally `find testid` checks.

- **Custom UI diff extraction from snapshot names**
  - Prefer `diff snapshot` and optionally `diff screenshot` for stronger signal.

- **Sparse diagnostics on failures**
  - Add `console`, `errors`, `network requests`, and optional HAR on failure path.

## Keep (for now)

- contract extraction + preflight gating
- typed integrity errors / pipeline abort policy
- run metrics writing and observability

## Recommended Integration Plan

## Phase 1 (Immediate, lowest risk)

- In `run_ab_stepwise`, replace post-click fixed wait with:
  - `wait --load domcontentloaded`, then
  - optional `wait --text <validation>` when condition exists.
- On click no-match:
  - try `find role button click --name "<intent>"` and `find text "<intent>" click`.
  - only then use LLM fallback.
- On failure:
  - collect `console`, `errors`, and `network requests`.

## Phase 2 (Reliability + Debuggability)

- Replace custom snapshot diff summary with `diff snapshot` output.
- Start/stop `trace` around each run and persist trace path in run metrics.
- Add HAR capture for failure cases.

## Phase 3 (Refactor cleanup)

- Remove duplicated custom fallback branches that semantic `find` covers.
- Reduce custom terminal matcher complexity by moving to command-level checks.

## Practical Mapping to Current Files

- `app/execution/step_runner.py`
  - add `find ...` fallback branch before LLM fallback
  - switch waits to `wait --load/--text`
  - add diagnostics (`console/errors/network`)
  - adopt `diff snapshot` for post-click state-change evidence

- `app/browser/agent_browser_cli.py`
  - add thin wrappers for:
    - `find role/text/testid`
    - `wait --text/--url/--load/--fn`
    - `console`, `errors`
    - `network requests`, `network har start/stop`
    - `diff snapshot`

- `app/steps/metrics.py`
  - include command-level debug artifact paths/ids:
    - trace path, HAR path, console/error counts, network error counts

## Bottom Line

A large portion of current custom reliability logic can be replaced or simplified by native `agent-browser` commands, especially:

- semantic target recovery (`find ...`)
- robust waits (`wait --*`)
- built-in diff and diagnostics (`diff`, `console`, `errors`, `network`, `trace`)

This should improve accuracy and reduce maintenance burden at the same time.
