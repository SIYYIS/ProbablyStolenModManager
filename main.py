"""Entry point for the desktop workbench and command-line tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.es3_manager import ProfileManager, inspect_save_file
from core.game_review import read_game_review
from core.paths import discover_paths, launch_game
from core.scanner import scan_installed_mods, topological_sort_mods, validate_active_mods
from core.settings import AppSettings
from core.snapshot_history import SnapshotError, SnapshotHistory
from core.test_session import active_session


def _default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "manager_data"
    return Path(__file__).resolve().parent / "manager_data"


def run_cli(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir()
    settings = AppSettings.load(data_dir / "settings.json")
    paths = discover_paths(
        custom_game_dir=args.game_dir or settings.game_dir or None,
        custom_workshop_dir=args.workshop_dir or settings.workshop_dir or None,
        custom_save_dir=args.save_dir or settings.save_dir or None,
    )
    mgr = ProfileManager(data_dir)

    if args.command in {'save-profile', 'apply-profile', 'sort-active'} and active_session():
        print('请先在桌面工作台中结束排错会话并恢复正式存档。')
        return 1

    if args.command == "scan":
        mods = scan_installed_mods(paths)
        state = inspect_save_file(paths.save_file)
        issues = validate_active_mods(state.active_mods, mods, settings.game_version or read_game_review(paths).version)
        print(
            json.dumps(
                {
                    "installed_count": len(mods),
                    "active_mods": state.active_mods,
                    "issues": [{"severity": i.severity, "msg": i.message} for i in issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "save-profile":
        ok, msg = mgr.save_profile(args.name, paths, save_note=args.note or "")
        print(msg)
        return 0 if ok else 1

    if args.command == "apply-profile":
        try:
            SnapshotHistory(data_dir, mgr, paths).backup_link(args.name)
        except (SnapshotError, OSError) as exc:
            print(f'关联档位备份失败，未切换预设：{exc}')
            return 1
        ok, msg = mgr.apply_profile(args.name, paths, allow_full_restore=args.full_restore)
        print(msg)
        if ok and args.launch:
            ok, lmsg = launch_game(paths.game_dir)
            print(lmsg)
        return 0 if ok else 1

    if args.command == "sort-active":
        mods = scan_installed_mods(paths)
        state = inspect_save_file(paths.save_file)
        sorted_ids, warns = topological_sort_mods(state.active_mods, mods)
        ok, msg = mgr.update_live_mod_order(paths, sorted_ids)
        print(msg)
        for w in warns:
            print("WARNING:", w)
        return 0 if ok else 1

    if args.command == "list-profiles":
        for p in mgr.list_profiles():
            print(f"- {p.name} ({len(p.active_mods)} mods) | updated={p.updated_at} | note={p.save_note}")
        return 0

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probably Stolen Mod Manager")
    parser.add_argument("--data-dir", help="Custom manager_data directory")
    parser.add_argument("--game-dir", help="Override Probably Stolen game folder")
    parser.add_argument("--workshop-dir", help="Override Steam workshop 4348910 folder")
    parser.add_argument("--save-dir", help="Override LocalLow save folder")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scan", help="Scan installed mods & validate active load order")
    sub.add_parser("list-profiles", help="List saved mod profiles")
    sub.add_parser("sort-active", help="Topologically sort active mods by prerequisites")

    p_save = sub.add_parser("save-profile", help="Snapshot current SaveFile.es3 as a profile")
    p_save.add_argument("name", help="Profile name")
    p_save.add_argument("--note", help="Optional save-slot note")

    p_apply = sub.add_parser("apply-profile", help="Apply a saved profile")
    p_apply.add_argument("name", help="Profile name")
    p_apply.add_argument("--launch", action="store_true", help="Launch game after switching")
    p_apply.add_argument(
        "--full-restore",
        action="store_true",
        help="Allow a full-file profile to replace the complete SaveFile.es3",
    )

    args = parser.parse_args()
    if args.command:
        return run_cli(args)

    data_dir = Path(args.data_dir).resolve() if args.data_dir else _default_data_dir()
    from webui.desktop import run_desktop, show_start_error

    try:
        run_desktop(data_dir)
    except Exception as exc:
        show_start_error(exc)
        return 1
    return 0


if __name__ == "__main__":
    # The Windows desktop build has no console streams; source CLI retains them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    raise SystemExit(main())
