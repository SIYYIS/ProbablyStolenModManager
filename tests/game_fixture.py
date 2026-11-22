from __future__ import annotations

from pathlib import Path

from core.paths import GamePaths
from core.settings import AppSettings


def make_game_fixture(base: Path) -> GamePaths:
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
    (loot / "fixture.json").write_text('{"patches": [{"table": "fixtureLoot"}]}\n', encoding="utf-8")
    (configs / "alpha.json").write_text('{"enabled": true, "multiplier": 2}', encoding="utf-8")
    save_file = save / "SaveFile.es3"
    save_file.write_text('{"modOrder": ["alpha", "beta"], "volume": 2}', encoding="utf-8")
    AppSettings().save(base / "manager" / "settings.json")
    return GamePaths(
        game_dir=game,
        local_mods_dir=mods,
        workshop_dir=workshop,
        save_data_dir=save,
        save_file=save_file,
        mod_configs_dir=configs,
    )
