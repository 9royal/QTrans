"""把 QTrans 目前的安裝狀態寫成一份報告，讓 Claude 直接讀。"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
buf = io.StringIO()


def say(line: str = "") -> None:
    print(line)
    buf.write(line + "\n")


def probe(code: str) -> tuple[bool, str]:
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=str(ROOT), timeout=120)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


say("QTrans 安裝狀態報告")
say("=" * 50)
say(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
say(f"專案資料夾  : {ROOT}")
say()

# --- 套件 ---
say("[套件]")
for mod, extra in [("PySide6", "PySide6.__version__"),
                   ("requests", "requests.__version__"),
                   ("pyperclip", "'ok'")]:
    ok, out = probe(f"import {mod};print({extra})")
    say(f"  {'OK ' if ok else 'FAIL'} {mod:10} {out.splitlines()[-1] if ok and out else ''}")
    if not ok:
        say(f"       {out.strip().splitlines()[-1][:160] if out else ''}")

ok, out = probe("from PySide6.QtWidgets import QApplication;"
                "from PySide6.QtMultimedia import QMediaPlayer;print('ok')")
say(f"  {'OK ' if ok else 'FAIL'} Qt 元件（視窗＋朗讀）")
if not ok:
    say(f"       {out.strip().splitlines()[-1][:160] if out else ''}")
say()

# --- DPAPI ---
say("[金鑰加密 DPAPI]")
try:
    p = subprocess.run([sys.executable, "-m", "qtrans.keyvault"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=60)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in out.splitlines():
        say("  " + line.rstrip())
    say(f"  -> 結果：{'通過' if p.returncode == 0 else '未通過（代碼 %d）' % p.returncode}")
except Exception as exc:
    say(f"  無法執行：{exc}")
say()

# --- 設定檔 ---
say("[設定檔]")
appdata = os.environ.get("APPDATA", "")
cfg = Path(appdata) / "QTrans" / "config.json" if appdata else None
if cfg and cfg.exists():
    say(f"  存在：{cfg}")
    try:
        text = cfg.read_text(encoding="utf-8")
        import json
        data = json.loads(text)
        for name in ("gemini", "deepl", "openai", "libretranslate"):
            val = (data.get("engines", {}).get(name, {}) or {}).get("api_key", "")
            if not val:
                state = "未填"
            elif val.startswith("dpapi:v1:"):
                state = "已加密 ✓"
            else:
                state = "明文 ⚠"
            say(f"    {name:14} {state}")
        say(f"    預設引擎        {data.get('engine')}")
        say(f"    目標語言        {data.get('target_lang')}")
    except Exception as exc:
        say(f"  讀取失敗：{exc}")
else:
    say("  尚未建立（還沒在設定視窗按過確定，屬正常）")
say()

# --- 捷徑與執行狀態 ---
say("[捷徑與執行狀態]")
for base in (os.environ.get("USERPROFILE", ""), os.environ.get("OneDrive", "")):
    if base:
        lnk = Path(base) / "Desktop" / "QTrans.lnk"
        if lnk.exists():
            say(f"  桌面捷徑：{lnk}")
            break
else:
    say("  桌面捷徑：找不到（可用「啟動 QTrans.bat」代替）")

try:
    p = subprocess.run(["tasklist", "/fi", "imagename eq pythonw.exe"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    running = "pythonw.exe" in (p.stdout or "")
    say(f"  QTrans 是否執行中：{'是' if running else '否'}")
except Exception:
    say("  QTrans 是否執行中：無法判斷")

say()
say("=" * 50)

report = ROOT / "DIAGNOSTIC.txt"
try:
    report.write_text(buf.getvalue(), encoding="utf-8")
    say(f"報告已寫入：{report}")
    say("請回到對話告訴 Claude「跑好了」，他會直接讀這個檔案。")
except Exception as exc:
    say(f"報告寫入失敗：{exc}")

print()
input("按 Enter 關閉...")
