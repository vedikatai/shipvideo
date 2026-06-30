from __future__ import annotations
import subprocess
import unittest
from unittest.mock import patch
from app.steps.step_normalizer import _extract_routes_from_diff
from app.render import run_ffmpeg_with_retry

class Issues45Tests(unittest.TestCase):
    def test_deleted_not_in_routes(self):
        routes = _extract_routes_from_diff([
            {"path": "src/app/pricing/page.tsx", "status": "removed", "patch": "-x"},
            {"path": "src/app/demo/page.tsx", "status": "modified", "patch": "+y"},
        ])
        self.assertNotIn("/pricing", routes)
        self.assertIn("/demo", routes)

    def test_ffmpeg_retries(self):
        fail = subprocess.CompletedProcess(args=["ffmpeg"], returncode=1, stdout="", stderr="e")
        ok = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
        with patch("app.render.subprocess.run", side_effect=[fail, ok]):
            r = run_ffmpeg_with_retry(["ffmpeg", "-y"], timeout=5, max_attempts=3)
        self.assertEqual(r.returncode, 0)
