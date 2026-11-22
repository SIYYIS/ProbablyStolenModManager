"""Run snapshot interaction tests only against the disposable workbench fixture."""

from contextlib import ExitStack
from unittest.mock import patch

from webui_fixture import main


if __name__ == "__main__":
    # The fixture uses a private temporary game/save/profile directory and never launches the game.
    with ExitStack() as stack:
        for module in ("core.es3_manager", "core.save_slots", "core.snapshot_history",
                       "webui.slot_routes", "webui.server"):
            stack.enter_context(patch(f"{module}.is_game_running", return_value=False))
        main()
