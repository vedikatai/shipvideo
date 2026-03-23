"""
Agent Browser CLI wrapper — Phase 1 / Phase 3.

Provides a thin Python subprocess adapter for the stock `agent-browser` binary
(https://github.com/vercel-labs/agent-browser). All interactions with the
agent-browser daemon go through this module.

Public API:
    AgentBrowserCLI.open(url)               — navigate to URL
    AgentBrowserCLI.snapshot(...)           — take accessibility snapshot,
                                              normalize to AgentBrowserSnapshot
    AgentBrowserCLI.click(ref)              — click element by ref (@e1, @e2, …)
    AgentBrowserCLI.wait(ms)                — wait for a fixed number of ms
    AgentBrowserCLI.screenshot(path)        — save a screenshot to disk
    AgentBrowserCLI.close()                 — close the browser session
    AgentBrowserCLI.get_url()               — return the current page URL
    AgentBrowserCLI.get_text(ref_or_sel)    — return visible text of an element
                                              (Phase 3: post-click text validation)

Execution model:
    agent-browser uses a client-daemon architecture. The daemon starts
    automatically on the first command and persists between commands within
    the same session. Session isolation is controlled via --session <name>.

Error model:
    Any non-zero exit code, subprocess timeout, or JSON success=False raises
    AgentBrowserError. The exception carries the full command list, stderr
    output, and exit code so failures can be debugged without guessing.

Raw snapshot persistence:
    Every snapshot call saves the raw CLI JSON payload to
    app/data/ab_snapshots/ by default. These files are the primary debugging
    artifact for the accuracy experiment. Disable with save_raw=False.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import CommandResult, SnapshotPayload
from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot

# Directory for raw snapshot JSON artifacts (created on first write).
_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "ab_snapshots"

_INTERACTIVE_ROLES = frozenset({
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "tab",
    "menuitem",
    "option",
})


class AgentBrowserError(RuntimeError):
    """
    Raised when an agent-browser CLI command fails.

    Attributes:
        command    — the exact argv list that was executed.
        stderr     — captured stderr from the process.
        exit_code  — process exit code (-1 for binary-not-found,
                     -2 for timeout, otherwise the real exit code).
    """

    def __init__(
        self,
        message: str,
        *,
        command: List[str],
        stderr: str,
        exit_code: int,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.exit_code = exit_code

    def __str__(self) -> str:
        base = super().__str__()
        return (
            f"{base} "
            f"[exit_code={self.exit_code} "
            f"cmd={' '.join(self.command)!r} "
            f"stderr={self.stderr.strip()!r}]"
        )


class AgentBrowserCLI:
    """
    Thin Python wrapper around the stock `agent-browser` CLI binary.

    Each instance corresponds to one named session. Multiple commands issued
    through the same instance share browser state via the agent-browser
    daemon. Use distinct session names for parallel or isolated runs.

    Args:
        session — agent-browser session name (--session flag). Defaults to
                  "default". Use a unique value per concurrent job to ensure
                  browser state isolation.
        binary  — path to the agent-browser executable. Defaults to
                  "agent-browser" (expects it to be on $PATH).
    """

    def __init__(
        self,
        *,
        session: str = "default",
        binary: str = "agent-browser",
    ) -> None:
        self._session = session
        self._binary = binary

    # ------------------------------------------------------------------
    # Internal subprocess runner
    # ------------------------------------------------------------------

    def _run(
        self,
        *args: str,
        json_output: bool = True,
        timeout: int = 60,
    ) -> CommandResult:
        """
        Execute one agent-browser command via subprocess.

        Global flags prepended in order:
            --session <name>   — isolates daemon state per session.
            --json             — requests structured JSON output (when
                                 json_output=True).

        Raises AgentBrowserError on:
            - binary not found (FileNotFoundError → exit_code=-1)
            - subprocess timeout (exit_code=-2)
            - non-zero process exit code
            - JSON envelope with success=False

        Returns CommandResult with parsed data on success.
        """
        cmd: List[str] = [self._binary, "--session", self._session]
        if json_output:
            cmd.append("--json")
        cmd.extend(str(a) for a in args)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise AgentBrowserError(
                f"agent-browser binary not found: {self._binary!r}. "
                "Install with: npm install -g agent-browser && agent-browser install",
                command=cmd,
                stderr="",
                exit_code=-1,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentBrowserError(
                f"agent-browser command timed out after {timeout}s",
                command=cmd,
                stderr="",
                exit_code=-2,
            ) from exc

        if proc.returncode != 0:
            raise AgentBrowserError(
                "agent-browser command returned non-zero exit code",
                command=cmd,
                stderr=proc.stderr,
                exit_code=proc.returncode,
            )

        data: Dict[str, Any] = {}
        if json_output and proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout.strip())
                if isinstance(parsed, dict):
                    if not parsed.get("success", True):
                        raise AgentBrowserError(
                            "agent-browser reported success=false in JSON envelope",
                            command=cmd,
                            stderr=proc.stderr,
                            exit_code=proc.returncode,
                        )
                    data = parsed.get("data") or {}
            except json.JSONDecodeError:
                # Non-JSON stdout is acceptable for a small set of commands
                # (e.g. plain-text snapshot output without --json). The caller
                # can still read result["stdout"] directly in that case.
                pass

        return CommandResult(
            success=True,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            data=data,
        )

    # ------------------------------------------------------------------
    # Public command API
    # ------------------------------------------------------------------

    def open(self, url: str) -> CommandResult:
        """
        Navigate to url. Starts the agent-browser daemon automatically if it
        is not already running.

        Corresponds to: agent-browser open <url>
        """
        print(f"[agent_browser] open url={url!r}", flush=True)
        return self._run("open", url)

    def snapshot(
        self,
        *,
        interactive: bool = True,
        cursor: bool = True,
        compact: bool = True,
        save_raw: bool = True,
    ) -> AgentBrowserSnapshot:
        """
        Take an accessibility snapshot of the current page and return a
        normalized AgentBrowserSnapshot.

        CLI flag mapping:
            interactive=True  → -i  (interactive elements only: buttons,
                                      links, inputs)
            cursor=True       → -C  (also include cursor-interactive elements
                                      such as divs with onclick/tabindex; needed
                                      for modern web apps that use non-semantic
                                      clickable elements)
            compact=True      → -c  (remove empty structural elements to reduce
                                      snapshot size)

        Args:
            save_raw — when True, persist the raw CLI JSON payload to
                       app/data/ab_snapshots/ for experiment debugging.
                       Set to False only in performance-sensitive hot paths.

        Returns:
            AgentBrowserSnapshot with current_url, snapshot_text,
            interactive_elements, context_elements, and raw_snapshot_path.
        """
        args = ["snapshot"]
        if interactive:
            args.append("-i")
        if cursor:
            args.append("-C")
        if compact:
            args.append("-c")

        result = self._run(*args)

        current_url = self.get_url()

        raw_path = ""
        if save_raw:
            raw_path = self._save_raw_snapshot(result)

        return self._normalize_snapshot(
            result,
            current_url=current_url,
            raw_snapshot_path=raw_path,
        )

    def click(self, ref: str) -> CommandResult:
        """
        Click the element identified by ref (e.g. "@e1").

        Refs are stable within a snapshot session but become stale after
        navigation or significant DOM changes. Always re-snapshot after a
        click that causes page state to change.

        Corresponds to: agent-browser click <ref>
        """
        print(f"[agent_browser] click ref={ref!r}", flush=True)
        return self._run("click", ref)

    def wait(self, ms: int) -> CommandResult:
        """
        Wait for a fixed number of milliseconds.

        Use after a click to allow the page to settle before taking a
        follow-up snapshot. The plan mandates a minimum 1000–2000 ms
        floor after any click that may trigger navigation or animation.

        Corresponds to: agent-browser wait <ms>
        """
        return self._run("wait", str(ms))

    def screenshot(self, path: str | Path) -> CommandResult:
        """
        Save a screenshot of the current page to path.

        The parent directory of path must exist before calling this method.
        Corresponds to: agent-browser screenshot <path>

        json_output is disabled because the screenshot command writes a binary
        file to disk; structured JSON output is not required.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"[agent_browser] screenshot path={target}", flush=True)
        return self._run("screenshot", str(target), json_output=False)

    def close(self) -> CommandResult:
        """
        Close the current browser session.

        Corresponds to: agent-browser close
        """
        print(f"[agent_browser] close session={self._session!r}", flush=True)
        return self._run("close", json_output=False)

    def get_url(self) -> str:
        """
        Return the current page URL.

        Used internally by snapshot() to annotate normalized elements with
        the URL at which they were observed.

        Corresponds to: agent-browser get url
        Returns empty string on any failure (e.g. browser not open yet).
        """
        try:
            result = self._run("get", "url")
            return str(result["data"].get("url") or "")
        except AgentBrowserError:
            return ""

    def get_text(self, ref_or_selector: str) -> str:
        """
        Return the visible text content of an element by ref (@e1) or CSS selector.

        Useful for post-click success validation — check that expected text
        appeared in the target element after an interaction:

            text = cli.get_text("@e3")
            if "API Key" in text: ...

        Corresponds to: agent-browser get text <ref_or_selector>
        Returns empty string on any failure (element not found, browser closed).
        """
        try:
            result = self._run("get", "text", ref_or_selector)
            return str(result["data"].get("text") or "")
        except AgentBrowserError:
            return ""

    # ------------------------------------------------------------------
    # Snapshot persistence
    # ------------------------------------------------------------------

    def _save_raw_snapshot(self, result: CommandResult) -> str:
        """
        Persist the raw CLI output from a snapshot command to disk.

        Saves to app/data/ab_snapshots/snapshot_{session}_{utc_ts}.json.
        Creates the directory if it does not exist.

        Returns the file path string on success, or "" if the write fails
        (logged as a warning; never raises — disk errors must not abort the
        experiment pipeline).
        """
        try:
            _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            path = _SNAPSHOT_DIR / f"snapshot_{self._session}_{ts}.json"
            payload = {
                "session": self._session,
                "stdout": result["stdout"],
                "data": result["data"],
            }
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[agent_browser] raw snapshot saved path={path}", flush=True)
            return str(path)
        except OSError as exc:
            print(
                f"[agent_browser] warning: could not save raw snapshot: {exc}",
                flush=True,
            )
            return ""

    # ------------------------------------------------------------------
    # Snapshot normalization
    # ------------------------------------------------------------------

    def _normalize_snapshot(
        self,
        result: CommandResult,
        *,
        current_url: str,
        raw_snapshot_path: str,
    ) -> AgentBrowserSnapshot:
        """
        Convert a raw CLI CommandResult into a normalized AgentBrowserSnapshot.

        Expected agent-browser JSON envelope (with --json):
            {
                "success": true,
                "data": {
                    "snapshot": "<accessibility tree text>",
                    "refs": {
                        "e1": {"role": "button", "name": "Submit"},
                        "e2": {"role": "textbox", "name": "Email"},
                        ...
                    }
                }
            }

        Normalization rules per element:
            ref     — "@{ref_id}" (@ prefix added; agent-browser omits it in
                       the refs dict keys)
            role    — lowercased; empty string if absent
            name    — stripped; empty string if absent
            url     — current_url at snapshot time
            visible — always True (interactive snapshot only surfaces visible
                       interactive elements)

        Snapshot bucketing:
            interactive_elements — only the allowlisted actionable roles used
                                   by the ref selector.
            context_elements     — everything else from the snapshot, preserved
                                   for debugging and validation only.

        If refs dict is empty or missing, interactive_elements will be an
        empty list. This is a valid result (e.g. a blank page or a page with
        no interactive elements), not an error.

        snapshot_text falls back to raw stdout if the "snapshot" key is
        absent from data (defensive: handles plain-text output if --json
        was not honoured by an older CLI version).
        """
        data = result.get("data") or {}
        refs: Dict[str, Any] = data.get("refs") or {}
        snapshot_text: str = data.get("snapshot") or result["stdout"]

        interactive_elements: List[AgentBrowserElement] = []
        context_elements: List[AgentBrowserElement] = []
        for ref_id, meta in refs.items():
            if not isinstance(meta, dict):
                continue
            element = AgentBrowserElement(
                ref=f"@{ref_id}",
                role=(meta.get("role") or "").lower().strip(),
                name=(meta.get("name") or "").strip(),
                url=current_url,
                visible=True,
            )
            if element["role"] in _INTERACTIVE_ROLES:
                interactive_elements.append(element)
            else:
                context_elements.append(element)

        print(
            f"[agent_browser] snapshot normalized "
            f"interactive={len(interactive_elements)} "
            f"context={len(context_elements)} "
            f"url={current_url!r}",
            flush=True,
        )

        return AgentBrowserSnapshot(
            current_url=current_url,
            snapshot_text=snapshot_text,
            interactive_elements=interactive_elements,
            context_elements=context_elements,
            raw_snapshot_path=raw_snapshot_path,
        )


# ---------------------------------------------------------------------------
# Manual smoke-test harness (run with: python -m app.browser.agent_browser_cli)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    print(f"[smoke_test] target url={target_url!r}", flush=True)

    cli = AgentBrowserCLI(session="smoke_test")

    try:
        cli.open(target_url)
        print("[smoke_test] open: OK", flush=True)

        snap = cli.snapshot()
        print(f"[smoke_test] current_url={snap['current_url']!r}", flush=True)
        print(
            f"[smoke_test] elements_found={len(snap['interactive_elements'])}",
            flush=True,
        )

        if snap["interactive_elements"]:
            print("[smoke_test] first 5 elements:", flush=True)
            for el in snap["interactive_elements"][:5]:
                print(
                    f"  {el['ref']}  [{el['role']}]  {el['name']!r}",
                    flush=True,
                )
            print("[smoke_test] PASS: snapshot parsed refs successfully", flush=True)
        else:
            print(
                "[smoke_test] WARNING: no interactive elements found in snapshot",
                flush=True,
            )

        if snap["raw_snapshot_path"]:
            print(
                f"[smoke_test] raw snapshot saved to: {snap['raw_snapshot_path']}",
                flush=True,
            )

    except AgentBrowserError as exc:
        print(f"[smoke_test] FAIL: {exc}", flush=True)
        sys.exit(1)
    finally:
        try:
            cli.close()
        except AgentBrowserError:
            pass
