from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from app.dom_schema import ExperimentMode


class CommandResult(TypedDict):

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    data: Dict[str, Any]


class SnapshotPayload(TypedDict):

    snapshot_text: str
    refs: Dict[str, Any]


















SelectionReason = Literal[
    "testid_match",
    "aria_match",
    "id_match",
    "exact_match",
    "case_insensitive_match",
    "partial_match",
    "scored_match",
    "ambiguous",
    "no_match",
    "ab_find",
]


class RefCandidate(TypedDict):

    ref: str
    role: str
    name: str
    match_type: str


class SelectionResult(TypedDict):

    chosen_ref: str
    selection_reason: SelectionReason
    candidates: List[RefCandidate]
    intent: str
    mode: ExperimentMode


ValidationSource = Literal["step", "test_case", ""]


ValidationConditionType = Literal["url_match", "text_present", "element_present"]


class ValidationCondition(TypedDict):

    type: ValidationConditionType
    value: str


class StepValidationResult(TypedDict):

    passed: bool
    condition: Optional[ValidationCondition]
    actual: str
    source: ValidationSource
    failure_reason: str


class ABPageSettleResult(TypedDict):
    domcontentloaded: bool
    networkidle: bool
    validation_wait: str
    fallback_wait_used: bool


class ABTargetResolution(TypedDict):
    chosen_ref: str
    selection_reason: str
    selection_source: str
    scroll_retry_used: bool
    should_retry: bool


class ABActionabilityResult(TypedDict):
    target_visible: bool
    target_enabled: bool
