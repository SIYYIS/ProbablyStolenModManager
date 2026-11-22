from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class TestDesktopEntrypoint(unittest.TestCase):
    def test_no_subcommand_opens_the_desktop_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            with (
                patch.object(sys, "argv", ["main.py", "--data-dir", str(data_dir)]),
                patch("webui.desktop.run_desktop") as run_desktop,
                patch("webui.desktop.show_start_error") as show_start_error,
            ):
                self.assertEqual(main.main(), 0)

        run_desktop.assert_called_once_with(data_dir.resolve())
        show_start_error.assert_not_called()

    def test_desktop_start_failure_is_reported(self) -> None:
        failure = RuntimeError("startup failed")
        with (
            patch.object(sys, "argv", ["main.py"]),
            patch("main._default_data_dir", return_value=Path("manager_data")),
            patch("webui.desktop.run_desktop", side_effect=failure),
            patch("webui.desktop.show_start_error") as show_start_error,
        ):
            self.assertEqual(main.main(), 1)

        show_start_error.assert_called_once_with(failure)


if __name__ == "__main__":
    unittest.main()
