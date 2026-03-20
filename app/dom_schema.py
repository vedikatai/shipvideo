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
"""
from __future__ import annotations

from typing import Dict, List, TypedDict


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
