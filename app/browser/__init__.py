"""
Agent Browser browser module — Phase 1 / Phase 4.

Public exports from this module:
    AgentBrowserCLI   — thin subprocess wrapper for the stock agent-browser binary.
    AgentBrowserError — raised on any CLI command failure.
    CommandResult     — TypedDict for raw CLI subprocess output.
    SnapshotPayload   — TypedDict for the intermediate raw snapshot form.
    ExperimentLogger  — Phase 4: per-run artifact writer for accuracy experiment.
    compare_runs      — Phase 4: side-by-side backend comparison.
    save_comparison   — Phase 4: persist ComparisonReport to disk.

Stable downstream contract types (in app.dom_schema, not re-exported here):
    AgentBrowserElement, AgentBrowserSnapshot, ExperimentMode
"""
from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError
from app.browser.agent_browser_types import CommandResult, SnapshotPayload
from app.browser.experiment_logger import ExperimentLogger, compare_runs, save_comparison

__all__ = [
    "AgentBrowserCLI",
    "AgentBrowserError",
    "CommandResult",
    "SnapshotPayload",
    "ExperimentLogger",
    "compare_runs",
    "save_comparison",
]
