@echo off
setlocal
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto err
py -3 -m PyInstaller --clean --noconfirm pyrat.spec
if errorlevel 1 goto err
echo dist\PyRAT.exe
pause
exit /b 0
:err
echo build failed
pause
exit /b 1