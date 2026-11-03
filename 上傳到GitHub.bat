@echo off
REM ===========================================================
REM  QTrans - upload to GitHub
REM
REM  IMPORTANT: this file must stay PURE ASCII with CRLF line
REM  endings. cmd.exe parses a .bat using the system OEM codepage
REM  (950 on Traditional Chinese Windows) BEFORE chcp takes
REM  effect, so UTF-8 Chinese here gets mis-decoded and Big5 lead
REM  bytes swallow the following ASCII - even "echo" disappears.
REM  All Chinese text lives in github_setup.py instead, because
REM  Python writes to the console through the Unicode API.
REM ===========================================================
chcp 65001 >nul 2>&1
setlocal
title QTrans - GitHub
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
echo    Python 3.10 or newer not found.
echo    Run the installer .bat first.
echo.
pause
exit /b 1

:found
%PYCMD% "%~dp0github_setup.py"
exit /b %errorlevel%
