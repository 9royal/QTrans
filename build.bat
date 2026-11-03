@echo off
chcp 65001 >nul
REM ============================================================
REM  QTrans 打包腳本（Windows）
REM  產出： dist\QTrans\QTrans.exe
REM ============================================================

echo [1/3] 安裝相依套件...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo [2/3] 開始打包（資料夾模式，啟動較快）...
pyinstaller --noconfirm --clean ^
  --name QTrans ^
  --windowed ^
  --hidden-import PySide6.QtMultimedia ^
  --exclude-module tkinter ^
  --exclude-module matplotlib ^
  run.py
if errorlevel 1 goto :error

echo.
echo [3/3] 完成。
echo 執行檔位置： %CD%\dist\QTrans\QTrans.exe
echo.
echo 若想改成「單一 exe 檔」，把上面的指令加上 --onefile 即可，
echo 但每次啟動都要先解壓縮，常駐型工具通常不建議。
goto :eof

:error
echo.
echo *** 打包失敗，請往上捲動查看錯誤訊息。***
exit /b 1
