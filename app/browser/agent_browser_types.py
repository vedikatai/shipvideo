"""
Lightweight internal types for the Agent Browser CLI wrapper (Phase 1 + 2).

These types define the stable internal contract between the CLI subprocess
wrapper (agent_browser_cli.py), the ref-selection policy (ref_selector.py),
and their callers within the browser module.

Design boundaries:
  Phase 1:
    - CommandResult  — raw output from one agent-browser CLI invocation.
    - SnapshotPayload — intermediate extraction of raw snapshot fields before
                        final normalization into AgentBrowserSnapshot.

  Phase 2:
    - SelectionReason — why a specific ref was (or was not) chosen.
    - RefCandidate    — one element considered during ref selection.
    - SelectionResult — structured output of one select_ref() call; consumed
                        by the experiment runner and instrumentation.

  ExperimentMode is defined in app.dom_schema (shared stable contract across
  modules) and imported here for use in SelectionResult.

  The stable downstream contract types used by pipeline and experiment code
  (AgentBrowserElement, AgentBrowserSnapshot, ExperimentMode) live in
  app.dom_schema to keep them accessible without importing from the browser
  sub-package.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

from app.dom_schema import ExperimentMode


class CommandResult(TypedDict):
    """
    Raw result from a single agent-browser CLI subprocess invocation.

    Fields:
        success    — True when exit_code == 0 and JSON reported success=True.
        stdout     — captured standard output (text mode).
        stderr     — captured standard error (text mode).
        exit_code  — process exit code; 0 on success.
        data       — parsed JSON data dict when --json was used, else {}.
                     Matches the "data" key from agent-browser JSON envelope:
                     {"success": true, "data": {...}}.
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    data: Dict[str, Any]


class SnapshotPayload(TypedDict):
    """
    Intermediate extraction of raw agent-browser snapshot fields.

    Produced immediately after parsing the JSON envelope from a snapshot
    invocation and before normalization into AgentBrowserSnapshot.

    Fields:
        snapshot_text — raw accessibility tree text returned by the CLI.
        refs          — dict mapping ref-id (e.g. "e1") to element metadata
                        {"role": "button", "name": "Submit"} as returned by
                        agent-browser when --json is used.
    """

    snapshot_text: str
    refs: Dict[str, Any]


# ---------------------------------------------------------------------------
# Phase 2 — Deterministic ref-selection types
# ---------------------------------------------------------------------------

#: Why select_ref() succeeded or failed on a given intent.
#:
#:  "exact_match"            — element name == intent (case-sensitive).
#:  "case_insensitive_match" — element name matches intent ignoring case
#:                             (and is not an exact case-sensitive match).
#:  "partial_match"          — intent is a substring of element name, or
#:                             element name is a substring of intent
#:                             (case-insensitive; not a full exact match).
#:  "ambiguous"              — multiple elements matched at the same waterfall
#:                             level; chosen_ref is "" in this case.
#:  "no_match"               — no element matched at any level;
#:                             chosen_ref is "" in this case.
SelectionReason = Literal[
    "exact_match",
    "case_insensitive_match",
    "partial_match",
    "ambiguous",
    "no_match",
]


class RefCandidate(TypedDict):
    """
    One interactive element considered during a single ref-selection attempt.

    Included in SelectionResult.candidates to make the selection decision
    fully auditable — every candidate that contributed to the final reason
    is recorded here.

    Fields:
        ref        — agent-browser ref string, e.g. "@e1".
        role       — ARIA role in lowercase, e.g. "button", "link".
        name       — accessible name of the element.
        match_type — at which waterfall level this element matched:
                     "exact" | "case_insensitive" | "partial".
    """

    ref: str
    role: str
    name: str
    match_type: str


class SelectionResult(TypedDict):
    """
    Structured output of one select_ref() invocation.

    Consumed by the experiment runner (Phase 3) and instrumentation (Phase 4).
    Every field is populated regardless of outcome so downstream code can
    log, compare, and debug without conditional checks.

    Fields:
        chosen_ref        — winning ref string (e.g. "@e2") when selection
                            succeeded; empty string "" on ambiguous or no_match.
        selection_reason  — SelectionReason literal explaining the outcome.
        candidates        — list of RefCandidate entries at the winning
                            waterfall level (all ambiguous candidates, or the
                            single winner, or [] for no_match).
        intent            — original intent string passed to select_ref().
        mode              — ExperimentMode used for this selection call.
                            Always log this field; never mix "deterministic"
                            and "deterministic_plus_llm" results.
    """

    chosen_ref: str
    selection_reason: SelectionReason
    candidates: List[RefCandidate]
    intent: str
    mode: ExperimentMode


ValidationSource = Literal["step", "test_case", "legacy_state_change", ""]


class StepValidationResult(TypedDict):
    """
    Structured post-click validation result recorded by the AB runner.

    Fields:
        condition_type   — validation type used for the step:
                           "url_match" | "text_present" | "element_present" |
                           "state_changed" | "".
        condition_value  — expected value for structured validation, or "" for
                           legacy state-change fallback.
        source           — whether the validation came from the step payload,
                           fixed test-case metadata, or legacy fallback.
        passed           — True when the post-click page state satisfied the
                           validation rule.
        failure_reason   — reason string when validation failed, else "".
    """

    condition_type: str
    condition_value: str
    source: ValidationSource
    passed: bool
    failure_reason: str
