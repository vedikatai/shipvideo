from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

from app.dom_schema import SuccessCondition






_RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "experiment_runs"


_TEST_SUITE_PATH = Path(__file__).resolve().parent.parent / "data" / "test_suite.json"


_EXPERIMENT_SUMMARY_PATH = _RUNS_DIR / "experiment_summary.json"






class TestCase(TypedDict):

    id: str
    category: str
    description: str
    intent: str
    route: str
    steps: List[Dict[str, Any]]
    success_condition: SuccessCondition










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







FailureTaxonomy = Literal[
    "NO_MATCH", "AMBIGUOUS", "WRONG_CLICK", "CLICK_FAILED", "TIMEOUT", "STALE_REF"
]




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
    if not outcome or outcome in ("success", "pending", "ok", "unvalidated"):
        return ""
    for key, taxonomy in _OUTCOME_TO_TAXONOMY.items():
        if outcome == key or outcome.startswith(f"{key}:"):
            return taxonomy
    if outcome.startswith("validation_failed"):
        return "CLICK_FAILED"
    return "CLICK_FAILED"                                              







FinalOutcome = Literal["passed", "ambiguous", "regressed", "inconclusive"]


class StepTrace(TypedDict):

    step_index: int
    backend: str
    mode: str
    intent: str
    raw_snapshot_path: str
    chosen_ref: str
    selection_reason: str
    candidate_count: int
    action: str
    result: str                                                    
    failure_reason: str                      
    failure_taxonomy: str                                       
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

    run_id: str
    backend: str
    mode: str
    test_case_id: str
    created_at: str                                   
    steps: List[StepTrace]
    final_outcome: FinalOutcome
    metrics: Dict[str, Any]


class RunSummary(TypedDict):

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

    ab_at_least_as_good_on_core_paths: bool
    reduced_target_selection_failures: bool
    explainable_failures: bool


class DecisionSummary(TypedDict):

    outcome: FinalOutcome
    recommendation: str
    promotion_allowed: bool
    has_paired_baseline: bool
    paired_test_case_count: int
    rationale: List[str]


class TestCaseComparison(TypedDict):

    test_case_id: str
    mode: str
    has_paired_baseline: bool
    playwright_outcome: FinalOutcome
    agent_browser_outcome: FinalOutcome
    decision_outcome: FinalOutcome


class ModeSummary(TypedDict):

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

    playwright: RunSummary
    agent_browser_cli: RunSummary
    winner: str                                                                            
    decision_outcome: FinalOutcome
    threshold_checks: ThresholdChecks
    notes: List[str]


class ExperimentSummary(TypedDict):

    generated_at: str
    thresholds: ThresholdChecks
    decision: DecisionSummary
    mode_summaries: List[ModeSummary]






def _compute_metrics(
    steps: List[StepTrace],
    total_initial_steps: int,
    retries_per_run: float,
) -> Dict[str, Any]:
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
    value = str(runner_result.get("final_outcome") or "").strip().lower()
    if value in {"passed", "ambiguous", "regressed", "inconclusive"}:
        return value                              

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
    counts = metrics.get("failure_type_counts") or {}
    return sum(int(counts.get(key, 0)) for key in _TARGET_SELECTION_FAILURES)






class ExperimentLogger:

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


        final_outcome = _coerce_final_outcome(runner_result)


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
                latest_by_key[key] = (mtime, summary)                            
        except Exception as exc:
            print(
                f"[experiment_logger] WARNING: failed to load {path}: {exc}",
                flush=True,
            )
    return [item[1] for item in latest_by_key.values()]


def summarize_experiment(run_summaries: List[RunSummary]) -> ExperimentSummary:
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
