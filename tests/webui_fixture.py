"""Run the WebUI on loopback with disposable files and stubbed Steam reads."""

import argparse
import base64
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.es3_manager import ProfileManager
from core.paths import GAME_EXE_NAME
from core.settings import AppSettings
from core.workshop import WorkshopItem, WorkshopPage
from test_ui_workbench import _make_fixture
from webui.server import run


def add_mod_fixtures(paths):
    for mod_id, title, prerequisite in [('fixture.wizard', '向导测试模组', 'fixture.middle'),
                                         ('fixture.middle', '向导中间前置', 'fixture.tail'),
                                         ('fixture.tail', '向导底层前置', '')]:
        folder = paths.local_mods_dir / mod_id
        folder.mkdir()
        dependencies = f'<Prerequisites><Mod>{prerequisite}</Mod></Prerequisites>' if prerequisite else ''
        (folder / 'manifest.xml').write_text(f'<Manifest><ID>{mod_id}</ID><Name>{title}</Name>{dependencies}</Manifest>', encoding='utf-8')
    workshop = paths.workshop_dir / "180019999"
    workshop.mkdir()
    (workshop / "manifest.xml").write_text(
        "<Manifest><ID>fixture.dependent</ID><Name>测试工坊扩展</Name>"
        "<Description>测试模组简介</Description><Prerequisites><Mod>fixture.base</Mod>"
        "<Mod>fixture.missing</Mod></Prerequisites><ModVersion>0.19.4</ModVersion>"
        "<GameVersions><Version>0.46D</Version><Version>1.0.0</Version></GameVersions>"
        "<Capabilities><Capability>files</Capability><Capability>steam</Capability></Capabilities></Manifest>", encoding="utf-8",
    )
    base = paths.local_mods_dir / "unrelated_folder_name"
    base.mkdir()
    (base / "manifest.xml").write_text("<Manifest><ID>fixture.base</ID><Name>本机前置框架</Name></Manifest>", encoding="utf-8")
    downloaded = paths.local_mods_dir / "ws_180019998"
    downloaded.mkdir()
    (downloaded / "manifest.xml").write_text(
        "<Manifest><ID>fixture.downloaded</ID><Name>测试下载副本</Name></Manifest>", encoding="utf-8")
    broken = paths.local_mods_dir / "fixture.broken"
    broken.mkdir()
    (broken / "manifest.xml").write_text("<Manifest><ID>", encoding="utf-8")
    for folder in (workshop, base):
        textures = folder / "Textures" / "Items"
        textures.mkdir(parents=True)
        for name in ("overlap_a", "overlap_b", "contents_differ"):
            (textures / f"{name}.png").write_bytes(name.encode() if name != "contents_differ" else folder.name.encode())
    (workshop / "cover.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg=="
    ))
    (workshop / "preview.png").write_bytes((workshop / "cover.png").read_bytes())
    (workshop / "Textures" / "Items" / "fixture-sprite.png").write_bytes((workshop / "cover.png").read_bytes())
    (workshop / "README.md").write_text("测试作者指南：仅用于临时测试。", encoding="utf-8")
    (paths.mod_configs_dir / "fixture.dependent.json").write_text(json.dumps({"entries": [
        {"key": "budget", "value": 50, "displayName": "测试预算", "description": "模拟预算上限。"},
        {"key": "opaque", "value": True},
    ]}, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8873)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="psmm-web-demo-") as folder:
        base = Path(folder)
        paths = _make_fixture(base)
        add_mod_fixtures(paths)
        (paths.game_dir / GAME_EXE_NAME).touch()
        (paths.save_data_dir / "save_0.es3").write_bytes(
            b'{"playerStore":{"__type":"PlayerStore,Assembly-CSharp","value":'
            b'{"playerCash":22,"wildFavor":365,"saveSlotId":0,"rentValue":468,"garbageLevel":0,'
            b'"tutorialEnable":false,"odinBudget":861,"healthData":{"luck":-5,"isWoundStable":true}}},'
            b'"storeStation":{"__type":"StoreStation,Assembly-CSharp","value":{"dayCounter":106,'
            b'"storeOperationManager":{"secPower":100}}}}'
        )
        settings = AppSettings(game_dir=str(paths.game_dir), save_dir=str(paths.save_data_dir),
                               workshop_dir=str(paths.workshop_dir))
        settings.save(base / "manager" / "settings.json")
        with patch("core.es3_manager.is_game_running", return_value=False):
            ok, message = ProfileManager(base / "manager").save_profile("Fixture profile", paths)
            assert ok, message
        managed = paths.game_dir / 'Probably Stolen_Data' / 'Managed'
        (paths.save_data_dir / 'Player.log').write_text(
            f"Mono path[0] = '{managed.as_posix()}'\n[Example] NullReferenceException: fixture\n"
            "[ModLoader] audit: game 0.46D, trust list: fixture\n"
            "[ModLoader] audit: fixture.dependent v0.19.4 source=Workshop workshop=180019999 owner=0 "
            "running=True trust=Match caps=declared files, steam hash=fixture\n", encoding='utf-8')
        items = [WorkshopItem(str(180010001 + i), name, description=description, tags=tags,
                              subscriptions=subscriptions, file_size=1048576)
                 for i, (name, description, tags, subscriptions) in enumerate([
                     ("Warehouse Assistant", "Adds inventory filters and sorting rules.", ["Quality of Life"], 3274),
                     ("Night Shift Expansion", "Adds new customers and nighttime events.", ["Gameplay"], 1481),
                     ("Rare Items Index", "Lists rare items and unlock hints.", ["Items"], 732),
                 ])]
        with patch("webui.server.browse_workshop_public_page", return_value=WorkshopPage(items, len(items), False)), \
                patch("webui.server.fetch_workshop_details_by_ids", return_value=items[:1]), \
                patch("core.workshop_versions.fetch_workshop_details_by_ids", return_value=[
                    WorkshopItem("180019999", "测试工坊扩展", time_updated=1700000050),
                ]), \
                patch("webui.server.open_workshop_page_in_steam"), \
                patch("webui.management_routes.open_workshop_page_in_steam"), \
                patch("webui.management_routes.os.startfile", side_effect=AssertionError("Fixture must not open the Windows shell")), \
                patch("webui.operations.launch_game", side_effect=AssertionError("Fixture must not launch Steam")), \
                patch("core.es3_manager.is_game_running", return_value=False), \
                patch("core.save_slots.is_game_running", return_value=False), \
                patch("core.snapshot_history.is_game_running", return_value=False), \
                patch("webui.slot_routes.is_game_running", return_value=False), \
                patch("webui.operations.is_game_running", return_value=False), \
                patch("webui.server.is_game_running", return_value=False), \
                patch("core.test_session.default_save_data_dir", return_value=paths.save_data_dir), \
                patch("core.test_session.launch_game", return_value=(False, "演示环境不会启动游戏。")), \
                patch("webui.server.launch_game", return_value=(False, "演示环境不会启动游戏。")):
            run(base / "manager", port=args.port, open_browser=False)


if __name__ == "__main__":
    main()
