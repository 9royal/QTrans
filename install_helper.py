"""QTrans 安裝流程（由 安裝.bat 呼叫，不要直接雙擊）。

批次檔只負責找到 Python；真正的安裝步驟放在這裡，因為 Python 處理中文輸出
比 .bat 可靠得多（.bat 在繁中 Windows 的預設 Big5 碼頁下常出現亂碼）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:  # 確保中文不會因為主控台碼頁而變亂碼
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OK, BAD, WARN = "  [OK] ", "  [!!] ", "  [--] "
results: list[tuple[bool, str]] = []


def head(step: str, title: str) -> None:
    print()
    print("=" * 62)
    print(f"  步驟 {step}　{title}")
    print("=" * 62)


def run(args: list[str], live: bool = False) -> tuple[int, str]:
    """執行指令。live=True 時直接把輸出印到畫面（給 pip 用，讓你看到進度）。

    找不到執行檔時回傳非零代碼而不是拋出例外 —— 安裝程式不該因為某一步
    的工具不存在就整個崩掉，後面的步驟和總結還是要跑完。
    """
    try:
        if live:
            return subprocess.call(args, cwd=str(ROOT)), ""
        proc = subprocess.run(args, cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except OSError as exc:
        return 127, f"無法執行 {args[0]}：{exc}"
    except Exception as exc:
        return 126, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------ 1. 環境
head("1/6", "檢查環境")
print(f"  Python 版本 ：{sys.version.split()[0]}")
print(f"  Python 位置 ：{sys.executable}")
print(f"  安裝資料夾  ：{ROOT}")

pythonw = Path(sys.executable).with_name("pythonw.exe")
if pythonw.exists():
    print(f"  pythonw.exe ：{pythonw}")
    results.append((True, "環境檢查"))
else:
    print(f"{WARN}找不到 pythonw.exe，啟動時會多一個黑色主控台視窗（功能正常）。")
    results.append((True, "環境檢查（無 pythonw）"))

if not (ROOT / "requirements.txt").exists():
    print(f"\n{BAD}這個資料夾裡沒有 requirements.txt。")
    print("     請確認解壓縮後，安裝.bat 和 qtrans 資料夾在同一層。")
    sys.exit(1)


# ------------------------------------------------------------ 2. 相依套件
head("2/6", "安裝相依套件（PySide6 約 70MB，請耐心等一兩分鐘）")
code, _ = run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], live=True)
code, _ = run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], live=True)
if code == 0:
    print(f"\n{OK}套件安裝完成。")
    results.append((True, "安裝相依套件"))
else:
    print(f"\n{BAD}pip 安裝失敗（代碼 {code}）。常見原因：")
    print("     ・公司或學校網路阻擋 PyPI → 換網路或設定代理伺服器")
    print("     ・磁碟空間不足")
    print("     上面的紅字訊息就是原因，把它拍下來或複製給我。")
    results.append((False, "安裝相依套件"))


# ------------------------------------------------------------ 3. 匯入實測
# 注意：第 5 步的測試套件用的是 PySide6 的「替身」，所以不會驗證真正的 Qt
# 有沒有裝好。這一步才是真的在考 PySide6。
head("3/6", "確認套件真的能載入")
probe = (
    "import PySide6, requests, pyperclip;"
    "from PySide6.QtWidgets import QApplication;"
    "from PySide6.QtMultimedia import QMediaPlayer;"
    "print(PySide6.__version__)"
)
code, out = run([sys.executable, "-c", probe])
if code == 0:
    print(f"{OK}PySide6 {out.strip()}　requests、pyperclip 都正常。")
    results.append((True, "套件載入"))
else:
    print(f"{BAD}套件載入失敗：")
    print("     " + out.strip().replace("\n", "\n     ")[:800])
    results.append((False, "套件載入"))


# ------------------------------------------------------------ 4. DPAPI
head("4/6", "驗證金鑰加密（最重要的一步）")
print("  這段程式呼叫 Windows 底層 API，只能在你的電腦上驗證。")
print()
code, out = run([sys.executable, "-m", "qtrans.keyvault"])
print("  " + out.strip().replace("\n", "\n  "))
dpapi_ok = code == 0 and "✓" in out
if dpapi_ok:
    results.append((True, "金鑰加密（DPAPI）"))
else:
    print(f"\n{BAD}DPAPI 驗證沒通過。API 金鑰會改以明文儲存。")
    print("     請把上面這段訊息貼給 Claude，我照你的 Windows 版本修。")
    results.append((False, "金鑰加密（DPAPI）"))


# ------------------------------------------------------------ 5. 測試
head("5/6", "執行程式自我測試（66 項）")
code, out = run([sys.executable, "-I", str(ROOT / "tests" / "test_qtrans.py")])
summary = next((ln for ln in out.splitlines() if "通過" in ln and "失敗" in ln), "")
if code == 0:
    print(f"{OK}{summary.strip() or '全部通過'}")
    results.append((True, "自我測試"))
else:
    print(f"{BAD}{summary.strip() or '測試失敗'}")
    for line in out.splitlines():
        if line.strip().startswith("✗"):
            print("     " + line.strip())
    results.append((False, "自我測試"))


# ------------------------------------------------------------ 6. 捷徑
head("6/6", "建立桌面捷徑")
launcher = pythonw if pythonw.exists() else Path(sys.executable)
target = ROOT / "run.py"
desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
if not desktop.is_dir():
    onedrive = Path(os.environ.get("OneDrive", "")) / "Desktop"
    desktop = onedrive if onedrive.is_dir() else desktop

lnk = desktop / "QTrans.lnk"
ps = (
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
    "$s.TargetPath='{exe}';"
    "$s.Arguments='\"{script}\"';"
    "$s.WorkingDirectory='{cwd}';"
    "$s.IconLocation='{exe},0';"
    "$s.Description='QTrans 桌面即時翻譯';"
    "$s.Save()"
).format(lnk=lnk, exe=launcher, script=target, cwd=ROOT)

code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
if code == 0 and lnk.exists():
    print(f"{OK}已建立：{lnk}")
    results.append((True, "桌面捷徑"))
else:
    print(f"{WARN}捷徑建立失敗，但不影響使用 —— 直接雙擊資料夾裡的")
    print("       「啟動 QTrans.bat」就能開啟。")
    results.append((True, "桌面捷徑（略過）"))


# ------------------------------------------------------------ 總結
print()
print("=" * 62)
print("  安裝結果")
print("=" * 62)
for ok, name in results:
    print(f"  {'✓' if ok else '✗'}  {name}")

failed = [name for ok, name in results if not ok]
print()
if not failed:
    print("  安裝完成，全部項目通過。")
else:
    print(f"  有 {len(failed)} 項未通過：{'、'.join(failed)}")
    print("  把這個畫面拍下來或複製文字給 Claude，我來處理。")

print()
print("-" * 62)
print("  接下來要做的事")
print("-" * 62)
print("  1. 啟動：雙擊桌面的 QTrans 捷徑（或資料夾裡的「啟動 QTrans.bat」）")
print("  2. 右下角系統匣會出現藍色「譯」圖示")
print("  3. 在圖示上按右鍵 → 設定 → 翻譯引擎，貼上 Gemini 金鑰")
print("     免費申請：https://aistudio.google.com/apikey")
print("  4. 貼完按「列出可用模型」，挑一個 flash 結尾的")
print("  5. 回「一般」分頁，把預設引擎改成 Gemini")
print()
print("  之後就是：選取任何文字 → 按 Ctrl+Q")
print()

if not failed:
    try:
        answer = input("  現在要啟動 QTrans 嗎？(Y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer in ("", "y", "yes"):
        try:
            subprocess.Popen([str(launcher), str(target)], cwd=str(ROOT),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            print("\n  已啟動，請看右下角系統匣的「譯」圖示。")
            print("  （這個視窗可以關掉了）")
        except Exception as exc:
            print(f"\n  自動啟動失敗（{exc}）。")
            print("  請改用桌面捷徑，或雙擊「啟動 QTrans.bat」。")

print()
sys.exit(1 if failed else 0)
