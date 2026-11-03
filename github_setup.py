"""把 QTrans 上傳到 GitHub。

做的事：檢查工具 → 初始化 git → 讓你確認要上傳的檔案 → 建立存放庫 → 推上去。

最關鍵的一步是「讓你確認」。.gitignore 已經排除了設定檔與記錄檔，
但那是我寫的規則，不是你看過的事實 —— 推上公開存放庫之後才發現
金鑰跟著走了，是補不回來的（GitHub 會留下歷史）。所以推之前一定
把清單印出來給你看。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_NAME = "QTrans"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def say(line: str = "") -> None:
    print(line, flush=True)


def run(args: list[str], check: bool = True, quiet: bool = False):
    """執行指令並回傳 CompletedProcess。"""
    return subprocess.run(args, cwd=str(ROOT), check=check,
                          capture_output=quiet, text=True,
                          encoding="utf-8", errors="replace")


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ask(prompt: str, options: dict[str, str]) -> str:
    while True:
        say()
        for key, label in options.items():
            say(f"   [{key}] {label}")
        say()
        try:
            answer = input("  請輸入後按 Enter：").strip().lower()
        except Exception:
            return ""
        if answer in options:
            return answer
        say("  請輸入上面其中一個選項。")


# ---------------------------------------------------------------- 檢查工具

def check_tools() -> str | None:
    """回傳 'gh'（全自動）、'git'（半自動）或 None（做不下去）。"""
    say("[1/5] 檢查工具")
    say()

    if not have("git"):
        say("  ✗ 找不到 git")
        say()
        say("    git 是版本控制工具，上傳到 GitHub 一定要用它。")
        say("    正在開啟下載頁，裝好之後再執行一次這個檔案。")
        say("    （安裝時全部用預設值即可，一直按「下一步」就好。）")
        try:
            os.startfile("https://git-scm.com/download/win")
        except Exception:
            say("    網址：https://git-scm.com/download/win")
        return None

    version = run(["git", "--version"], quiet=True).stdout.strip()
    say(f"  ✓ {version}")

    if have("gh"):
        who = run(["gh", "api", "user", "--jq", ".login"],
                  check=False, quiet=True)
        login = (who.stdout or "").strip()
        if who.returncode == 0 and login:
            say(f"  ✓ GitHub CLI（已登入為 {login}）")
            say()
            return "gh"
        say("  ! GitHub CLI 有裝但還沒登入")
        say()
        say("    等一下會請你登入，瀏覽器會自動打開。")
        say()
        return "gh-login"

    say("  ! 沒有 GitHub CLI")
    say()
    say("    沒有它也能做，只是要你手動去網頁建立一個空的存放庫。")
    say("    想要全自動的話，可以先裝 GitHub CLI：https://cli.github.com")
    say()
    return "git"


# ---------------------------------------------------------------- 初始化

def init_repo() -> bool:
    say("[2/5] 準備 git 儲存庫")
    say()

    if not (ROOT / ".gitignore").exists():
        say("  ✗ 找不到 .gitignore")
        say()
        say("    沒有它，你的設定檔和記錄檔會一起被上傳。停在這裡比較安全。")
        return False
    say("  ✓ .gitignore 在")

    if (ROOT / ".git").is_dir():
        say("  ✓ 已經是 git 儲存庫了，沿用現有的")
    else:
        run(["git", "init"], quiet=True)
        say("  ✓ 已初始化")

    # 沒設過身分的話 git commit 會直接失敗，先補上。
    name = run(["git", "config", "user.name"], check=False, quiet=True).stdout.strip()
    email = run(["git", "config", "user.email"], check=False, quiet=True).stdout.strip()
    if not name or not email:
        say()
        say("  git 還不知道你是誰（這會記在每一次提交裡）。")
        try:
            if not name:
                got = input("  你的名字（可以隨便寫，例如 Ro）：").strip() or "Ro"
                run(["git", "config", "user.name", got], quiet=True)
            if not email:
                got = input("  你的 Email：").strip()
                if got:
                    run(["git", "config", "user.email", got], quiet=True)
                else:
                    run(["git", "config", "user.email",
                         "noreply@users.noreply.github.com"], quiet=True)
        except Exception:
            return False
        say("  ✓ 已記住")

    run(["git", "add", "-A"], quiet=True)
    say()
    return True


# ---------------------------------------------------------------- 確認內容

RISKY = ("config.json", ".env", ".key", "select-debug.log",
         "DIAGNOSTIC", "SELECT-DEBUG")


def confirm_files() -> bool:
    say("[3/5] 確認要上傳的檔案")
    say()

    result = run(["git", "diff", "--cached", "--name-only"], quiet=True)
    files = [f for f in result.stdout.splitlines() if f.strip()]

    if not files:
        say("  沒有任何變更要提交。")
        say("  如果這個資料夾之前就推過了，直接看第 5 步的更新指令。")
        return False

    say(f"  這 {len(files)} 個檔案會上傳到 GitHub：")
    say()
    for f in files[:60]:
        say(f"    {f}")
    if len(files) > 60:
        say(f"    …還有 {len(files) - 60} 個")

    hits = [f for f in files if any(bad.lower() in f.lower() for bad in RISKY)]
    say()
    if hits:
        say("  ✗ 危險：下面這些不該上傳")
        for f in hits:
            say(f"      {f}")
        say()
        say("    已經停下來了。請檢查 .gitignore，或直接把這些檔案刪掉，")
        say("    然後再執行一次。")
        return False

    say("  ✓ 沒有看到設定檔、金鑰或診斷記錄")
    say()
    say("  提醒：你的 API 金鑰存在 %APPDATA%\\QTrans\\config.json，")
    say("  本來就不在這個資料夾裡，所以不會被上傳。")
    say()

    answer = ask("  上面的清單看起來對嗎？",
                 {"y": "對，繼續", "n": "不對，先停下來"})
    return answer == "y"


# ---------------------------------------------------------------- 可見性

def pick_visibility() -> str | None:
    say("[4/5] 這個存放庫要公開還是私有？")
    say()
    say("  公開（public）")
    say("    任何人都能看到程式碼、也能直接下載。")
    say("    同事不用你另外給權限，傳個網址就好。")
    say("    注意：公開之後即使改回私有，中間被看過、被複製的內容收不回來。")
    say()
    say("  私有（private）")
    say("    只有你看得到。要讓同事下載，得一個一個邀請他們當協作者。")
    say("    之後想公開隨時可以改，方向是安全的。")
    say()
    say("  這支程式裡沒有金鑰也沒有個資，公開是常見選擇；")
    say("  但要不要公開是你的決定，不是技術問題。")

    answer = ask("  選哪一個？",
                 {"1": "公開 public（同事直接下載）",
                  "2": "私有 private（之後可以再改公開）",
                  "0": "先不要，離開"})
    if answer == "1":
        return "--public"
    if answer == "2":
        return "--private"
    return None


# ---------------------------------------------------------------- 推上去

def push(mode: str, visibility: str) -> bool:
    say()
    say("[5/5] 建立存放庫並上傳")
    say()

    status = run(["git", "diff", "--cached", "--quiet"], check=False, quiet=True)
    if status.returncode != 0:          # 有東西待提交
        run(["git", "commit", "-m", "QTrans v1.3"], quiet=True)
        say("  ✓ 已建立提交")

    if mode == "gh-login":
        say()
        say("  要先登入 GitHub，瀏覽器會自動打開。")
        say("  （選 GitHub.com → HTTPS → 用瀏覽器登入）")
        say()
        login = run(["gh", "auth", "login"], check=False)
        if login.returncode != 0:
            say("  ✗ 登入沒完成，請再執行一次這個檔案。")
            return False
        mode = "gh"

    if mode == "gh":
        say()
        say(f"  正在建立 {REPO_NAME} 並上傳……")
        say()
        created = run(["gh", "repo", "create", REPO_NAME, visibility,
                       "--source=.", "--push"], check=False)
        if created.returncode != 0:
            say()
            say("  ✗ 建立失敗。最常見的原因是你的帳號下已經有同名的存放庫。")
            say(f"    到 https://github.com/new 看看 {REPO_NAME} 是不是已經存在。")
            return False

        url = run(["git", "remote", "get-url", "origin"],
                  check=False, quiet=True).stdout.strip()
        url = url.replace(".git", "")
        say()
        say("  ✓ 上傳完成")
        say()
        say(f"    網址：{url}")
        say()
        if visibility == "--public":
            say("    這個網址可以直接傳給同事，他們按綠色的 Code 按鈕")
            say("    → Download ZIP 就能下載。")
        else:
            say("    私有存放庫同事看不到。要分享的話：")
            say("    進去 Settings → Collaborators → 加入他們的 GitHub 帳號。")
        try:
            os.startfile(url)
        except Exception:
            pass
        return True

    # 沒有 gh：引導手動建立
    say("  沒有 GitHub CLI，所以存放庫要你自己在網頁上建一個空的。")
    say()
    say("  1. 正在開啟 https://github.com/new")
    say(f"  2. Repository name 填：{REPO_NAME}")
    say(f"  3. 選 {'Public' if visibility == '--public' else 'Private'}")
    say("  4. 下面三個勾選框（README、.gitignore、license）全部不要勾")
    say("  5. 按 Create repository")
    say()
    try:
        os.startfile("https://github.com/new")
    except Exception:
        say("     網址：https://github.com/new")
    say()
    try:
        account = input("  建好之後，把你的 GitHub 帳號名稱輸入這裡：").strip()
    except Exception:
        return False
    if not account:
        say("  沒有輸入帳號，停在這裡。")
        return False

    remote = f"https://github.com/{account}/{REPO_NAME}.git"
    run(["git", "remote", "remove", "origin"], check=False, quiet=True)
    run(["git", "remote", "add", "origin", remote], quiet=True)
    run(["git", "branch", "-M", "main"], check=False, quiet=True)
    say()
    say("  正在上傳……（可能會跳出 GitHub 登入視窗）")
    say()
    pushed = run(["git", "push", "-u", "origin", "main"], check=False)
    if pushed.returncode != 0:
        say()
        say("  ✗ 上傳失敗。最常見的兩個原因：")
        say("     ・帳號名稱打錯了")
        say("     ・剛才建立存放庫時勾了 README，導致兩邊對不起來")
        say(f"       那就改執行：git push -u origin main --force")
        return False

    url = f"https://github.com/{account}/{REPO_NAME}"
    say()
    say("  ✓ 上傳完成")
    say()
    say(f"    網址：{url}")
    try:
        os.startfile(url)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- 主程式

def main() -> int:
    say()
    say("=" * 58)
    say("  把 QTrans 上傳到 GitHub")
    say("=" * 58)
    say()

    mode = check_tools()
    if mode is None:
        return 1
    if not init_repo():
        return 1
    if not confirm_files():
        return 1
    visibility = pick_visibility()
    if visibility is None:
        say()
        say("  已取消，什麼都沒有上傳。")
        return 0
    if not push(mode, visibility):
        return 1

    say()
    say("=" * 58)
    say("  以後要更新")
    say("=" * 58)
    say()
    say("  改完程式之後，在這個資料夾開 PowerShell，執行：")
    say()
    say("      git add .")
    say('      git commit -m "說明這次改了什麼"')
    say("      git push")
    say()
    say("  或者再執行一次這個檔案也可以，它會跳過已經建好的部分。")
    say()
    return 0


if __name__ == "__main__":
    code = main()
    say()
    try:
        input("按 Enter 關閉...")
    except Exception:
        pass
    sys.exit(code)
