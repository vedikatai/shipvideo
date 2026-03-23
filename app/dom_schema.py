"""
Canonical DOM element schema shared by all pipeline stages.

Every module that reads or writes DOM data (dom_crawler, dom_extractor,
script_generator, step_generator, selector_validator, step_normalizer)
must use these TypedDicts instead of ad-hoc dicts. This eliminates the
label/aria/testid field-name drift that previously caused _build_action_menu()
to silently fall through to brittle raw CSS selectors.

Rules:
  - `aria`    holds the aria-label attribute value. Use for [aria-label='x'] selectors.
  - `title`   holds the title attribute value. Display-only; never use as a selector.
  - `selector` holds a precomputed CSS selector (testid > aria > id > tag).
               Empty string ("") when no derivation is applicable (e.g. runtime extractor).

Agent Browser experiment (Phase 1):
  - AgentBrowserElement    — one normalized interactive element from an
                             accessibility snapshot.
  - AgentBrowserSnapshot   — normalized output of one agent-browser snapshot
                             invocation; consumed by the decision layer (Phase 2+)
                             and by experiment instrumentation.
  These two types are defined here (not in app.browser) so that all pipeline
  modules can import a stable contract without taking a dependency on the
  browser sub-package.
"""
from __future__ import annotations

from typing import Dict, List, Literal, TypedDict


class ButtonCandidate(TypedDict):
    text: str      # visible innerText, stripped, max 100 chars
    testid: str    # data-testid attribute value or ""
    aria: str      # aria-label attribute value or ""
    title: str     # title attribute value or "" — display-only, not for selectors
    id: str        # element id or ""
    role: str      # element role (button, submit, etc.)
    selector: str  # derived CSS selector; "" when not applicable


class LinkCandidate(TypedDict):
    text: str    # visible innerText, stripped, max 100 chars
    href: str    # raw href attribute or ""
    testid: str  # data-testid attribute value or ""
    aria: str    # aria-label attribute value or ""
    id: str      # element id or ""


class InputCandidate(TypedDict):
    placeholder: str  # placeholder attribute or ""
    name: str         # name attribute or ""
    input_type: str   # type attribute (text, email, password, etc.) or ""
    testid: str       # data-testid attribute value or ""
    aria: str         # aria-label attribute value or ""
    id: str           # element id or ""


class TestIdCandidate(TypedDict):
    testid: str  # data-testid attribute value
    tag: str     # lowercase HTML tag name
    text: str    # visible innerText, stripped, max 80 chars


class DomSnapshot(TypedDict):
    current_path: str               # window.location.pathname
    routes: List[str]               # all known internal routes
    buttons: List[ButtonCandidate]
    links: List[LinkCandidate]
    inputs: List[InputCandidate]
    data_testids: List[TestIdCandidate]


# ---------------------------------------------------------------------------
# Agent Browser experiment types — Phase 1 / Phase 2
# ---------------------------------------------------------------------------

#: Which experiment execution mode is active for a given run.
#:
#:  "deterministic"          — Mode A: ref selection uses only the deterministic
#:                             waterfall (exact → case-insensitive → partial).
#:                             Required for all baseline comparison runs.
#:
#:  "deterministic_plus_llm" — Mode B: deterministic waterfall first; LLM
#:                             fallback when no deterministic match is found.
#:                             Must be logged explicitly; never mixed with Mode A
#:                             baseline results.
#:
#: Defined here (not in app.browser) so that dom_extractor, step_execution,
#: and ref_selector can all import from a single stable location without
#: taking a dependency on the browser sub-package.
ExperimentMode = Literal["deterministic", "deterministic_plus_llm"]

#: Explicit post-click validation condition used by Priority 1 structured
#: success validation in the Agent Browser runner.
SuccessConditionType = Literal["url_match", "text_present", "element_present"]


class SuccessCondition(TypedDict):
    """
    Explicit post-click success condition for one step.

    Fields:
        type  — one of:
                  "url_match"       → post-click URL must contain value
                  "text_present"    → post-click snapshot_text must contain value
                  "element_present" → post-click snapshot must contain an element
                                       whose accessible name matches value
        value — string to validate against the post-click page state.
    """

    type: SuccessConditionType
    value: str

class AgentBrowserElement(TypedDict):
    """
    One normalized interactive element from an agent-browser accessibility
    snapshot.

    Fields:
        ref     — agent-browser ref string, e.g. "@e1". Stable within one
                  snapshot session; must be re-queried after any navigation.
        role    — ARIA role in lowercase, e.g. "button", "link", "textbox".
        name    — accessible name: button label, link text, or input label.
        url     — page URL at the time the snapshot was taken.
        visible — always True; agent-browser only surfaces visible elements
                  in interactive (-i) snapshot mode.
    """

    ref: str
    role: str
    name: str
    url: str
    visible: bool


class AgentBrowserSnapshot(TypedDict):
    """
    Normalized output of one agent-browser snapshot invocation.

    This is the stable contract consumed by the experiment decision layer
    (Phase 2) and by instrumentation (Phase 4). All fields must remain
    stable across CLI output shape changes — normalization in
    AgentBrowserCLI._normalize_snapshot() is the adapter responsibility.

    Fields:
        current_url           — URL of the page at snapshot time.
        snapshot_text         — raw accessibility tree text from the CLI.
        interactive_elements  — normalized list of truly interactive elements
                                used for ref selection.
        context_elements      — normalized list of non-interactive snapshot
                                elements kept only for debugging / validation.
        raw_snapshot_path     — filesystem path to the saved raw JSON payload
                                for debugging; empty string if save was skipped
                                or failed.
    """

    current_url: str
    snapshot_text: str
    interactive_elements: List[AgentBrowserElement]
    context_elements: List[AgentBrowserElement]
    raw_snapshot_path: str
