"""
Experiment logger — Phase 4 / Phase 5.

Central artifact writer for the Agent Browser accuracy experiment.

Responsibilities:
    1. Define the fixed test suite of 5 flow categories required by Phase 4.
    2. Record per-step and per-run experiment traces in a stable schema.
    3. Compute comparable metrics for both the Playwright and Agent Browser
       backends using the same calculation logic.
    4. Save run_trace.json and run_summary.json to disk after each run.
    5. Produce a side-by-side ComparisonReport when both backends have run
       the same test case.
    6. Produce a final experiment summary with explicit go / no-go thresholds
       for Mode A (deterministic) runs.

Logger schema (section 13 of the integration plan):
    {
      "run_id": "string",
      "backend": "playwright|agent_browser_cli",
      "mode": "deterministic|deterministic_plus_llm",
      "test_case_id": "string",
      "steps": [...],
      "final_outcome": "passed|ambiguous|regressed|inconclusive"
    }

Per-run artifacts saved to disk (app/data/experiment_runs/<run_id>/):
    run_trace.json   — full per-step trace
    run_summary.json — aggregated metrics for direct backend comparison

Test suite:
    FIXED_TEST_SUITE defines 5 placeholder test cases covering the required
    categories: semantic_button, navigation_link, custom_clickable,
    ambiguous_target, post_nav_reanchor.

    Override with real values for your preview URL by creating:
        app/data/test_suite.json   (JSON array of TestCase dicts)

Failure taxonomy (section 13):
    NO_MATCH      — no ref found at any waterfall level.
    AMBIGUOUS     — multiple refs matched; cannot safely select.
    WRONG_CLICK   — click succeeded technically; expected state not reached.
    CLICK_FAILED  — click command itself failed.
    TIMEOUT       — snapshot or page load timed out.
    STALE_REF     — ref valid in prior snapshot but invalid at action time.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

from app.dom_schema import SuccessCondition

# ---------------------------------------------------------------------------
# Artifact directories
# ---------------------------------------------------------------------------

#: Root for per-run experiment artifacts: app/data/experiment_runs/<run_id>/
_RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "experiment_runs"

#: Optional test-suite JSON override: app/data/test_suite.json
_TEST_SUITE_PATH = Path(__file__).resolve().parent.parent / "data" / "test_suite.json"

#: Phase 5 aggregate experiment summary written at the root runs dir.
_EXPERIMENT_SUMMARY_PATH = _RUNS_DIR / "experiment_summary.json"


# ---------------------------------------------------------------------------
# Phase 4 — Test suite types (Key Task 1)
# ---------------------------------------------------------------------------

class TestCase(TypedDict):
    """
    One fixed test case in the accuracy experiment suite.

    Fields:
        id                — unique identifier, e.g. "tc_01_semantic_button".
        category          — one of the 5 required flow categories.
        description       — human-readable description of the flow.
        intent            — selection intent for the primary click step.
        route             — relative path to navigate to, e.g. "/api-keys".
        steps             — list of standard step dicts (action/text/url/selector).
        success_condition — structured ground-truth validation rule.
    """

    id: str
    category: str
    description: str
    intent: str
    route: str
    steps: List[Dict[str, Any]]
    success_condition: SuccessCondition


# ---------------------------------------------------------------------------
# Fixed test suite — 5 required categories (Phase 4, Key Task 1)
#
# These are placeholder definitions using common UI patterns.
# Replace with real values for your preview URL by creating:
#     app/data/test_suite.json   (JSON array of TestCase dicts)
# ---------------------------------------------------------------------------

FIXED_TEST_SUITE: List[TestCase] = [
    {
        "id": "tc_01_semantic_button",
        "category": "semantic_button",
        "description": (
            "Click a clearly-labeled semantic button (e.g. 'Generate API Key') "
            "and verify the confirmation message appears in the post-action snapshot."
        ),
        "intent": "Generate API Key",
        "route": "/",
        "steps": [
            {"action": "goto", "url": "/"},
            {
                "action": "click",
                "text": "Generate API Key",
                "success_condition": {"type": "text_present", "value": "API Key created"},
            },
            {"action": "screenshot"},
        ],
        "success_condition": {"type": "text_present", "value": "API Key created"},
    },
    {
        "id": "tc_02_navigation_link",
        "category": "navigation_link",
        "description": (
            "Click a navigation link (e.g. 'Settings') and verify the URL "
            "changes to the expected route."
        ),
        "intent": "Settings",
        "route": "/",
        "steps": [
            {"action": "goto", "url": "/"},
            {
                "action": "click",
                "text": "Settings",
                "success_condition": {"type": "url_match", "value": "/settings"},
            },
            {"action": "screenshot"},
        ],
        "success_condition": {"type": "url_match", "value": "/settings"},
    },
    {
        "id": "tc_03_custom_clickable",
        "category": "custom_clickable",
        "description": (
            "Click a div/span-style clickable element that requires cursor-interactive "
            "mode (-C flag). Verifies Agent Browser exposes non-semantic elements "
            "that the Playwright extractor may miss."
        ),
        "intent": "Open menu",
        "route": "/",
        "steps": [
            {"action": "goto", "url": "/"},
            {
                "action": "click",
                "text": "Open menu",
                "success_condition": {"type": "element_present", "value": "Close menu"},
            },
            {"action": "screenshot"},
        ],
        "success_condition": {"type": "element_present", "value": "Close menu"},
    },
    {
        "id": "tc_04_ambiguous_target",
        "category": "ambiguous_target",
        "description": (
            "Attempt to click a label that appears multiple times (e.g. repeated "
            "'Edit' buttons). Expects an AMBIGUOUS outcome, verifying the runner "
            "does not guess silently."
        ),
        "intent": "Edit",
        "route": "/",
        "steps": [
            {"action": "goto", "url": "/"},
            {"action": "click", "text": "Edit"},
            {"action": "screenshot"},
        ],
        "success_condition": {"type": "text_present", "value": "Edit"},
    },
    {
        "id": "tc_05_post_nav_reanchor",
        "category": "post_nav_reanchor",
        "description": (
            "Navigate to a page then perform a second action requiring re-anchoring. "
            "Validates that the post-navigation snapshot provides a usable "
            "next-action surface."
        ),
        "intent": "Confirm",
        "route": "/",
        "steps": [
            {"action": "goto", "url": "/"},
            {
                "action": "click",
                "text": "Create new",
                "success_condition": {"type": "element_present", "value": "Confirm"},
            },
            {"action": "click", "text": "Confirm"},
            {"action": "screenshot"},
        ],
        "success_condition": {"type": "element_present", "value": "Confirm"},
    },
]


def load_test_suite(json_path: Optional[Path] = None) -> List[TestCase]:
    """
    Load test suite from JSON file if available; fall back to FIXED_TEST_SUITE.

    Checks app/data/test_suite.json (or the provided path). Returns
    FIXED_TEST_SUITE unchanged if the file is missing, empty, or invalid JSON.

    Args:
        json_path — explicit path; defaults to app/data/test_suite.json.

    Returns:
        List of TestCase dicts. Never raises.
    """
    path = json_path or _TEST_SUITE_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception as exc:
            print(
                f"[experiment_logger] WARNING: test_suite.json parse failed: {exc}",
                flush=True,
            )
    return FIXED_TEST_SUITE


# ---------------------------------------------------------------------------
# Phase 4 — Failure taxonomy
# ---------------------------------------------------------------------------

#: Canonical failure categories from section 13 of the integration plan.
FailureTaxonomy = Literal[
    "NO_MATCH", "AMBIGUOUS", "WRONG_CLICK", "CLICK_FAILED", "TIMEOUT", "STALE_REF"
]

#: Maps runner step outcome strings to the Phase 4 failure taxonomy.
#: Supports both exact matches and prefix-matched compound strings
#: (e.g. "goto_failed:/billing" → "CLICK_FAILED").
_OUTCOME_TO_TAXONOMY: Dict[str, str] = {
    "no_match":            "NO_MATCH",
    "no_intent":           "NO_MATCH",
    "ambiguous":           "AMBIGUOUS",
    "wrong_click":         "WRONG_CLICK",
    "repeated_action":     "WRONG_CLICK",
    "click_failed":        "CLICK_FAILED",
    "stale_ref":           "STALE_REF",
    "stale_ref_unrecovered": "STALE_REF",
    "goto_failed":         "CLICK_FAILED",
    "snapshot_failed":     "TIMEOUT",
    "agent_browser_error": "TIMEOUT",
}


def normalize_failure_taxonomy(outcome: str) -> str:
    """
    Map a step outcome string to the Phase 4 failure taxonomy.

    Returns the taxonomy string (e.g. "NO_MATCH") for failure outcomes,
    or "" for non-failure outcomes ("success", "pending", "ok", "").

    Args:
        outcome — raw step outcome string from the runner result.

    Returns:
        Taxonomy string or "" on success/non-failure.
    """
    if not outcome or outcome in ("success", "pending", "ok", "unvalidated"):
        return ""
    for key, taxonomy in _OUTCOME_TO_TAXONOMY.items():
        if outcome == key or outcome.startswith(f"{key}:"):
            return taxonomy
    if outcome.startswith("validation_failed"):
        return "CLICK_FAILED"
    return "CLICK_FAILED"  # catch-all for unrecognised failure strings


# ---------------------------------------------------------------------------
# Phase 4 — Run log schema (matches section 13 of the integration plan)
# ---------------------------------------------------------------------------

#: Final experiment run outcome (Phase 5 machine-readable categories).
FinalOutcome = Literal["passed", "ambiguous", "regressed", "inconclusive"]


class StepTrace(TypedDict):
    """
    Complete per-step experiment log for one executed action.

    Populated by ExperimentLogger.finish_from_runner_result() from the step
    results in the runner result dict. Fields unavailable for the Playwright
    backend (chosen_ref, selection_reason, etc.) default to empty strings so
    both backends produce structurally identical traces.
    """

    step_index: int
    backend: str
    mode: str
    intent: str
    raw_snapshot_path: str
    chosen_ref: str
    selection_reason: str
    candidate_count: int
    action: str
    result: str               # "success" | "failure" | "ambiguous"
    failure_reason: str       # "" on success
    failure_taxonomy: str     # FailureTaxonomy or "" on success
    url_before: str
    url_after: str
    state_changed: bool
    snapshot_diff_detected: bool
    validation_type: str
    validation_value: str
    validation_source: str
    validation_passed: bool
    validation_result: Dict[str, Any]
    stale_ref_count: int
    step_latency_ms: int


class RunTrace(TypedDict):
    """
    Full per-run experiment trace, saved to run_trace.json.

    Matches the logger schema from section 13 of the integration plan.
    """

    run_id: str
    backend: str
    mode: str
    test_case_id: str
    created_at: str           # ISO-8601 UTC timestamp
    steps: List[StepTrace]
    final_outcome: FinalOutcome
    metrics: Dict[str, Any]


class RunSummary(TypedDict):
    """
    Aggregated per-run metrics, saved to run_summary.json.

    Contains the fields required for direct backend comparison
    (Phase 4 Key Tasks 4 and 5).
    """

    run_id: str
    backend: str
    mode: str
    test_case_id: str
    final_outcome: FinalOutcome
    success_rate: float
    retries_per_run: float
    failure_type_counts: Dict[str, int]
    wrong_click_count: int
    avg_step_latency_ms: float


class ThresholdChecks(TypedDict):
    """Phase 5 go / no-go checks, evaluated on Mode A deterministic runs."""

    ab_at_least_as_good_on_core_paths: bool
    reduced_target_selection_failures: bool
    explainable_failures: bool


class DecisionSummary(TypedDict):
    """Top-level go / no-go decision emitted by summarize_experiment()."""

    outcome: FinalOutcome
    recommendation: str
    promotion_allowed: bool
    has_paired_baseline: bool
    paired_test_case_count: int
    rationale: List[str]


class TestCaseComparison(TypedDict):
    """One test-case comparison entry in the aggregate experiment summary."""

    test_case_id: str
    mode: str
    has_paired_baseline: bool
    playwright_outcome: FinalOutcome
    agent_browser_outcome: FinalOutcome
    decision_outcome: FinalOutcome


class ModeSummary(TypedDict):
    """Aggregate summary for one AB mode (deterministic or deterministic_plus_llm)."""

    mode: str
    paired_baseline_available: bool
    paired_test_case_count: int
    agent_only_test_case_count: int
    test_case_results: List[TestCaseComparison]
    baseline_metrics: Dict[str, Any]
    agent_browser_metrics: Dict[str, Any]
    paired_agent_browser_metrics: Dict[str, Any]
    top_failure_modes: Dict[str, int]
    ambiguous_cases: List[str]
    unexplained_failure_count: int


class ComparisonReport(TypedDict):
    """
    Side-by-side comparison of Playwright and Agent Browser run summaries.

    Produced by compare_runs() when both backends have completed the same
    test case. Saved to comparison_<test_case_id>.json by save_comparison().
    """

    playwright: RunSummary
    agent_browser_cli: RunSummary
    winner: str               # "playwright" | "agent_browser_cli" | "tie" | "inconclusive"
    decision_outcome: FinalOutcome
    threshold_checks: ThresholdChecks
    notes: List[str]


class ExperimentSummary(TypedDict):
    """Phase 5 final summary across all saved run summaries."""

    generated_at: str
    thresholds: ThresholdChecks
    decision: DecisionSummary
    mode_summaries: List[ModeSummary]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_metrics(
    steps: List[StepTrace],
    total_initial_steps: int,
    retries_per_run: float,
) -> Dict[str, Any]:
    """
    Compute Phase 4 comparison metrics from a list of StepTrace objects.

    Args:
        steps               — per-step traces built by ExperimentLogger.
        total_initial_steps — denominator for success_rate.
        retries_per_run     — total retry count from the runner result.
    """
    succeeded = sum(1 for s in steps if s["result"] == "success")
    wrong_click_count = sum(
        1 for s in steps if s["failure_taxonomy"] == "WRONG_CLICK"
    )

    failure_type_counts: Dict[str, int] = {}
    for s in steps:
        tax = s["failure_taxonomy"]
        if tax:
            failure_type_counts[tax] = failure_type_counts.get(tax, 0) + 1

    latencies = [s["step_latency_ms"] for s in steps if s["step_latency_ms"] > 0]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    return {
        "success_rate": succeeded / max(total_initial_steps, 1),
        "retries_per_run": retries_per_run,
        "failure_type_counts": failure_type_counts,
        "wrong_click_count": wrong_click_count,
        "avg_step_latency_ms": round(avg_latency, 1),
    }


_TARGET_SELECTION_FAILURES = frozenset({
    "NO_MATCH",
    "AMBIGUOUS",
    "WRONG_CLICK",
    "CLICK_FAILED",
    "STALE_REF",
})


def _coerce_final_outcome(runner_result: Dict[str, Any]) -> FinalOutcome:
    """Normalize runner final_outcome into the logger's Phase 5 categories."""
    value = str(runner_result.get("final_outcome") or "").strip().lower()
    if value in {"passed", "ambiguous", "regressed", "inconclusive"}:
        return value  # type: ignore[return-value]

    success = bool(runner_result.get("success"))
    failure_reason = str(runner_result.get("failure_reason") or "").lower()
    if success:
        return "passed"
    if "ambiguous" in failure_reason:
        return "ambiguous"
    if failure_reason:
        return "regressed"
    return "inconclusive"


def _aggregate_run_summaries(summaries: List[RunSummary]) -> Dict[str, Any]:
    """Average comparable metrics across a set of run summaries."""
    if not summaries:
        return {
            "run_count": 0,
            "success_rate": 0.0,
            "retries_per_run": 0.0,
            "failure_type_counts": {},
            "wrong_click_count": 0,
            "avg_step_latency_ms": 0.0,
        }

    failure_counts: Counter[str] = Counter()
    for summary in summaries:
        failure_counts.update(summary["failure_type_counts"])

    return {
        "run_count": len(summaries),
        "success_rate": round(
            sum(s["success_rate"] for s in summaries) / len(summaries), 4
        ),
        "retries_per_run": round(
            sum(s["retries_per_run"] for s in summaries) / len(summaries), 2
        ),
        "failure_type_counts": dict(failure_counts),
        "wrong_click_count": sum(s["wrong_click_count"] for s in summaries),
        "avg_step_latency_ms": round(
            sum(s["avg_step_latency_ms"] for s in summaries) / len(summaries), 1
        ),
    }


def _target_selection_failure_count(metrics: Dict[str, Any]) -> int:
    """Return selector / target-selection failure count from aggregated metrics."""
    counts = metrics.get("failure_type_counts") or {}
    return sum(int(counts.get(key, 0)) for key in _TARGET_SELECTION_FAILURES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ExperimentLogger:
    """
    Central artifact writer for the Agent Browser accuracy experiment.

    Converts a runner result dict (from run_stepwise or run_ab_stepwise)
    into a structured experiment trace and saves two JSON artifacts:
        run_trace.json   — full per-step trace
        run_summary.json — aggregated metrics for comparison

    Both Playwright and Agent Browser runs produce the same artifact schema.
    Fields absent from the Playwright step results (chosen_ref, selection_reason,
    etc.) default to empty strings so traces remain structurally comparable.

    Usage:
        logger = ExperimentLogger(
            backend="agent_browser_cli",
            mode="deterministic",
            test_case_id="tc_01_semantic_button",
        )
        runner_result = run_ab_stepwise(...)
        trace = logger.finish_from_runner_result(runner_result)
        # → artifacts saved to app/data/experiment_runs/<run_id>/

    The returned RunTrace can be passed to compare_runs() alongside a
    RunTrace from the Playwright backend for direct comparison.
    """

    def __init__(
        self,
        *,
        backend: str,
        mode: str,
        test_case_id: str = "",
        artifact_dir: Optional[Path] = None,
    ) -> None:
        self.run_id: str = uuid.uuid4().hex[:8]
        self.backend: str = backend
        self.mode: str = mode
        self.test_case_id: str = test_case_id
        self._artifact_dir: Path = artifact_dir or (_RUNS_DIR / self.run_id)

    def finish_from_runner_result(
        self,
        runner_result: Dict[str, Any],
    ) -> RunTrace:
        """
        Build and persist run artifacts from a raw runner result dict.

        Accepts the dict returned by run_stepwise() or run_ab_stepwise().
        Step fields absent from Playwright results (chosen_ref, intent, etc.)
        default to empty strings so both backends produce comparable traces.

        Args:
            runner_result — dict from run_stepwise or run_ab_stepwise.

        Returns:
            The completed RunTrace; also saved to disk as run_trace.json and
            run_summary.json in app/data/experiment_runs/<run_id>/.
        """
        raw_steps = runner_result.get("results") or []
        step_traces: List[StepTrace] = []

        for sr in raw_steps:
            step = sr.get("step") or {}
            outcome = (sr.get("outcome") or "").strip()
            status = (sr.get("status") or "failed").strip()
            validation_result = sr.get("validation_result") or {}
            is_failure = status == "failed" or outcome in (
                "wrong_click",
                "click_failed",
                "stale_ref",
                "stale_ref_unrecovered",
            )

            if status == "ok" and outcome == "success":
                step_result_label = "success"
            else:
                step_result_label = "failure" if is_failure else "success"

            taxonomy = normalize_failure_taxonomy(outcome) if is_failure else ""

            trace = StepTrace(
                step_index=int(sr.get("index", 0)),
                backend=self.backend,
                mode=self.mode,
                intent=(sr.get("intent") or step.get("label") or step.get("text") or "").strip(),
                raw_snapshot_path=str(sr.get("raw_snapshot_path") or "").strip(),
                chosen_ref=(sr.get("chosen_ref") or "").strip(),
                selection_reason=(sr.get("selection_reason") or "").strip(),
                candidate_count=0,
                action=(step.get("action") or "").strip(),
                result=step_result_label,
                failure_reason=(
                    (sr.get("validation_failure_reason") or sr.get("error") or outcome)
                    if is_failure
                    else ""
                ),
                failure_taxonomy=taxonomy,
                url_before=(sr.get("url_before") or "").strip(),
                url_after=(sr.get("url_after") or "").strip(),
                state_changed=bool(sr.get("state_changed", False)),
                snapshot_diff_detected=bool(sr.get("state_changed", False)),
                validation_type=str(
                    sr.get("validation_type")
                    or validation_result.get("condition", {}).get("type")
                    or ""
                ).strip(),
                validation_value=str(
                    sr.get("validation_value")
                    or validation_result.get("condition", {}).get("value")
                    or ""
                ).strip(),
                validation_source=str(sr.get("validation_source") or validation_result.get("source") or "").strip(),
                validation_passed=bool(sr.get("validation_passed", validation_result.get("passed", False))),
                validation_result=validation_result if isinstance(validation_result, dict) else {},
                stale_ref_count=int(sr.get("stale_ref_count") or 0),
                step_latency_ms=int(sr.get("step_latency_ms") or 0),
            )
            step_traces.append(trace)

        # Determine final outcome from runner result.
        final_outcome = _coerce_final_outcome(runner_result)

        # Retries are pre-computed by the runner and stored in metrics dict.
        retries = float(
            (runner_result.get("metrics") or {}).get("retries_per_run", 0)
        )
        metrics = _compute_metrics(step_traces, max(len(raw_steps), 1), retries)

        trace_obj = RunTrace(
            run_id=self.run_id,
            backend=self.backend,
            mode=self.mode,
            test_case_id=self.test_case_id,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            steps=step_traces,
            final_outcome=final_outcome,
            metrics=metrics,
        )

        self._save(trace_obj)
        return trace_obj

    def _save(self, trace: RunTrace) -> None:
        """Persist run_trace.json and run_summary.json to the artifact directory."""
        try:
            self._artifact_dir.mkdir(parents=True, exist_ok=True)

            trace_path = self._artifact_dir / "run_trace.json"
            trace_path.write_text(
                json.dumps(trace, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            summary = RunSummary(
                run_id=trace["run_id"],
                backend=trace["backend"],
                mode=trace["mode"],
                test_case_id=trace["test_case_id"],
                final_outcome=trace["final_outcome"],
                success_rate=trace["metrics"]["success_rate"],
                retries_per_run=trace["metrics"]["retries_per_run"],
                failure_type_counts=trace["metrics"]["failure_type_counts"],
                wrong_click_count=trace["metrics"]["wrong_click_count"],
                avg_step_latency_ms=trace["metrics"]["avg_step_latency_ms"],
            )
            summary_path = self._artifact_dir / "run_summary.json"
            summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            print(
                f"[experiment_logger] saved run_id={self.run_id!r} "
                f"backend={self.backend!r} test_case={self.test_case_id!r} "
                f"outcome={trace['final_outcome']!r} "
                f"dir={self._artifact_dir}",
                flush=True,
            )
        except OSError as exc:
            print(
                f"[experiment_logger] WARNING: could not save artifacts: {exc}",
                flush=True,
            )


def compare_runs(
    playwright_summary: RunSummary,
    ab_summary: RunSummary,
) -> ComparisonReport:
    """
    Produce a structured side-by-side comparison of two RunSummary objects.

    Determines a winner based on success rate. Notes differences in
    wrong-click counts, failure distributions, and step latency so the
    comparison can be reviewed by artifact, not by memory.

    Args:
        playwright_summary — RunSummary from a run_stepwise (Playwright) run.
        ab_summary         — RunSummary from a run_ab_stepwise run.

    Returns:
        ComparisonReport with playwright, agent_browser_cli, winner, notes.
    """
    notes: List[str] = []

    pw_sr = playwright_summary["success_rate"]
    ab_sr = ab_summary["success_rate"]
    threshold_checks = ThresholdChecks(
        ab_at_least_as_good_on_core_paths=ab_sr >= pw_sr,
        reduced_target_selection_failures=(
            _target_selection_failure_count(ab_summary)
            <= _target_selection_failure_count(playwright_summary)
        ),
        explainable_failures=(
            ab_summary["final_outcome"] in {"passed", "ambiguous"}
            or bool(ab_summary["failure_type_counts"])
        ),
    )

    if ab_sr > pw_sr:
        winner = "agent_browser_cli"
        notes.append(f"AB success rate ({ab_sr:.1%}) > Playwright ({pw_sr:.1%})")
    elif pw_sr > ab_sr:
        winner = "playwright"
        notes.append(f"Playwright success rate ({pw_sr:.1%}) > AB ({ab_sr:.1%})")
    else:
        winner = "tie" if pw_sr > 0 else "inconclusive"
        notes.append(f"Equal success rates ({pw_sr:.1%})")

    pw_wc = playwright_summary["wrong_click_count"]
    ab_wc = ab_summary["wrong_click_count"]
    if ab_wc < pw_wc:
        notes.append(f"AB has fewer wrong clicks ({ab_wc} vs Playwright {pw_wc})")
    elif ab_wc > pw_wc:
        notes.append(f"AB has more wrong clicks ({ab_wc} vs Playwright {pw_wc})")

    pw_lat = playwright_summary["avg_step_latency_ms"]
    ab_lat = ab_summary["avg_step_latency_ms"]
    if pw_lat > 0 and ab_lat > 0:
        notes.append(
            f"Avg step latency — Playwright: {pw_lat:.0f}ms  AB: {ab_lat:.0f}ms"
        )

    # Per-taxonomy failure comparison.
    all_taxonomies = set(playwright_summary["failure_type_counts"]) | set(
        ab_summary["failure_type_counts"]
    )
    for tax in sorted(all_taxonomies):
        pw_count = playwright_summary["failure_type_counts"].get(tax, 0)
        ab_count = ab_summary["failure_type_counts"].get(tax, 0)
        if ab_count < pw_count:
            notes.append(f"{tax}: AB {ab_count} < Playwright {pw_count} failures")
        elif ab_count > pw_count:
            notes.append(f"{tax}: AB {ab_count} > Playwright {pw_count} failures")

    if ab_summary["final_outcome"] == "ambiguous":
        decision_outcome: FinalOutcome = "ambiguous"
    elif all(threshold_checks.values()):
        decision_outcome = "passed"
    elif not threshold_checks["ab_at_least_as_good_on_core_paths"] or not threshold_checks[
        "reduced_target_selection_failures"
    ]:
        decision_outcome = "regressed"
    else:
        decision_outcome = "inconclusive"

    return ComparisonReport(
        playwright=playwright_summary,
        agent_browser_cli=ab_summary,
        winner=winner,
        decision_outcome=decision_outcome,
        threshold_checks=threshold_checks,
        notes=notes,
    )


def save_comparison(report: ComparisonReport, artifact_dir: Path) -> None:
    """
    Save a ComparisonReport to comparison_<test_case_id>.json.

    Args:
        report       — output of compare_runs().
        artifact_dir — directory to write the file; typically app/data/experiment_runs/.
    """
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        test_case_id = (
            report["playwright"].get("test_case_id")
            or report["agent_browser_cli"].get("test_case_id")
            or "unknown"
        )
        path = artifact_dir / f"comparison_{test_case_id}.json"
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"[experiment_logger] comparison saved "
            f"test_case={test_case_id!r} winner={report['winner']!r} path={path}",
            flush=True,
        )
    except OSError as exc:
        print(
            f"[experiment_logger] WARNING: could not save comparison: {exc}",
            flush=True,
        )


def load_run_summaries(runs_dir: Path = _RUNS_DIR) -> List[RunSummary]:
    """
    Load the latest saved run summaries from disk.

    Deduplicates by (backend, mode, test_case_id), keeping the most recently
    modified summary file for each key.
    """
    latest_by_key: Dict[tuple[str, str, str], tuple[float, RunSummary]] = {}
    for path in runs_dir.rglob("run_summary.json"):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                continue
            key = (
                str(summary.get("backend") or ""),
                str(summary.get("mode") or ""),
                str(summary.get("test_case_id") or ""),
            )
            mtime = path.stat().st_mtime
            prev = latest_by_key.get(key)
            if prev is None or mtime >= prev[0]:
                latest_by_key[key] = (mtime, summary)  # type: ignore[assignment]
        except Exception as exc:
            print(
                f"[experiment_logger] WARNING: failed to load {path}: {exc}",
                flush=True,
            )
    return [item[1] for item in latest_by_key.values()]


def summarize_experiment(run_summaries: List[RunSummary]) -> ExperimentSummary:
    """
    Build the Phase 5 aggregate experiment summary from per-run summaries.

    Decision rules are evaluated on Mode A (`deterministic`) only:
        1. AB success rate >= Playwright success rate on paired core paths
        2. AB target-selection failures are reduced
        3. AB failures remain explainable from saved artifacts
    """
    mode_summaries: List[ModeSummary] = []
    play_by_test = {
        s["test_case_id"]: s for s in run_summaries if s["backend"] == "playwright"
    }
    ab_modes = sorted({
        s["mode"]
        for s in run_summaries
        if s["backend"] == "agent_browser_cli"
    })
    deterministic_has_paired_baseline = False
    deterministic_paired_test_case_count = 0

    deterministic_thresholds = ThresholdChecks(
        ab_at_least_as_good_on_core_paths=False,
        reduced_target_selection_failures=False,
        explainable_failures=False,
    )

    for mode in ab_modes:
        ab_summaries = [
            s
            for s in run_summaries
            if s["backend"] == "agent_browser_cli" and s["mode"] == mode
        ]
        paired_ab_summaries = [
            s for s in ab_summaries if s["test_case_id"] in play_by_test
        ]
        paired_playwright = [
            play_by_test[s["test_case_id"]]
            for s in ab_summaries
            if s["test_case_id"] in play_by_test
        ]
        test_case_results: List[TestCaseComparison] = []
        ambiguous_cases: List[str] = []
        unexplained_failure_count = 0

        for ab_summary in ab_summaries:
            pw_summary = play_by_test.get(ab_summary["test_case_id"])
            if ab_summary["final_outcome"] == "ambiguous":
                decision_outcome: FinalOutcome = "ambiguous"
                ambiguous_cases.append(ab_summary["test_case_id"])
            elif pw_summary is None:
                decision_outcome = "inconclusive"
            elif (
                ab_summary["success_rate"] < pw_summary["success_rate"]
                or ab_summary["wrong_click_count"] > pw_summary["wrong_click_count"]
            ):
                decision_outcome = "regressed"
            elif ab_summary["final_outcome"] == "passed":
                decision_outcome = "passed"
            else:
                decision_outcome = "inconclusive"

            if ab_summary["final_outcome"] in {"regressed", "inconclusive"} and not ab_summary[
                "failure_type_counts"
            ]:
                unexplained_failure_count += 1

            test_case_results.append(
                TestCaseComparison(
                    test_case_id=ab_summary["test_case_id"],
                    mode=mode,
                    has_paired_baseline=(pw_summary is not None),
                    playwright_outcome=(
                        pw_summary["final_outcome"] if pw_summary else "inconclusive"
                    ),
                    agent_browser_outcome=ab_summary["final_outcome"],
                    decision_outcome=decision_outcome,
                )
            )

        baseline_metrics = _aggregate_run_summaries(paired_playwright)
        agent_metrics = _aggregate_run_summaries(ab_summaries)
        paired_agent_metrics = _aggregate_run_summaries(paired_ab_summaries)
        top_failure_modes = dict(
            Counter(agent_metrics["failure_type_counts"]).most_common(5)
        )

        mode_summary = ModeSummary(
            mode=mode,
            paired_baseline_available=bool(paired_playwright),
            paired_test_case_count=len(paired_playwright),
            agent_only_test_case_count=max(len(ab_summaries) - len(paired_ab_summaries), 0),
            test_case_results=test_case_results,
            baseline_metrics=baseline_metrics,
            agent_browser_metrics=agent_metrics,
            paired_agent_browser_metrics=paired_agent_metrics,
            top_failure_modes=top_failure_modes,
            ambiguous_cases=sorted(set(ambiguous_cases)),
            unexplained_failure_count=unexplained_failure_count,
        )
        mode_summaries.append(mode_summary)

        if mode == "deterministic":
            deterministic_has_paired_baseline = bool(paired_playwright)
            deterministic_paired_test_case_count = len(paired_playwright)
            deterministic_thresholds = ThresholdChecks(
                ab_at_least_as_good_on_core_paths=(
                    paired_agent_metrics["success_rate"] >= baseline_metrics["success_rate"]
                ),
                reduced_target_selection_failures=(
                    _target_selection_failure_count(paired_agent_metrics)
                    <= _target_selection_failure_count(baseline_metrics)
                ),
                explainable_failures=unexplained_failure_count == 0,
            )

    rationale: List[str] = []
    if not any(ms["mode"] == "deterministic" for ms in mode_summaries):
        decision_outcome = "inconclusive"
        recommendation = "inconclusive"
        rationale.append("No Mode A deterministic run summaries were found.")
    elif not deterministic_has_paired_baseline:
        decision_outcome = "inconclusive"
        recommendation = "inconclusive"
        rationale.append(
            "No paired Mode A Playwright baseline exists yet; promotion decisions require paired baseline data."
        )
    elif all(deterministic_thresholds.values()):
        decision_outcome = "passed"
        recommendation = "go"
        rationale.append("Mode A meets all Phase 5 go / no-go thresholds.")
    elif any(
        tc["decision_outcome"] == "ambiguous"
        for ms in mode_summaries
        if ms["mode"] == "deterministic"
        for tc in ms["test_case_results"]
    ):
        decision_outcome = "ambiguous"
        recommendation = "inconclusive"
        rationale.append("Mode A contains ambiguous target cases that block a clear decision.")
    elif not deterministic_thresholds["ab_at_least_as_good_on_core_paths"] or not deterministic_thresholds[
        "reduced_target_selection_failures"
    ]:
        decision_outcome = "regressed"
        recommendation = "no_go"
        rationale.append("Mode A does not outperform or match Playwright on the required thresholds.")
    else:
        decision_outcome = "inconclusive"
        recommendation = "inconclusive"
        rationale.append("Mode A results are mixed and do not support a clear promotion decision.")

    return ExperimentSummary(
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        thresholds=deterministic_thresholds,
        decision=DecisionSummary(
            outcome=decision_outcome,
            recommendation=recommendation,
            promotion_allowed=(decision_outcome == "passed"),
            has_paired_baseline=deterministic_has_paired_baseline,
            paired_test_case_count=deterministic_paired_test_case_count,
            rationale=rationale,
        ),
        mode_summaries=mode_summaries,
    )


def summarize_artifacts(runs_dir: Path = _RUNS_DIR) -> ExperimentSummary:
    """
    Load run summaries from disk, aggregate them, and save experiment_summary.json.
    """
    summary = summarize_experiment(load_run_summaries(runs_dir))
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        summary_path = (
            _EXPERIMENT_SUMMARY_PATH if runs_dir == _RUNS_DIR else runs_dir / "experiment_summary.json"
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"[experiment_logger] experiment summary saved decision={summary['decision']['outcome']!r} "
            f"path={summary_path}",
            flush=True,
        )
    except OSError as exc:
        print(
            f"[experiment_logger] WARNING: could not save experiment summary: {exc}",
            flush=True,
        )
    return summary
