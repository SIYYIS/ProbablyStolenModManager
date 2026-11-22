@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%V in ('node -p "require('./frontend/package.json').version"') do set "APP_VERSION=%%V"
if not defined APP_VERSION exit /b 1
set "APP_NAME=ProbablyStolenModManager-Desktop-v%APP_VERSION%"
if /i not "%~1"=="--reuse-node-modules" (
    call npm.cmd --prefix frontend ci --no-fund || exit /b 1
)
call npm.cmd --prefix frontend run build || exit /b 1
py -3.10 -m pip install -r requirements-desktop.txt "pyinstaller==6.22.3" || exit /b 1
py -3.10 -m PyInstaller --noconfirm --onefile --windowed --distpath dist --workpath build --specpath build --collect-submodules py7zr --collect-data webview --icon "%CD%\frontend\public\app_icon.ico" --add-data "%CD%\frontend\dist:frontend/dist" --name "%APP_NAME%" desktop_launcher.py || exit /b 1
echo Built dist\%APP_NAME%.exe
