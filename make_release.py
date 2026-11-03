"""把 QTrans 打包成可以交給同事的檔案。

兩種產出，用途不同：

  1. 原始碼包（約 150 KB）
     同事要先裝 Python，但檔案小、可以用 Email 或通訊軟體傳。
     也是上 GitHub 用的乾淨版本。

  2. 免安裝版（約 60–90 MB 壓縮檔）
     把 Python 和所有套件一起包進去，同事解壓縮就能雙擊執行。
     檔案大，要用雲端硬碟（Google Drive、OneDrive）分享。

用法：
    python make_release.py            跳出選單
    python make_release.py --source   只做原始碼包
    python make_release.py --exe      只做免安裝版
    python make_release.py --both     兩種都做

產出都放在 release\\ 資料夾。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "release"
VERSION = "1.3"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------- 要帶走什麼

# 原始碼包帶這些。清單寫成白名單而不是黑名單，是刻意的：
# 黑名單漏掉一個檔案，金鑰或個人設定就跟著出門了，而且不會有人發現。
SOURCE_FILES = [
    "run.py",
    "requirements.txt",
    "install_helper.py",
    "安裝.bat",
    "啟動 QTrans.bat",
    "檢查引擎.bat",
    "diagnose_engines.py",
    "PYTHON-HELP.txt",
    "翻譯API申請指南.txt",
    "開始使用.txt",
    "ro-mark.svg",
    "ro-mark-white.svg",
]

# qtrans 套件整個帶走，但不要帶編譯快取。
PACKAGE_DIR = "qtrans"

# 這些絕對不能進包裡。設定檔其實放在 %APPDATA%\QTrans\，
# 不在專案資料夾，所以正常情況不會有；列出來是以防有人手動放過。
NEVER_PACK = {
    "config.json",
    "select-debug.log",
    "SELECT-DEBUG.txt",
    "DIAGNOSTIC-ENGINES.txt",
    "DIAGNOSTIC.txt",
}


def say(line: str = "") -> None:
    print(line, flush=True)


def _copy_tree_clean(src: Path, dst: Path) -> int:
    """複製整個資料夾，跳過快取與測試殘骸。回傳複製的檔案數。"""
    count = 0
    for path in sorted(src.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        if path.name in NEVER_PACK:
            continue
        if path.is_dir():
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _zip_dir(folder: Path, zip_path: Path) -> None:
    """把資料夾壓成 zip，裡面保留一層同名資料夾。

    保留那一層很重要：同事直接在「下載」裡按右鍵解壓縮時，
    檔案才不會散成一地，而是乖乖待在一個資料夾裡。
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_dir():
                continue
            zf.write(path, Path(folder.name) / path.relative_to(folder))


def _mb(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


# --------------------------------------------------------------- 原始碼包

def build_source() -> Path | None:
    say("=" * 58)
    say("  製作原始碼包")
    say("=" * 58)
    say()

    stage = RELEASE / f"QTrans-{VERSION}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    missing = []
    copied = 0
    for name in SOURCE_FILES:
        src = ROOT / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, stage / name)
        copied += 1

    pkg_src = ROOT / PACKAGE_DIR
    if not pkg_src.is_dir():
        say(f"  ✗ 找不到 {PACKAGE_DIR}\\ 資料夾，無法打包。")
        say("    請確認這個腳本放在 QTrans 專案的根目錄。")
        return None
    copied += _copy_tree_clean(pkg_src, stage / PACKAGE_DIR)

    say(f"  收進 {copied} 個檔案")
    if missing:
        say()
        say("  以下檔案不存在，已跳過：")
        for name in missing:
            say(f"    ・{name}")
        say("    （若是說明文件缺漏，同事拿到的包會少一份說明，程式本身仍可運作）")

    zip_path = RELEASE / f"QTrans-{VERSION}-原始碼.zip"
    _zip_dir(stage, zip_path)
    shutil.rmtree(stage)

    say()
    say(f"  ✓ 完成：{zip_path.name}（{_mb(zip_path)}）")
    say()
    return zip_path


# --------------------------------------------------------------- 免安裝版

def build_exe() -> Path | None:
    say("=" * 58)
    say("  製作免安裝版")
    say("=" * 58)
    say()
    say("  這一步會下載 PyInstaller 並完整編譯，約需 3–10 分鐘。")
    say("  過程中畫面會跳出很多訊息，是正常的。")
    say()

    # PyInstaller 只能打包成「執行它的那個平台」的執行檔。
    if sys.platform != "win32":
        say("  ✗ 免安裝版只能在 Windows 上製作。")
        return None

    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                        "pyinstaller"], check=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                        str(ROOT / "requirements.txt")], check=True)
    except subprocess.CalledProcessError:
        say()
        say("  ✗ 套件安裝失敗。請確認網路連線後重試。")
        return None

    build_dir = ROOT / "build"
    dist_dir = ROOT / "dist"
    for folder in (build_dir, dist_dir):
        if folder.exists():
            shutil.rmtree(folder)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "QTrans",
        "--windowed",                       # 不要跳出黑色主控台視窗
        "--hidden-import", "PySide6.QtMultimedia",
        # QtSvg 是畫 Ro 標誌用的。PyInstaller 掃不到它（brand.py 是在
        # 函式裡才 import），漏了的話打包版就看不到作者簽名。
        "--hidden-import", "PySide6.QtSvg",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.Qt3DCore",
        str(ROOT / "run.py"),
    ]
    say()
    say("  開始編譯……")
    say()
    try:
        subprocess.run(cmd, check=True, cwd=str(ROOT))
    except subprocess.CalledProcessError:
        say()
        say("  ✗ 編譯失敗，請往上捲動看錯誤訊息。")
        return None

    app_dir = dist_dir / "QTrans"
    if not (app_dir / "QTrans.exe").exists():
        say()
        say("  ✗ 編譯似乎成功了，但找不到 QTrans.exe。")
        return None

    # 把說明文件一起放進去 —— 同事解壓縮後只會看到這個資料夾。
    for name in ("開始使用.txt", "翻譯API申請指南.txt",
                 "ro-mark.svg", "ro-mark-white.svg"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, app_dir / name)

    zip_path = RELEASE / f"QTrans-{VERSION}-免安裝版.zip"
    say()
    say("  壓縮中（檔案大，約需 1–3 分鐘）……")
    _zip_dir(app_dir, zip_path)

    say()
    say(f"  ✓ 完成：{zip_path.name}（{_mb(zip_path)}）")
    say()
    say("  這個檔案太大，無法用 Email 寄送。")
    say("  請上傳到 Google Drive 或 OneDrive，把連結傳給同事。")
    say()
    say(f"  未壓縮的版本留在：{app_dir}")
    say("  若要自己先試用，直接雙擊裡面的 QTrans.exe。")
    say()
    return zip_path


# --------------------------------------------------------------- 主程式

def menu() -> str:
    say()
    say("=" * 58)
    say(f"  QTrans 打包工具　v{VERSION}")
    say("=" * 58)
    say()
    say("  要做哪一種？")
    say()
    say("   [1] 原始碼包（約 150 KB）")
    say("       同事要先裝 Python。檔案小，可用 Email 或 LINE 傳。")
    say("       也是上傳到 GitHub 用的版本。")
    say()
    say("   [2] 免安裝版（約 60–90 MB）")
    say("       同事不用裝 Python，解壓縮雙擊就能用。")
    say("       檔案大，要用雲端硬碟分享。製作需 3–10 分鐘。")
    say()
    say("   [3] 兩種都做")
    say()
    say("   [0] 離開")
    say()
    try:
        return input("  請輸入數字後按 Enter：").strip()
    except Exception:
        return "0"


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]
    if "--source" in args:
        choice = "1"
    elif "--exe" in args:
        choice = "2"
    elif "--both" in args:
        choice = "3"
    else:
        choice = menu()

    RELEASE.mkdir(exist_ok=True)
    made = []

    if choice == "1":
        result = build_source()
        if result:
            made.append(result)
    elif choice == "2":
        result = build_exe()
        if result:
            made.append(result)
    elif choice == "3":
        result = build_source()
        if result:
            made.append(result)
        result = build_exe()
        if result:
            made.append(result)
    else:
        say()
        say("  已取消。")
        return 0

    say("=" * 58)
    if made:
        say("  打包完成")
        say("=" * 58)
        say()
        for path in made:
            say(f"  ・{path.name}　（{_mb(path)}）")
        say()
        say(f"  位置：{RELEASE}")
        say()
        say("  提醒：你的 API 金鑰不在這些檔案裡。")
        say("  金鑰存在 %APPDATA%\\QTrans\\config.json，而且用 Windows 帳號")
        say("  加密過，複製到別人電腦也解不開。同事要自己申請、自己填。")
        say("  （不填也能用 Google 免費翻譯，只是會有次數限制。）")
    else:
        say("  沒有產出任何檔案")
        say("=" * 58)
    say()
    return 0 if made or choice == "0" else 1


if __name__ == "__main__":
    code = main()
    try:
        input("按 Enter 關閉...")
    except Exception:
        pass
    sys.exit(code)
