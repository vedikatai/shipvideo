"""
Agent Browser CLI wrapper — Phase 1 / Phase 3.

Provides a thin Python subprocess adapter for the stock `agent-browser` binary
(https://github.com/vercel-labs/agent-browser). All interactions with the
agent-browser daemon go through this module.

Public API:
    AgentBrowserCLI.open(url)               — navigate to URL
    AgentBrowserCLI.set_viewport(...)       — set deterministic viewport size
    AgentBrowserCLI.snapshot(...)           — take accessibility snapshot,
                                              normalize to AgentBrowserSnapshot
    AgentBrowserCLI.click(ref)              — click element by ref (@e1, @e2, …)
    AgentBrowserCLI.wait(ms)                — wait for a fixed number of ms
    AgentBrowserCLI.wait_for_load_state(...) — wait for page load state
    AgentBrowserCLI.wait_for_text(...)      — wait for text to appear
    AgentBrowserCLI.wait_for_url(...)       — wait for URL pattern
    AgentBrowserCLI.scroll_into_view(...)   — move a target into view
    AgentBrowserCLI.is_visible(...)         — check whether a target is visible
    AgentBrowserCLI.is_enabled(...)         — check whether a target is enabled
    AgentBrowserCLI.console_messages()      — read browser console entries
    AgentBrowserCLI.page_errors()           — read page errors/exceptions
    AgentBrowserCLI.network_requests()      — read network request history
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
import re
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


def _snapshot_name_set(snapshot: AgentBrowserSnapshot) -> set[str]:
    names: set[str] = set()
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            name = str(element.get("name") or "").strip()
            if name:
                names.add(name)
    return names


def compare_snapshots(
    before: AgentBrowserSnapshot,
    after: AgentBrowserSnapshot,
) -> Dict[str, Any]:
    """
    Compute a human-friendly UI diff from two snapshots.

    Returns a compact dict suitable for narration:
      {
        "url_changed": bool,
        "from_url": str,
        "to_url": str,
        "added_elements": [str],
        "removed_elements": [str],
        "changed": bool,
        "summary": str
      }
    """
    before_names = _snapshot_name_set(before)
    after_names = _snapshot_name_set(after)
    added = sorted(after_names - before_names)[:12]
    removed = sorted(before_names - after_names)[:12]
    from_url = str(before.get("current_url") or "")
    to_url = str(after.get("current_url") or "")
    url_changed = from_url != to_url
    changed = bool(url_changed or added or removed)
    if not changed:
        summary = "No meaningful UI change detected."
    else:
        parts: List[str] = []
        if url_changed:
            parts.append(f"URL changed to {to_url or '(unknown)'}")
        if added:
            parts.append("Appeared: " + ", ".join(added[:4]))
        if removed:
            parts.append("Disappeared: " + ", ".join(removed[:4]))
        summary = "; ".join(parts)
    return {
        "url_changed": url_changed,
        "from_url": from_url,
        "to_url": to_url,
        "added_elements": added,
        "removed_elements": removed,
        "changed": changed,
        "summary": summary,
    }


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

    def set_viewport(self, width: int, height: int) -> CommandResult:
        """
        Set the browser viewport to a deterministic size for the session.

        Corresponds to: agent-browser set viewport <width> <height>
        """
        width_px = int(width)
        height_px = int(height)
        print(
            f"[agent_browser] set viewport width={width_px} height={height_px}",
            flush=True,
        )
        return self._run("set", "viewport", str(width_px), str(height_px))

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

    def wait_for_load_state(self, state: str, *, timeout: int = 15) -> CommandResult:
        """
        Wait until the page reaches a load state.

        Corresponds to: agent-browser wait --load <state>
        """
        state_norm = (state or "").strip().lower()
        if state_norm not in {"domcontentloaded", "networkidle"}:
            raise ValueError(f"unsupported load state: {state!r}")
        print(f"[agent_browser] wait load_state={state_norm!r}", flush=True)
        return self._run("wait", "--load", state_norm, timeout=timeout)

    def wait_for_text(self, text: str, *, timeout: int = 10) -> CommandResult:
        """
        Wait until the given text is visible on the page.

        Corresponds to: agent-browser wait --text <text>
        """
        expected = (text or "").strip()
        if not expected:
            raise ValueError("text cannot be empty")
        print(f"[agent_browser] wait text={expected!r}", flush=True)
        return self._run("wait", "--text", expected, timeout=timeout)

    def wait_for_url(self, pattern: str, *, timeout: int = 10) -> CommandResult:
        """
        Wait until the current URL matches the provided pattern.

        Corresponds to: agent-browser wait --url <pattern>
        """
        expected = (pattern or "").strip()
        if not expected:
            raise ValueError("pattern cannot be empty")
        print(f"[agent_browser] wait url={expected!r}", flush=True)
        return self._run("wait", "--url", expected, timeout=timeout)

    def scroll_into_view(self, ref_or_selector: str) -> CommandResult:
        """
        Scroll the target into the viewport before interaction.

        Corresponds to: agent-browser scrollintoview <ref_or_selector>
        """
        target = (ref_or_selector or "").strip()
        if not target:
            raise ValueError("ref_or_selector cannot be empty")
        print(f"[agent_browser] scrollintoview target={target!r}", flush=True)
        return self._run("scrollintoview", target)

    def scroll(self, direction: str = "down", px: int = 700) -> CommandResult:
        """
        Scroll page viewport to surface off-screen targets.

        Corresponds to: agent-browser scroll <direction> <px>
        """
        dir_norm = (direction or "down").strip().lower() or "down"
        dist = int(px)
        print(f"[agent_browser] scroll direction={dir_norm!r} px={dist}", flush=True)
        return self._run("scroll", dir_norm, str(dist))

    def is_visible(self, ref_or_selector: str) -> bool:
        """Return True when the target is currently visible."""
        try:
            result = self._run("is", "visible", ref_or_selector)
            return self._coerce_bool(result, primary_keys=("visible", "isVisible"))
        except AgentBrowserError:
            return False

    def is_enabled(self, ref_or_selector: str) -> bool:
        """Return True when the target is currently enabled."""
        try:
            result = self._run("is", "enabled", ref_or_selector)
            return self._coerce_bool(result, primary_keys=("enabled", "isEnabled"))
        except AgentBrowserError:
            return False

    def console_messages(self) -> List[str]:
        try:
            result = self._run("console")
            return self._coerce_string_list(result)
        except AgentBrowserError:
            return []

    def page_errors(self) -> List[str]:
        try:
            result = self._run("errors")
            return self._coerce_string_list(result)
        except AgentBrowserError:
            return []

    def network_requests(self) -> List[Dict[str, Any]]:
        try:
            result = self._run("network", "requests")
            return self._coerce_request_list(result)
        except AgentBrowserError:
            return []

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

    def get_count(self, selector: str) -> int:
        try:
            result = self._run("get", "count", selector)
            return self._coerce_int(result, primary_keys=("count", "value"))
        except AgentBrowserError:
            return 0

    def get_attr(self, ref_or_selector: str, attr: str) -> str:
        try:
            result = self._run("get", "attr", ref_or_selector, attr)
            data = result.get("data") or {}
            for key in ("value", "attr", attr):
                value = data.get(key)
                if value is not None:
                    return str(value)
            return str(result.get("stdout") or "").strip()
        except AgentBrowserError:
            return ""

    def find_testid_ref(self, testid: str) -> str:
        target = (testid or "").strip()
        if not target:
            return ""
        try:
            res = self._run("find", "testid", target, "text")
            ref = self._extract_ref_from_find_output(res)
            if ref:
                print(f"[agent_browser] find_testid_ref testid={target!r} ref={ref!r}", flush=True)
            return ref
        except AgentBrowserError:
            return ""

    def find_role_ref(self, role: str, name: str) -> str:
        role_norm = (role or "").strip().lower()
        target = (name or "").strip()
        if not role_norm or not target:
            return ""
        try:
            res = self._run("find", "role", role_norm, "text", "--name", target)
            ref = self._extract_ref_from_find_output(res)
            if ref:
                print(
                    f"[agent_browser] find_role_ref role={role_norm!r} name={target!r} ref={ref!r}",
                    flush=True,
                )
            return ref
        except AgentBrowserError:
            return ""

    def find_label_ref(self, label: str) -> str:
        target = (label or "").strip()
        if not target:
            return ""
        try:
            res = self._run("find", "label", target, "text")
            ref = self._extract_ref_from_find_output(res)
            if ref:
                print(f"[agent_browser] find_label_ref label={target!r} ref={ref!r}", flush=True)
            return ref
        except AgentBrowserError:
            return ""

    def find_ref(self, intent: str) -> str:
        """
        Try Agent Browser semantic `find` commands and return a discovered ref.
        Returns "" when no ref can be extracted.
        """
        intent = (intent or "").strip()
        if not intent:
            return ""
        for resolver in (
            lambda: self.find_role_ref("button", intent),
            lambda: self.find_role_ref("link", intent),
            lambda: self.find_label_ref(intent),
        ):
            ref = resolver()
            if ref:
                print(f"[agent_browser] find_ref intent={intent!r} ref={ref!r}", flush=True)
                return ref
        try:
            res = self._run("find", "text", intent, "text")
            ref = self._extract_ref_from_find_output(res)
            if ref:
                print(f"[agent_browser] find_ref intent={intent!r} ref={ref!r}", flush=True)
                return ref
        except AgentBrowserError:
            pass
        return ""

    def _extract_ref_from_find_output(self, result: CommandResult) -> str:
        # Try structured payload first.
        data = result.get("data") or {}
        maybe = self._find_ref_in_obj(data)
        if maybe:
            return maybe
        # Fallback to stdout parsing.
        out = str(result.get("stdout") or "")
        m = re.search(r"@e\d+", out)
        if m:
            return m.group(0)
        m = re.search(r"ref[=\s]e(\d+)", out)
        if m:
            return f"@e{m.group(1)}"
        return ""

    def _coerce_bool(
        self,
        result: CommandResult,
        *,
        primary_keys: tuple[str, ...],
    ) -> bool:
        data = result.get("data") or {}
        for key in primary_keys:
            value = data.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "false"}:
                    return lowered == "true"
        stdout = str(result.get("stdout") or "").strip().lower()
        if stdout in {"true", "false"}:
            return stdout == "true"
        return False

    def _coerce_int(
        self,
        result: CommandResult,
        *,
        primary_keys: tuple[str, ...],
    ) -> int:
        data = result.get("data") or {}
        for key in primary_keys:
            value = data.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.isdigit():
                    return int(stripped)
        stdout = str(result.get("stdout") or "").strip()
        if stdout.isdigit():
            return int(stdout)
        return 0

    def _coerce_string_list(self, result: CommandResult) -> List[str]:
        data = result.get("data") or {}
        for key in ("messages", "errors", "items", "logs", "entries"):
            value = data.get(key)
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def _coerce_request_list(self, result: CommandResult) -> List[Dict[str, Any]]:
        data = result.get("data") or {}
        raw_items: Any = data
        if isinstance(data, dict):
            for key in ("requests", "items", "entries"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    raw_items = candidate
                    break
        if not isinstance(raw_items, list):
            return []
        requests: List[Dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                requests.append(item)
            elif isinstance(item, str) and item.strip():
                requests.append({"summary": item.strip()})
        return requests

    def _find_ref_in_obj(self, obj: Any) -> str:
        if isinstance(obj, dict):
            for k in ("ref", "elementRef", "selected_ref"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    vv = v.strip()
                    return vv if vv.startswith("@") else (f"@{vv}" if vv.startswith("e") else "")
            for v in obj.values():
                r = self._find_ref_in_obj(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_ref_in_obj(item)
                if r:
                    return r
        return ""

    def compare_snapshots(
        self,
        before: AgentBrowserSnapshot,
        after: AgentBrowserSnapshot,
    ) -> Dict[str, Any]:
        """Instance wrapper for snapshot diffing used by step runner."""
        return compare_snapshots(before, after)

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
