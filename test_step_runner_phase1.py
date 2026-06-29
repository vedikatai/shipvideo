import unittest
import sys
import types
import tempfile
from unittest.mock import patch
from pathlib import Path

playwright_module = types.ModuleType("playwright")
playwright_sync_api = types.ModuleType("playwright.sync_api")
playwright_async_api = types.ModuleType("playwright.async_api")
playwright_sync_api.Page = object
playwright_sync_api.sync_playwright = lambda: None
playwright_async_api.async_playwright = lambda: None
playwright_module.sync_api = playwright_sync_api
playwright_module.async_api = playwright_async_api
sys.modules.setdefault("playwright", playwright_module)
sys.modules.setdefault("playwright.sync_api", playwright_sync_api)
sys.modules.setdefault("playwright.async_api", playwright_async_api)

observability_module = types.ModuleType("observability")
observability_module.record_agent_browser_diagnostics = lambda **kwargs: None
observability_module.pipeline_step = lambda name: (lambda fn: fn)
sys.modules.setdefault("observability", observability_module)

from app.config_types import CaptureSettings
from app.steps.contract_extraction import extract_contract_static
from app.steps.preflight import preflight_gate
from app.steps.step_generation import _sanitize_terminal_assertions
from app.execution.step_runner import (
    _assert_ab_terminal_condition,
    _collect_ab_failure_diagnostics,
    _discard_step_screenshots,
    _recover_ab_prerequisite_steps,
    _ensure_ab_target_actionable,
    _configure_ab_session,
    _resolve_ab_click_target,
    _scroll_to_find,
    _should_keep_click_screenshots,
    _settle_ab_page,
)


class _FakeCLI:
    def __init__(
        self,
        *,
        fail_load_states=None,
        fail_text=False,
        fail_url=False,
        found_ref="",
        found_testid_ref="",
        found_label_ref="",
        found_role_button_ref="",
        found_role_link_ref="",
        visible=True,
        enabled=True,
    ):
        self.calls = []
        self.fail_load_states = set(fail_load_states or [])
        self.fail_text = fail_text
        self.fail_url = fail_url
        self.found_ref = found_ref
        self.found_testid_ref = found_testid_ref
        self.found_label_ref = found_label_ref
        self.found_role_button_ref = found_role_button_ref
        self.found_role_link_ref = found_role_link_ref
        self.visible = visible
        self.enabled = enabled
        self.console_entries = []
        self.page_error_entries = []
        self.network_entries = []
        self.scroll_ref_sequence = []

    def set_viewport(self, width, height):
        self.calls.append(("set_viewport", width, height))

    def wait_for_load_state(self, state, *, timeout):
        self.calls.append(("wait_for_load_state", state, timeout))
        if state in self.fail_load_states:
            raise RuntimeError(f"load wait failed: {state}")

    def wait_for_text(self, text, *, timeout):
        self.calls.append(("wait_for_text", text, timeout))
        if self.fail_text:
            raise RuntimeError("text wait failed")

    def wait_for_url(self, pattern, *, timeout):
        self.calls.append(("wait_for_url", pattern, timeout))
        if self.fail_url:
            raise RuntimeError("url wait failed")

    def wait(self, ms):
        self.calls.append(("wait", ms))

    def find_ref(self, intent):
        self.calls.append(("find_ref", intent))
        return self.found_ref

    def find_testid_ref(self, testid):
        self.calls.append(("find_testid_ref", testid))
        return self.found_testid_ref

    def find_label_ref(self, label):
        self.calls.append(("find_label_ref", label))
        return self.found_label_ref

    def find_role_ref(self, role, name):
        self.calls.append(("find_role_ref", role, name))
        if role == "button":
            return self.found_role_button_ref
        if role == "link":
            return self.found_role_link_ref
        return ""

    def scroll_into_view(self, target):
        self.calls.append(("scroll_into_view", target))

    def scroll(self, direction="down", px=700):
        self.calls.append(("scroll", direction, px))

    def is_visible(self, target):
        self.calls.append(("is_visible", target))
        return self.visible

    def is_enabled(self, target):
        self.calls.append(("is_enabled", target))
        return self.enabled

    def console_messages(self):
        self.calls.append(("console_messages",))
        return list(self.console_entries)

    def page_errors(self):
        self.calls.append(("page_errors",))
        return list(self.page_error_entries)

    def network_requests(self):
        self.calls.append(("network_requests",))
        return list(self.network_entries)


class StepRunnerPhase1Tests(unittest.TestCase):
    def test_configure_ab_session_sets_capture_viewport(self):
        cli = _FakeCLI()
        settings = CaptureSettings(viewport_width=1440, viewport_height=900)

        result = _configure_ab_session(cli, settings)

        self.assertEqual(
            cli.calls,
            [("set_viewport", 1440, 900)],
        )
        self.assertEqual(
            result,
            {"viewport_width": 1440, "viewport_height": 900},
        )

    def test_settle_ab_page_prefers_load_states_and_validation_wait(self):
        cli = _FakeCLI()

        result = _settle_ab_page(
            cli,
            validation_condition={"type": "text_present", "value": "Saved"},
        )

        self.assertEqual(
            cli.calls,
            [
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_load_state", "networkidle", 1),
                ("wait_for_text", "Saved", 8),
            ],
        )
        self.assertTrue(result["networkidle"])
        self.assertTrue(result["domcontentloaded"])
        self.assertEqual(result["validation_wait"], "text_present")
        self.assertFalse(result["fallback_wait_used"])

    def test_settle_ab_page_marks_fallback_when_networkidle_fails(self):
        cli = _FakeCLI(fail_load_states={"networkidle"})

        result = _settle_ab_page(cli)

        self.assertEqual(
            cli.calls,
            [
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_load_state", "networkidle", 1),
            ],
        )
        self.assertFalse(result["networkidle"])
        self.assertTrue(result["domcontentloaded"])
        self.assertTrue(result["fallback_wait_used"])

    def test_resolve_ab_click_target_prefers_testid_lookup_from_selector(self):
        cli = _FakeCLI(found_testid_ref="@e11")
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            selector="[data-testid='proceed-recharge']",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(
            cli.calls,
            [("find_testid_ref", "proceed-recharge")],
        )
        self.assertEqual(result["chosen_ref"], "@e11")
        self.assertEqual(result["selection_source"], "semantic_testid")

    def test_resolve_ab_click_target_prefers_role_lookup_before_snapshot_matching(self):
        cli = _FakeCLI(found_role_button_ref="@e55")
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(
            cli.calls,
            [("find_role_ref", "button", "Proceed Recharge")],
        )
        self.assertEqual(result["chosen_ref"], "@e55")
        self.assertEqual(result["selection_source"], "semantic_role")

    def test_resolve_ab_click_target_uses_semantic_find_after_command_lookups_miss(self):
        cli = _FakeCLI(found_ref="@e99")
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(result["chosen_ref"], "@e99")
        self.assertEqual(result["selection_reason"], "ab_find")
        self.assertEqual(result["selection_source"], "semantic_find")
        self.assertFalse(result["should_retry"])
        self.assertEqual(
            cli.calls,
            [
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
            ],
        )

    def test_resolve_ab_click_target_requests_scroll_retry_after_find_miss(self):
        cli = _FakeCLI(found_ref="")
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(result["chosen_ref"], "")
        self.assertTrue(result["scroll_retry_used"])
        self.assertTrue(result["should_retry"])

    def test_ensure_ab_target_actionable_checks_visibility_and_enabled_state(self):
        cli = _FakeCLI(visible=False, enabled=True)

        result = _ensure_ab_target_actionable(cli, "@e5")

        self.assertEqual(
            cli.calls,
            [
                ("scroll_into_view", "@e5"),
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_load_state", "networkidle", 1),
                ("is_visible", "@e5"),
                ("is_enabled", "@e5"),
            ],
        )
        self.assertEqual(
            result,
            {"target_visible": False, "target_enabled": True},
        )

    def test_recover_ab_prerequisite_steps_inserts_recovery_before_retry(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Recharge Now",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="state_unchanged",
                current_step_completed_unvalidated=False,
                state_changed=False,
            )

        regenerate.assert_called_once()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(result["next_intent"], "Proceed Recharge")
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")
        self.assertEqual(
            [step.get("label") for step in result["replacement_steps"]],
            ["₹2000", "Recharge Now"],
        )
        self.assertTrue(result["replacement_steps"][1]["_ab_recovery_attempted"])

    def test_recover_ab_prerequisite_steps_skips_when_next_target_exists(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch("app.execution.step_runner.regenerate_with_feedback") as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Recharge Now",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="state_unchanged",
                current_step_completed_unvalidated=False,
                state_changed=False,
            )

        regenerate.assert_not_called()
        self.assertFalse(result["recovered"])
        self.assertTrue(result["blocked_target_present"])

    def test_recover_ab_prerequisite_steps_uses_current_intent_for_selection_failed_current_step(self):
        steps = [
            {"action": "click", "label": "Proceed Recharge"},
            {"action": "assert_terminal", "expected_element": "done"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Proceed Recharge",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="selection_failed_current_step",
                current_step_completed_unvalidated=False,
                state_changed=None,
            )

        regenerate.assert_called_once()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")

    def test_recover_ab_prerequisite_steps_marks_unvalidated_context_for_next_step_miss(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=1,
                current_step=steps[1],
                current_intent="Proceed Recharge",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="selection_failed_after_unvalidated",
                current_step_completed_unvalidated=True,
                state_changed=None,
            )

        regenerate.assert_called_once()
        regenerate_args = regenerate.call_args.kwargs["error_context"]
        self.assertTrue(result["recovered"])
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")
        self.assertTrue(regenerate_args["current_step_completed_unvalidated"])
        self.assertEqual(regenerate_args["trigger_reason"], "selection_failed_after_unvalidated")

    def test_extract_contract_static_records_interaction_hints_from_diff(self):
        diff_files = [
            {
                "path": "src/app/recharge/page.tsx",
                "status": "modified",
                "patch": '\n'.join(
                    [
                        '+<button>Select amount</button>',
                        '+<div className="tabs">Plan Tabs</div>',
                        '+<button>Proceed Recharge</button>',
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertIn("interaction_hint_high:select amount", contract.extraction_notes)
        self.assertIn("interaction_hint_low:switch tab", contract.extraction_notes)

    def test_extract_contract_static_limits_required_targets_to_interactive_lines(self):
        diff_files = [
            {
                "path": "src/app/security/page.tsx",
                "status": "modified",
                "patch": '\n'.join(
                    [
                        '+<div>Security Check</div>',
                        '+<button>Continue</button>',
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertEqual([target.label for target in contract.targets], ["Continue"])

    def test_preflight_rejects_missing_prerequisite_step_from_contract_hint(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_high:select amount"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertFalse(result.passed)
        self.assertIn(
            "Missing prerequisite setup step implied by contract hint: 'select amount'",
            result.errors,
        )

    def test_preflight_warns_for_weak_interaction_hint_instead_of_blocking(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_low:choose option"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertTrue(result.passed)
        self.assertIn(
            "Weak prerequisite setup hint not covered explicitly: 'choose option'",
            result.warnings,
        )

    def test_sanitize_terminal_assertions_drops_ungrounded_terminal_when_contract_is_silent(self):
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Start security flow"},
            {"action": "assert_terminal", "expected_element": "new-feature"},
        ]

        result = _sanitize_terminal_assertions(
            steps,
            contract=types.SimpleNamespace(terminal=None),
            real_data_testids=[{"testid": "security-flow-modal"}],
            diff_text='{"path":"src/app/settings/page.tsx","patch":"+<button>Start security flow</button>"}',
        )

        self.assertEqual(
            [step.get("action") for step in result],
            ["goto", "click"],
        )

    def test_preflight_rejects_last_click_before_terminal_without_validation(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=[],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge"},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertFalse(result.passed)
        self.assertTrue(
            any(
                "validation" in err.lower() or "proof condition" in err.lower()
                for err in result.errors
            ),
            result.errors,
        )

    def test_should_keep_click_screenshots_only_for_success(self):
        self.assertTrue(_should_keep_click_screenshots({"outcome": "success"}))
        self.assertFalse(
            _should_keep_click_screenshots({"outcome": "unvalidated", "state_changed": True})
        )
        self.assertFalse(
            _should_keep_click_screenshots({"outcome": "unvalidated", "state_changed": False})
        )
        self.assertFalse(_should_keep_click_screenshots({"outcome": "click_failed"}))

    def test_scroll_to_find_uses_incremental_scroll_until_target_found(self):
        cli = _FakeCLI()
        call_count = {"find_ref": 0}

        def fake_find_ref(intent):
            cli.calls.append(("find_ref", intent))
            call_count["find_ref"] += 1
            return "@e42" if call_count["find_ref"] == 3 else ""

        cli.find_ref = fake_find_ref

        result = _scroll_to_find(cli, intent="Proceed Recharge")

        self.assertEqual(result, "@e42")
        self.assertEqual(
            cli.calls,
            [
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll", "down", 400),
                ("wait_for_load_state", "networkidle", 1),
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll", "down", 400),
                ("wait_for_load_state", "networkidle", 1),
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll_into_view", "@e42"),
            ],
        )

    def test_assert_terminal_infers_element_present_from_expected_element(self):
        cli = _FakeCLI(found_testid_ref="@e99")

        result = _assert_ab_terminal_condition(
            cli,
            condition={},
            expected_element="security-flow-modal",
            extract_snapshot=lambda **kwargs: {
                "interactive_elements": [],
                "context_elements": [],
                "snapshot_text": "",
            },
        )

        self.assertTrue(result["found"])
        self.assertIn(result["source"], {"find_testid", "wait_for_element_present"})
        self.assertIn(result["actual"], {"@e99", "security-flow-modal"})
        self.assertIn(("find_testid_ref", "security-flow-modal"), cli.calls)

    def test_discard_step_screenshots_removes_files_and_clears_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.png"
            after_path = Path(tmpdir) / "after.png"
            before_path.write_text("before", encoding="utf-8")
            after_path.write_text("after", encoding="utf-8")
            step_result = {
                "before_screenshot": str(before_path),
                "after_screenshot": str(after_path),
            }

            _discard_step_screenshots(step_result)

            self.assertFalse(before_path.exists())
            self.assertFalse(after_path.exists())
            self.assertEqual(step_result["before_screenshot"], "")
            self.assertEqual(step_result["after_screenshot"], "")

    def test_collect_ab_failure_diagnostics_counts_console_errors_and_network(self):
        cli = _FakeCLI()
        cli.console_entries = ["warn one", "warn two"]
        cli.page_error_entries = ["TypeError"]
        cli.network_entries = [
            {"url": "/api/a", "status": 200},
            {"url": "/api/b", "status": 500},
            {"url": "/api/c", "error": "timeout"},
        ]

        diagnostics = _collect_ab_failure_diagnostics(cli)

        self.assertEqual(
            diagnostics,
            {
                "console_messages": ["warn one", "warn two"],
                "page_errors": ["TypeError"],
                "network_request_count": 3,
                "network_error_count": 2,
                "network_requests_preview": cli.network_entries,
            },
        )


if __name__ == "__main__":
    unittest.main()
