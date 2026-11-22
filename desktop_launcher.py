"""Entry point for the standalone Windows desktop workbench."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probably Stolen Mod Manager Desktop")
    parser.add_argument("--data-dir", type=Path, help="Custom manager_data directory")
    parser.add_argument("--apply-profile", help="Apply a named mod profile")
    parser.add_argument("--launch", action="store_true", help="Launch after applying the profile")
    args = parser.parse_args()
    root = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent

    from webui.desktop import DesktopStartError, run_desktop, show_start_error

    data = args.data_dir.resolve() if args.data_dir else root / "manager_data"
    if args.apply_profile is not None:
        from fastapi import HTTPException
        from webui.operations import Operations
        from webui.profile_launch import apply_profile

        try:
            ops = Operations(data)
            with ops.local():
                apply_profile(ops, args.apply_profile, full_restore=False, launch=args.launch, require_recent_review=True)
        except HTTPException as exc:
            show_start_error(DesktopStartError(str(exc.detail)))
            return 1
        except Exception as exc:
            show_start_error(exc)
            return 1
        return 0

    try:
        run_desktop(data)
    except Exception as exc:
        show_start_error(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
