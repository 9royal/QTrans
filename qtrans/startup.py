"""開機自動啟動（Windows 登錄檔 Run 鍵，不需要管理員權限）。"""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "QTrans"

IS_WINDOWS = sys.platform == "win32"


def _command() -> str:
    if getattr(sys, "frozen", False):          # PyInstaller 打包後
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    # 用 pythonw.exe 啟動才不會跳出黑色主控台視窗
    pythonw = exe.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else exe
    script = Path(__file__).resolve().parents[1] / "run.py"
    return f'"{launcher}" "{script}"'


def is_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enable: bool) -> tuple[bool, str]:
    """回傳 (是否成功, 訊息)。"""
    if not IS_WINDOWS:
        return False, "開機自動啟動目前只支援 Windows。"
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, str(exc)
