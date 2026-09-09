@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ==============================================
echo  StockPilot 打包: PyInstaller single-file exe
echo ==============================================
python scripts\make_icon.py || goto :err
python -m PyInstaller stockpilot.spec --noconfirm || goto :err
echo.
echo [OK] 产物: dist\StockPilot.exe
echo 自检: dist\StockPilot.exe --smoke
goto :eof
:err
echo [ERROR] 打包失败，请检查上方日志
exit /b 1
