@echo off
REM ===========================================================
REM  QTrans launcher
REM  Must stay PURE ASCII - see the note in the installer .bat.
REM ===========================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM pythonw.exe runs without leaving a black console window.
where pythonw >nul 2>&1
if errorlevel 1 goto trylauncher
start "" pythonw "%~dp0run.py"
exit /b 0

:trylauncher
REM pythonw is not on PATH - ask the py launcher where it lives.
py -3 -c "import sys,os;w=os.path.join(os.path.dirname(sys.executable),'pythonw.exe');print(w if os.path.exists(w) else sys.executable)" > "%TEMP%\qtrans_pyw.txt" 2>nul
if errorlevel 1 goto fail
set "PYW="
set /p PYW=<"%TEMP%\qtrans_pyw.txt"
del "%TEMP%\qtrans_pyw.txt" >nul 2>&1
if not defined PYW goto fail
start "" "%PYW%" "%~dp0run.py"
exit /b 0

:fail
echo.
echo    Cannot start QTrans.
echo    Please run the installer first: double-click the .bat
echo    file whose name starts with an-zhuang (install).
echo.
pause
exit /b 1
