@echo off
REM ===========================================================
REM  QTrans installer
REM
REM  IMPORTANT: this file must stay PURE ASCII.
REM  cmd.exe parses a .bat using the system OEM codepage (950 on
REM  Traditional Chinese Windows) BEFORE chcp can take effect, so
REM  UTF-8 Chinese here gets mis-decoded and Big5 lead bytes eat
REM  the following ASCII characters - even "echo" gets swallowed.
REM  All Chinese text lives in install_helper.py instead, because
REM  Python writes to the console through the Unicode API and is
REM  not affected by the codepage.
REM ===========================================================
chcp 65001 >nul 2>&1
setlocal
title QTrans Installer
cd /d "%~dp0"

REM Make the Python subprocess speak UTF-8 reliably.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo ==============================================================
echo    QTrans Installer
echo ==============================================================
echo.
echo    Looking for Python 3.10 or newer...
echo.

REM ----- Locate a usable Python -------------------------------
REM Try the py launcher first, then plain python.
REM The version probe also filters out the Microsoft Store stub.
set "PYCMD="

py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto found

python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto found

goto nopython

:found
echo    Found: %PYCMD%
echo.
echo    Starting installer. All further messages are in Chinese.
echo.
%PYCMD% "%~dp0install_helper.py"
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:nopython
echo.
echo ==============================================================
echo    Python not found
echo ==============================================================
echo.
echo    QTrans needs Python 3.10 or newer.
echo.
echo    Opening a help file with instructions in Chinese,
echo    and the Python download page.
echo.
echo    IMPORTANT when installing Python: tick the checkbox
echo    "Add python.exe to PATH" at the bottom of the installer.
echo.
if exist "%~dp0PYTHON-HELP.txt" start "" notepad "%~dp0PYTHON-HELP.txt"
start "" "https://www.python.org/downloads/"
echo.
echo    After installing Python, run this file again.
echo.
pause
exit /b 1
