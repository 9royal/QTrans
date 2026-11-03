@echo off
REM ===========================================================
REM  QTrans diagnostic
REM  PURE ASCII ONLY - cmd.exe parses .bat in the system OEM
REM  codepage (950 here), so Chinese in this file would break it.
REM  All Chinese output comes from diagnose.py instead.
REM ===========================================================
chcp 65001 >nul 2>&1
setlocal
title QTrans Diagnostic
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
%PYCMD% "%~dp0diagnose.py"
exit /b 0
