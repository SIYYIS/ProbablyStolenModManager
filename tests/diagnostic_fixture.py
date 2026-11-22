"""Isolated HTTP fixture for the diagnostics router and frontend harness."""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core import diagnostics
from core.paths import GAME_EXE_NAME
from core.settings import AppSettings
from webui.diagnostic_routes import diagnostic_router
from webui.operations import Operations
from webui.server import LoopbackGuard


FIXTURE_DIGEST = "1e02d30b7803c231349fe6c3ee809b63dd446d0d680ef688616f1ff00f5a57fa"


def fixture_app(root: Path):
    game, save, workshop = root / "game", root / "save", root / "workshop"
    for folder in (game, save / "ModConfigs", workshop):
        folder.mkdir(parents=True)
    marker = game / "dev.txt"
    (game / GAME_EXE_NAME).touch()
    (game / diagnostics._ASSEMBLY).parent.mkdir(parents=True)
    (game / diagnostics._ASSEMBLY).write_bytes(b"fixture-managed-assembly")
    (save / "SaveFile.es3").write_text('{"modOrder":{"value":["fixture.current-only"]}, "privateSave": "never-export-this"}')
    (save / "Player.log").write_bytes((Path(__file__).parent / "fixtures" / "diagnostic-player.log").read_bytes())
    (save / "ModConfigs" / "fixture.json").write_text(
        r'{"extraCustomers": 4, "apiKey": "unique-config-secret", "home": "C:\\Users\\FixtureOwner\\config"}',
        encoding="utf-8",
    )
    ops = Operations(root / "manager")
    AppSettings(game_dir=str(game), save_dir=str(save), workshop_dir=str(workshop)).save(ops.settings_path)
    app = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
    app.add_middleware(LoopbackGuard)
    app.include_router(diagnostic_router(ops), prefix="/api")

    @app.get("/")
    def ready():
        return {"fixture": "diagnostics"}

    return app, ops, marker


def main():
    import uvicorn

    with TemporaryDirectory(prefix="psmm-diagnostics-") as tmp:
        root = Path(tmp)
        assets = root / "assets"
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        subprocess.run(["node", "node_modules/vite/bin/vite.js", "build", "--config",
                        "diagnostics-e2e/vite.config.ts", "--no-emptyOutDir", "--outDir", str(assets)], cwd=frontend, check=True)
        app, _, _ = fixture_app(root)
        app.mount("/", StaticFiles(directory=assets))
        with patch.object(diagnostics, "_DEV_SHA256", FIXTURE_DIGEST), patch.object(diagnostics, "_game_running", return_value=False):
            uvicorn.run(app, host="127.0.0.1", port=8876, access_log=False)


if __name__ == "__main__":
    main()
