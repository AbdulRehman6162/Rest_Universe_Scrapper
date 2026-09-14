import asyncio
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch
import local_runner
from collector_core import load_core


class LocalRunnerTests(unittest.TestCase):
    def test_help_needs_no_auth(self):
        result = subprocess.run([sys.executable, str(local_runner.ROOT / "local_runner.py"), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--write", result.stdout)

    def test_preview_and_visible_browser_defaults(self):
        args = local_runner.parse_args([])
        self.assertFalse(args.write)
        self.assertFalse(args.headless)

    def test_load_core_does_not_authenticate_or_run_cells(self):
        with patch("gspread.oauth", side_effect=AssertionError("unexpected auth")), \
             patch("gspread.service_account", side_effect=AssertionError("unexpected auth")), \
             contextlib.redirect_stdout(io.StringIO()) as captured:
            core = load_core()
        self.assertEqual(captured.getvalue(), "")
        self.assertFalse(hasattr(core, "spreadsheet"))
        self.assertFalse(hasattr(core, "checkpoint"))
        self.assertTrue(callable(core.extract_place_details))
        self.assertTrue(callable(core.save_search_debug))
        self.assertEqual(core.SEARCH_READY_TIMEOUT_MS, 45000)

    def test_paths_resolve_from_repo(self):
        self.assertEqual(local_runner.local_path("output"), local_runner.ROOT / "output")

    def test_configuration_uses_local_paths(self):
        core = load_core()
        args = local_runner.parse_args(["--mode", "search", "--max-queries", "0", "--run-id", "test"])
        with tempfile.TemporaryDirectory() as folder:
            local_runner.configure_core(core, args, types.SimpleNamespace(id="sheet"), Path(folder))
            self.assertEqual(core.CHECKPOINT_JSON, str(Path(folder) / "checkpoint.json"))
            self.assertIsNone(core.MAX_SEARCH_QUERIES)
            self.assertFalse(core.RUN_REFRESH)
            self.assertTrue(core.RUN_SEARCH)
            self.assertFalse(core.HEADLESS)

    def test_invalid_options(self):
        for args in [["--max-results", "0"], ["--max-refresh", "-1"], ["--check", "--write"],
                     ["--query", "cafes", "--mode", "refresh"]]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                local_runner.parse_args(args)

    def test_identity_rules_are_preserved(self):
        core = load_core()
        self.assertIsNone(core.locate_record_row(
            {"place_id": "ChIJnew", "phone": "111"},
            {"place_id": {"ChIJold": {2}}, "maps": {}, "phone": {"111": {2}}, "name_address": {}},
        ))

    def test_browser_closes_after_failure(self):
        browser = types.SimpleNamespace(new_context=AsyncMock(return_value=object()), close=AsyncMock())
        chromium = types.SimpleNamespace(launch=AsyncMock(return_value=browser))
        class Manager:
            async def __aenter__(self): return types.SimpleNamespace(chromium=chromium)
            async def __aexit__(self, *args): pass
        core = types.SimpleNamespace(
            async_playwright=lambda: Manager(), HEADLESS=False, VIEWPORT={"width": 100, "height": 100},
            run_refresh=AsyncMock(return_value=[]), run_search=AsyncMock(side_effect=RuntimeError("failed")),
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(local_runner.collect_local(core))
        browser.close.assert_awaited_once()
        chromium.launch.assert_awaited_once_with(headless=False)


if __name__ == "__main__":
    unittest.main()
