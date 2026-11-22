@echo off
setlocal
cd /d "%~dp0"
call npm.cmd --prefix frontend ci --no-fund || exit /b 1
call npm.cmd --prefix frontend run build || exit /b 1
py -3.10 -m pip install -r requirements-web.txt "pyinstaller==6.22.3" || exit /b 1
py -3.10 -m PyInstaller --noconfirm --onefile --console --distpath dist --workpath build --specpath build --exclude-module tkinter --collect-submodules py7zr --add-data "%CD%\frontend\dist:frontend/dist" --name ProbablyStolenModManager-WebUI web_launcher.py || exit /b 1
echo Built dist\ProbablyStolenModManager-WebUI.exe
