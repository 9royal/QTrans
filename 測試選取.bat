@echo off
REM ===========================================================
REM  QTrans select-to-translate diagnostic
REM  PURE ASCII ONLY - see the note in the installer .bat.
REM ===========================================================
chcp 65001 >nul 2>&1
setlocal
title QTrans Select Diagnostic
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYCMD="
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto found
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto found

echo.
echo    Python not found. Run the installer first.
echo.
pause
exit /b 1

:found
%PYCMD% "%~dp0test_select.py"
exit /b 0
