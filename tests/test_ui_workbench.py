"""Tk integration tests with isolated game data and a timeout per case."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.es3_manager import inspect_save_file
from core.paths import GamePaths
from core.settings import AppSettings
from core.workshop import WorkshopItem

_TK_CHILD = __name__ == "__main__" and sys.argv[1:2] == ["--tk-child"]
_TK_UNAVAILABLE = 77
_TIMEOUT_SECONDS = 10
_RAW_CONFIG = '{"patches": [{"table": "fixtureLoot"}]}\n'


def _make_fixture(base: Path) -> GamePaths:
    game = base / "Game"
    mods = game / "Mods"
    workshop = base / "Workshop"
    workshop.mkdir()
    save = base / "Save"
    configs = save / "ModConfigs"
    configs.mkdir(parents=True)
    for mod_id, name in (("alpha", "Alpha Config"), ("beta", "Beta Tools"), ("unused", "Spare Module")):
        folder = mods / mod_id
        folder.mkdir(parents=True)
        (folder / "manifest.xml").write_text(
            f"<Manifest><ID>{mod_id}</ID><Name>{name}</Name></Manifest>", encoding="utf-8"
        )
    loot = mods / "alpha" / "LootTables"
    loot.mkdir()
    (loot / "fixture.json").write_bytes(_RAW_CONFIG.encode("utf-8"))
    (configs / "alpha.json").write_text('{"enabled": true, "multiplier": 2}', encoding="utf-8")
    save_file = save / "SaveFile.es3"
    save_file.write_text('{"modOrder": ["alpha", "beta"], "volume": 2}', encoding="utf-8")
    AppSettings().save(base / "manager" / "settings.json")
    return GamePaths(
        game_dir=game, local_mods_dir=mods, workshop_dir=workshop,
        save_data_dir=save, save_file=save_file, mod_configs_dir=configs,
    )


def _widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from _widgets(child)


class TestUIWorkbench(unittest.TestCase):
    def _run_child(self) -> None:
        # Tk may wedge in native event dispatch; a parent process owns the deadline and fixtures.
        method = self.id().rsplit(".", 1)[-1]
        with tempfile.TemporaryDirectory(prefix="psmm-ui-") as tmp:
            try:
                result = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--tk-child", method, tmp],
                    cwd=ROOT, capture_output=True, encoding="utf-8", timeout=_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                self.fail(f"Tk case {method} did not finish within {_TIMEOUT_SECONDS}s")
        if result.returncode == _TK_UNAVAILABLE:
            self.skipTest("Tk or a windowing display is unavailable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def setUp(self) -> None:
        if not _TK_CHILD:
            return
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError as exc:
            self.skipTest(str(exc))

        from ui import app as desktop

        desktop._enable_windows_dpi_awareness()
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        self.ttk = ttk
        self.callback_errors: list[str] = []
        root.report_callback_exception = (
            lambda *exc: self.callback_errors.append("".join(traceback.format_exception(*exc)))
        )

        base = Path(sys.argv[3])
        self.paths = _make_fixture(base)
        errors = patch.object(desktop.messagebox, "showerror", side_effect=AssertionError("Unexpected Tk error dialog"))
        self.addCleanup(errors.stop)
        errors.start()
        with patch.object(desktop.tk, "Tk", return_value=root), patch.object(desktop, "discover_paths", return_value=self.paths):
            self.app = desktop.ModManagerApp(base / "manager")
        self._pump()

    def _pump(self) -> None:
        self.app.root.update()
        self.assertFalse(self.callback_errors, "\n".join(self.callback_errors))

    def test_local_selection_search_and_saved_load_order(self) -> None:
        if not _TK_CHILD:
            return self._run_child()
        app = self.app
        active, inactive = app.active_tree, app.inactive_tree
        up, down, disable = app.order_buttons
        search = app.local_search_entry
        self.assertEqual(active.get_children(), ("alpha", "beta"))
        self.assertEqual(inactive.get_children(), ("unused",))

        inactive.selection_set("unused")
        self._pump()
        self.assertIn("Spare Module", app.mod_detail_text.get("1.0", "end-1c"))
        self.assertTrue(all(button.instate(["disabled"]) for button in (up, down, disable)))

        active.selection_set("beta")
        self._pump()
        self.assertFalse(inactive.selection())
        self.assertIn("Beta Tools", app.mod_detail_text.get("1.0", "end-1c"))
        search.insert(0, "TOOLS")
        self._pump()
        self.assertEqual(active.get_children(), ("beta",))
        self.assertEqual(active.selection(), ("beta",))
        self.assertFalse(inactive.get_children())
        search.delete(0, "end")
        search.insert(0, "no matching mod")
        self._pump()
        self.assertFalse(active.get_children())
        self.assertNotIn("Beta Tools", app.mod_detail_text.get("1.0", "end-1c"))
        self.assertTrue(all(button.instate(["disabled"]) for button in (up, down, disable)))

        search.delete(0, "end")
        active.selection_set("beta")
        self._pump()
        self.assertTrue(up.instate(["!disabled"]))
        self.assertTrue(down.instate(["disabled"]))
        up.invoke()
        self._pump()
        self.assertEqual(active.get_children(), ("beta", "alpha"))
        self.assertEqual(active.selection(), ("beta",))
        self.assertTrue(app.order_state_var.get())
        self.assertEqual(inspect_save_file(self.paths.save_file).active_mods, ["alpha", "beta"])

        save_button = next(
            widget for widget in _widgets(app.tab_local)
            if isinstance(widget, self.ttk.Button) and widget.cget("text") == "保存加载顺序"
        )
        with patch("core.es3_manager.is_game_running", return_value=False), patch("ui.app.messagebox.showinfo"):
            save_button.invoke()
        self._pump()
        state = inspect_save_file(self.paths.save_file)
        self.assertEqual(state.active_mods, ["beta", "alpha"])
        self.assertEqual(state.raw_data["volume"], 2)
        self.assertFalse(app.order_state_var.get())

    def test_config_switches_between_parameter_form_and_raw_json(self) -> None:
        if not _TK_CHILD:
            return self._run_child()
        app = self.app
        app.page_navigation["config"][1].invoke()
        self._pump()
        self.assertFalse(app.cfg_form_frame.winfo_ismapped())
        self.assertFalse(app.cfg_raw_text.winfo_ismapped())
        self.assertEqual(app.cfg_mod_list.size(), 1)
        self.assertIn("Alpha Config", app.cfg_mod_list.get(0))

        app.cfg_mod_list.selection_set(0)
        app.cfg_mod_list.event_generate("<<ListboxSelect>>")
        self._pump()
        self.assertTrue(app.cfg_form_frame.winfo_ismapped())
        self.assertFalse(app.cfg_raw_text.winfo_ismapped())
        fields = app.cfg_form_frame.winfo_children()
        self.assertEqual([field.get() for field in fields if isinstance(field, self.ttk.Entry)], ["2"])
        checkboxes = [field for field in fields if isinstance(field, self.ttk.Checkbutton)]
        self.assertEqual(len(checkboxes), 1)
        self.assertTrue(checkboxes[0].instate(["selected"]))

        app.cfg_file_combo.current(1)
        app.cfg_file_combo.event_generate("<<ComboboxSelected>>")
        self._pump()
        self.assertFalse(app.cfg_form_frame.winfo_ismapped())
        self.assertTrue(app.cfg_raw_text.winfo_ismapped())
        self.assertEqual(app.cfg_raw_text.get("1.0", "end-1c"), _RAW_CONFIG)
        self.assertFalse(app.cfg_raw_text.edit_modified())

        app.cfg_file_combo.current(0)
        app.cfg_file_combo.event_generate("<<ComboboxSelected>>")
        self._pump()
        self.assertTrue(app.cfg_form_frame.winfo_ismapped())
        self.assertFalse(app.cfg_raw_text.winfo_ismapped())
        config_file = self.paths.mod_configs_dir / "alpha.json"
        self.assertEqual(json.loads(config_file.read_text(encoding="utf-8")), {"enabled": True, "multiplier": 2})

    def test_workshop_refresh_clears_selection_and_switches_empty_state(self) -> None:
        if not _TK_CHILD:
            return self._run_child()
        app = self.app
        app.page_navigation["workshop"][1].invoke()
        self._pump()
        tree, detail = app.ws_tree, app.ws_detail_text
        self.assertTrue(app.ws_results_empty.winfo_ismapped())
        self.assertTrue(all(button.instate(["disabled"]) for button in app.ws_item_buttons))

        # Inject a completed search response, without Steam or translation requests.
        item = WorkshopItem("12345", "Workshop Fixture", description="Fixture description", subscriptions=17)
        app._populate_workshop_tree([item])
        self._pump()
        (row,) = tree.get_children()
        self.assertEqual(tree.set(row, "title"), "Workshop Fixture")
        self.assertEqual(str(tree.set(row, "subs")), "17")
        self.assertFalse(app.ws_results_empty.winfo_ismapped())
        self.assertTrue(all(button.instate(["disabled"]) for button in app.ws_item_buttons))

        tree.selection_set(row)
        self._pump()
        self.assertEqual(detail.get("1.0", "1.end"), "Workshop Fixture")
        self.assertIn("Fixture description", detail.get("1.0", "end-1c"))
        self.assertEqual(detail.cget("state"), "disabled")
        self.assertTrue(all(button.instate(["!disabled"]) for button in app.ws_item_buttons))

        app._populate_workshop_tree([WorkshopItem("67890", "Replacement Fixture")])
        self._pump()
        self.assertFalse(tree.selection())
        self.assertNotIn("Fixture description", detail.get("1.0", "end-1c"))
        self.assertTrue(all(button.instate(["disabled"]) for button in app.ws_item_buttons))

        tree.selection_set(tree.get_children()[0])
        self._pump()
        self.assertEqual(detail.get("1.0", "1.end"), "Replacement Fixture")
        app._populate_workshop_tree([])
        self._pump()
        self.assertFalse(tree.get_children())
        self.assertTrue(app.ws_results_empty.winfo_ismapped())
        self.assertTrue(app.ws_result_count_var.get().startswith("0"))
        self.assertNotIn("Replacement Fixture", detail.get("1.0", "end-1c"))
        self.assertTrue(all(button.instate(["disabled"]) for button in app.ws_item_buttons))


if __name__ == "__main__":
    if _TK_CHILD:
        suite = unittest.defaultTestLoader.loadTestsFromName(f"TestUIWorkbench.{sys.argv[2]}", sys.modules[__name__])
        result = unittest.TextTestRunner().run(suite)
        sys.exit(_TK_UNAVAILABLE if result.skipped and result.wasSuccessful() else int(not result.wasSuccessful()))
    unittest.main()
