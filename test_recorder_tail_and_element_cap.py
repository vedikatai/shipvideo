"""Tests for closing-frame approval and interactive element pagination/batching."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.execution.step_runner import _approved_frame_paths
from app.llm import step_generator as sg
from app.render import LAST_FRAME_PAD_S, _frame_hold_seconds
from app.steps.step_generation import _paginate_entries, _route_snapshot_catalog


class ApprovedFrameTailTests(unittest.TestCase):
    def _touch(self, d: Path, name: str) -> str:
        p = d / name
        p.write_bytes(b"x")
        return str(p)

    def test_closing_terminal_frame_kept(self):
        d = Path(tempfile.mkdtemp())
        click_shot = self._touch(d, "after_click.png")
        terminal_shot = self._touch(d, "terminal.png")
        results = [
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "click", "label": "Save"},
                "after_screenshot": click_shot,
            },
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "assert_terminal"},
                "terminal_condition_reached": True,
                "screenshot_path": terminal_shot,
            },
        ]
        frames = _approved_frame_paths(results)
        self.assertTrue(frames)
        self.assertEqual(frames[-1], terminal_shot)

    def test_screenshot_after_terminal_kept(self):
        d = Path(tempfile.mkdtemp())
        term = self._touch(d, "t.png")
        closing = self._touch(d, "close.png")
        results = [
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "assert_terminal"},
                "terminal_condition_reached": True,
                "screenshot_path": term,
            },
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "screenshot"},
                "screenshot_path": closing,
            },
        ]
        frames = _approved_frame_paths(results)
        self.assertIn(closing, frames)
        self.assertEqual(frames[-1], closing)

    def test_last_frame_hold_padded(self):
        self.assertGreater(_frame_hold_seconds(2, 3), _frame_hold_seconds(0, 3))
        self.assertAlmostEqual(
            _frame_hold_seconds(2, 3) - _frame_hold_seconds(0, 3),
            LAST_FRAME_PAD_S,
            places=5,
        )


class ElementCapTests(unittest.TestCase):
    def test_route_catalog_not_hard_capped_at_20(self):
        buttons = [{"text": f"B{i}", "selector": f"#b{i}"} for i in range(70)]
        links = [{"text": f"L{i}", "href": f"/{i}"} for i in range(10)]
        dom = {"route_snapshots": {"/": {"buttons": buttons, "links": links, "data_testids": []}}}
        cat = _route_snapshot_catalog(dom, fallback_routes=["/"])
        entry = cat["/"]
        self.assertEqual(entry["interactive_total"], 80)
        self.assertGreaterEqual(entry["interactive_page_count"], 2)
        self.assertEqual(len(entry["buttons"]), 70)
        self.assertEqual(len(entry["links"]), 10)

    def test_paginate_entries(self):
        pages = _paginate_entries(list(range(90)), page_size=40)
        self.assertEqual(len(pages), 3)
        self.assertEqual(len(pages[0]), 40)
        self.assertEqual(len(pages[-1]), 10)

    def test_find_ref_batches_over_60(self):
        elements = [
            {"ref": f"@e{i}", "role": "button", "name": f"Item {i}"}
            for i in range(75)
        ]
        elements[70]["name"] = "UniqueSaveTarget"
        calls = {"n": 0}

        def fake_simple(prompt: str) -> str:
            calls["n"] += 1
            if "UniqueSaveTarget" in prompt:
                return "@e70"
            return "none"

        with patch.object(sg, "_call_llm_simple", side_effect=fake_simple):
            ref = asyncio.run(
                sg.find_ref_with_llm(
                    intent="click UniqueSaveTarget",
                    interactive_elements=elements,
                )
            )
        self.assertEqual(ref, "@e70")
        # Must have scanned more than one batch (75 / 40 => 2+)
        self.assertGreaterEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
