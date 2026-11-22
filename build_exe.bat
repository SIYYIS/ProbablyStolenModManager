@echo off
chcp 65001 >nul
echo 正在使用 PyInstaller 打包为单文件 EXE...
pip show pyinstaller >nul 2>&1 || pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "ProbablyStolenModManager" main.py
echo 打包完成！输出文件位于 dist\ProbablyStolenModManager.exe
pause
