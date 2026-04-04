from __future__ import annotations

import json
import subprocess
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.browser.agent_browser_types import CommandResult, SnapshotPayload
from app.dom_schema import AgentBrowserElement, AgentBrowserSnapshot


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

    def __init__(
        self,
        *,
        session: str = "default",
        binary: str = "agent-browser",
    ) -> None:
        self._session = session
        self._binary = binary





    def _run(
        self,
        *args: str,
        json_output: bool = True,
        timeout: int = 60,
    ) -> CommandResult:
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



                pass

        return CommandResult(
            success=True,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            data=data,
        )





    def open(self, url: str) -> CommandResult:
        print(f"[agent_browser] open url={url!r}", flush=True)
        return self._run("open", url)

    def set_viewport(self, width: int, height: int) -> CommandResult:
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
        print(f"[agent_browser] click ref={ref!r}", flush=True)
        return self._run("click", ref)

    def wait(self, ms: int) -> CommandResult:
        return self._run("wait", str(ms))

    def wait_for_load_state(self, state: str, *, timeout: int = 15) -> CommandResult:
        state_norm = (state or "").strip().lower()
        if state_norm not in {"domcontentloaded", "networkidle"}:
            raise ValueError(f"unsupported load state: {state!r}")
        print(f"[agent_browser] wait load_state={state_norm!r}", flush=True)
        return self._run("wait", "--load", state_norm, timeout=timeout)

    def wait_for_text(self, text: str, *, timeout: int = 10) -> CommandResult:
        expected = (text or "").strip()
        if not expected:
            raise ValueError("text cannot be empty")
        print(f"[agent_browser] wait text={expected!r}", flush=True)
        return self._run("wait", "--text", expected, timeout=timeout)

    def wait_for_url(self, pattern: str, *, timeout: int = 10) -> CommandResult:
        expected = (pattern or "").strip()
        if not expected:
            raise ValueError("pattern cannot be empty")
        print(f"[agent_browser] wait url={expected!r}", flush=True)
        return self._run("wait", "--url", expected, timeout=timeout)

    def scroll_into_view(self, ref_or_selector: str) -> CommandResult:
        target = (ref_or_selector or "").strip()
        if not target:
            raise ValueError("ref_or_selector cannot be empty")
        print(f"[agent_browser] scrollintoview target={target!r}", flush=True)
        return self._run("scrollintoview", target)

    def scroll(self, direction: str = "down", px: int = 700) -> CommandResult:
        dir_norm = (direction or "down").strip().lower() or "down"
        dist = int(px)
        print(f"[agent_browser] scroll direction={dir_norm!r} px={dist}", flush=True)
        return self._run("scroll", dir_norm, str(dist))

    def is_visible(self, ref_or_selector: str) -> bool:
        try:
            result = self._run("is", "visible", ref_or_selector)
            return self._coerce_bool(result, primary_keys=("visible", "isVisible"))
        except AgentBrowserError:
            return False

    def is_enabled(self, ref_or_selector: str) -> bool:
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
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"[agent_browser] screenshot path={target}", flush=True)
        return self._run("screenshot", str(target), json_output=False)

    def close(self) -> CommandResult:
        print(f"[agent_browser] close session={self._session!r}", flush=True)
        return self._run("close", json_output=False)

    def get_url(self) -> str:
        try:
            result = self._run("get", "url")
            return str(result["data"].get("url") or "")
        except AgentBrowserError:
            return ""

    def get_text(self, ref_or_selector: str) -> str:
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

    def find_testid(self, testid: str) -> str:
        return self.find_testid_ref(testid)

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

        data = result.get("data") or {}
        maybe = self._find_ref_in_obj(data)
        if maybe:
            return maybe

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
        return compare_snapshots(before, after)





    def _save_raw_snapshot(self, result: CommandResult) -> str:
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





    def _normalize_snapshot(
        self,
        result: CommandResult,
        *,
        current_url: str,
        raw_snapshot_path: str,
    ) -> AgentBrowserSnapshot:
        data = result.get("data") or {}
        refs: Dict[str, Any] = data.get("refs") or {}
        snapshot_text: str = data.get("snapshot") or result["stdout"]
        current_path = urlparse(current_url).path or "/"

        interactive_elements: List[AgentBrowserElement] = []
        context_elements: List[AgentBrowserElement] = []
        active_surfaces: List[str] = []
        headings: List[str] = []
        for ref_id, meta in refs.items():
            if not isinstance(meta, dict):
                continue
            role = (meta.get("role") or "").lower().strip()
            name = (meta.get("name") or "").strip()
            testid = str(meta.get("testid") or meta.get("data-testid") or "").strip()
            aria_label = str(meta.get("ariaLabel") or meta.get("aria-label") or "").strip()
            element_id = str(meta.get("id") or "").strip()
            nearby_text = str(
                meta.get("nearbyText")
                or meta.get("text")
                or meta.get("description")
                or ""
            ).strip()
            surface = str(
                meta.get("surface")
                or meta.get("container")
                or meta.get("region")
                or meta.get("dialog")
                or ""
            ).strip()
            href = str(meta.get("href") or "").strip()
            element = AgentBrowserElement(
                ref=f"@{ref_id}",
                role=role,
                name=name,
                url=current_url,
                visible=True,
                testid=testid,
                aria_label=aria_label,
                element_id=element_id,
                nearby_text=nearby_text,
                surface=surface,
                href=href,
            )
            if element["role"] in _INTERACTIVE_ROLES:
                interactive_elements.append(element)
            else:
                context_elements.append(element)
            if surface and surface not in active_surfaces:
                active_surfaces.append(surface)
            if role == "heading" and name and name not in headings:
                headings.append(name)

        print(
            f"[agent_browser] snapshot normalized "
            f"interactive={len(interactive_elements)} "
            f"context={len(context_elements)} "
            f"url={current_url!r}",
            flush=True,
        )

        return AgentBrowserSnapshot(
            current_url=current_url,
            current_path=current_path,
            snapshot_text=snapshot_text,
            interactive_elements=interactive_elements,
            context_elements=context_elements,
            raw_snapshot_path=raw_snapshot_path,
            active_surfaces=active_surfaces[:8],
            headings=headings[:12],
        )






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
