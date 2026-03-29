import unittest
import sys
import types
from unittest.mock import patch

playwright_module = types.ModuleType("playwright")
playwright_sync_api = types.ModuleType("playwright.sync_api")
playwright_sync_api.Page = object
playwright_sync_api.sync_playwright = lambda: None
playwright_module.sync_api = playwright_sync_api
sys.modules.setdefault("playwright", playwright_module)
sys.modules.setdefault("playwright.sync_api", playwright_sync_api)

observability_module = types.ModuleType("observability")
observability_module.record_agent_browser_diagnostics = lambda **kwargs: None
sys.modules.setdefault("observability", observability_module)

from app.config_types import CaptureSettings
from app.execution.step_runner import (
    _collect_ab_failure_diagnostics,
    _recover_ab_prerequisite_steps,
    _ensure_ab_target_actionable,
    _configure_ab_session,
    _resolve_ab_click_target,
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
        visible=True,
        enabled=True,
    ):
        self.calls = []
        self.fail_load_states = set(fail_load_states or [])
        self.fail_text = fail_text
        self.fail_url = fail_url
        self.found_ref = found_ref
        self.visible = visible
        self.enabled = enabled
        self.console_entries = []
        self.page_error_entries = []
        self.network_entries = []

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

    def scroll_into_view(self, target):
        self.calls.append(("scroll_into_view", target))

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
                ("wait_for_load_state", "networkidle", 8),
                ("wait_for_text", "Saved", 8),
            ],
        )
        self.assertTrue(result["domcontentloaded"])
        self.assertTrue(result["networkidle"])
        self.assertEqual(result["validation_wait"], "text_present")
        self.assertFalse(result["fallback_wait_used"])

    def test_settle_ab_page_falls_back_to_fixed_wait_when_load_checks_fail(self):
        cli = _FakeCLI(fail_load_states={"domcontentloaded", "networkidle"})

        result = _settle_ab_page(cli)

        self.assertEqual(
            cli.calls,
            [
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_load_state", "networkidle", 8),
                ("wait", 1500),
            ],
        )
        self.assertFalse(result["domcontentloaded"])
        self.assertFalse(result["networkidle"])
        self.assertTrue(result["fallback_wait_used"])

    def test_resolve_ab_click_target_uses_semantic_find_after_no_match(self):
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
                ("wait_for_load_state", "networkidle", 8),
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
            )

        regenerate.assert_called_once()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(result["next_intent"], "Proceed Recharge")
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
            )

        regenerate.assert_not_called()
        self.assertFalse(result["recovered"])
        self.assertTrue(result["next_target_present"])

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
