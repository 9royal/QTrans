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
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_NAME = "QTrans"
LOG_PATH = ROOT / "GITHUB-LOG.txt"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_log: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)
    _log.append(line)


def write_log() -> None:
    """把整個過程寫成報告檔。

    視窗一閃而過、或訊息刷太快看不清楚時，這個檔案是唯一的真相。
    """
    try:
        LOG_PATH.write_text("\n".join(_log) + "\n", encoding="utf-8")
    except Exception:
        pass


# git 在繁體中文 Windows 上會用 cp950 輸出中文訊息，而我們是用 UTF-8
# 去解讀它 —— 解出來就是「G;4JT06G 194」那種看不懂的東西。
# 強迫 git 講英文（ASCII），兩邊就不會打架；英文訊息也比較好查。
_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_TERMINAL_PROMPT": "1",
}


def run(args: list[str], check: bool = False, quiet: bool = False):
    """執行指令並回傳 CompletedProcess。

    check 預設是 False。原本預設 True，任何一個指令失敗就拋例外、
    視窗直接關掉，使用者只看得到一閃而過的畫面 —— 那比失敗本身更糟。
    現在一律由呼叫端看 returncode 自己決定怎麼處理。
    """
    try:
        proc = subprocess.run(args, cwd=str(ROOT), check=check,
                              capture_output=quiet, text=True, env=_ENV,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        say(f"  ✗ 找不到指令：{args[0]}")
        raise
    # 把每一道指令和結果都記進報告，出事時才查得出是哪一步。
    _log.append(f"    $ {' '.join(args)}  → returncode={proc.returncode}")
    if quiet:
        for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
            for line in (stream or "").splitlines():
                _log.append(f"      [{tag}] {line}")
    return proc


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def diagnose() -> None:
    """把現場狀態收進報告檔。

    只寫檔不印出來 —— 使用者不需要看這些，但我需要。
    """
    _log.append("")
    _log.append("=" * 50)
    _log.append("現場狀態（給排錯用）")
    _log.append("=" * 50)
    _log.append(f"資料夾：{ROOT}")
    _log.append(f"Python：{sys.version}")
    for args in (["git", "--version"],
                 ["git", "remote", "-v"],
                 ["git", "status", "--short", "--branch"],
                 ["git", "branch", "-a"],
                 ["git", "log", "--oneline", "-3"],
                 ["gh", "--version"],
                 ["gh", "auth", "status"]):
        if not have(args[0]):
            _log.append(f"    $ {' '.join(args)}  → 沒有安裝")
            continue
        try:
            run(args, quiet=True)
        except Exception as exc:
            _log.append(f"    $ {' '.join(args)}  → 例外：{exc}")


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
        inited = run(["git", "init"], quiet=True)
        if inited.returncode != 0:
            say("  ✗ git init 失敗：")
            for line in ((inited.stderr or "") + (inited.stdout or "")).splitlines()[:8]:
                say(f"      {line}")
            diagnose()
            return False
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

    added = run(["git", "add", "-A"], quiet=True)
    if added.returncode != 0:
        say("  ✗ git add 失敗：")
        for line in ((added.stderr or "") + (added.stdout or "")).splitlines()[:8]:
            say(f"      {line}")
        diagnose()
        return False
    say()
    return True


# ---------------------------------------------------------------- 確認內容

RISKY = ("config.json", ".env", ".key", "select-debug.log",
         "DIAGNOSTIC", "SELECT-DEBUG", "GITHUB-LOG")


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
                       "--source=.", "--push"], quiet=True)
        if created.returncode != 0:
            detail = ((created.stderr or "") + (created.stdout or "")).strip()
            say()
            say("  ✗ 建立失敗。GitHub 回報的原因：")
            say()
            for line in detail.splitlines()[:12]:
                say(f"      {line}")
            say()
            if "already exists" in detail or "Name already" in detail:
                say("    你的帳號下已經有一個叫 QTrans 的存放庫了。")
                say("    要嘛去 GitHub 把舊的刪掉或改名，要嘛改用更新指令：")
                say("        git remote add origin https://github.com/<你的帳號>/QTrans.git")
                say("        git push -u origin main")
            elif "GraphQL" in detail or "403" in detail:
                say("    權限不足。執行 gh auth login 重新登入一次，")
                say("    登入時記得勾選 repo 權限。")
            diagnose()
            return False
        say(created.stdout or "")

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

    # 沒有 gh：引導手動建立。
    #
    # 這條路徑原本有個洞：它問完帳號就直接 push，完全沒確認網頁上那個
    # 存放庫真的建好了。使用者只要漏掉「按 Create repository」那一下，
    # push 就會以 "Repository not found" 失敗，而錯誤訊息裡看不出
    # 真正的原因是「存放庫根本不存在」。現在 push 前一定先驗一次。
    say("  沒有 GitHub CLI，所以存放庫要你自己在網頁上建一個空的。")
    say()

    # 如果之前已經設過 remote，沿用它的帳號，不要再問一次。
    existing = run(["git", "remote", "get-url", "origin"], quiet=True)
    account = ""
    if existing.returncode == 0:
        url = existing.stdout.strip()
        parts = url.rstrip("/").replace(".git", "").split("/")
        if len(parts) >= 2:
            account = parts[-2]
            say(f"  （沿用上次設定的帳號：{account}）")
            say()

    if not account:
        try:
            account = input("  先輸入你的 GitHub 帳號名稱：").strip()
        except Exception:
            return False
        if not account:
            say("  沒有輸入帳號，停在這裡。")
            return False
        say()

    kind = "public" if visibility == "--public" else "private"
    new_url = f"https://github.com/new?name={REPO_NAME}&visibility={kind}"
    say("  接下來請在瀏覽器裡建立存放庫：")
    say()
    say(f"  1. 正在開啟建立頁面（名稱與公開設定已經幫你填好）")
    say("  2. 下面三個勾選框（README、.gitignore、license）全部不要勾")
    say("  3. 按最下面綠色的「Create repository」")
    say()
    say("  ※ 一定要真的按到那顆按鈕。只開頁面不按，存放庫不會存在，")
    say("    等一下上傳就會失敗。")
    say()
    try:
        os.startfile(new_url)
    except Exception:
        say(f"     網址：{new_url}")
    say()
    try:
        input("  建好之後，回到這裡按 Enter 繼續…")
    except Exception:
        return False

    remote = f"https://github.com/{account}/{REPO_NAME}.git"
    run(["git", "remote", "remove", "origin"], quiet=True)
    run(["git", "remote", "add", "origin", remote], quiet=True)
    run(["git", "branch", "-M", "main"], quiet=True)

    # 先問 GitHub：這個存放庫到底存不存在。
    say()
    say("  正在確認存放庫是否已經建立……")
    probe = run(["git", "ls-remote", remote], quiet=True)
    if probe.returncode != 0:
        detail = ((probe.stderr or "") + (probe.stdout or "")).strip()
        say()
        say(f"  ✗ GitHub 上找不到 {account}/{REPO_NAME}")
        say()
        for line in detail.splitlines()[:6]:
            say(f"      {line}")
        say()
        say("    最可能的原因：剛才的建立頁面沒有按到「Create repository」。")
        say(f"    請到 https://github.com/{account}?tab=repositories 確認，")
        say("    建好之後再執行一次這個檔案（前面的步驟會自動跳過）。")
        diagnose()
        return False
    say("  ✓ 存放庫在")
    say()
    say("  正在上傳……（可能會跳出 GitHub 登入視窗）")
    say()
    pushed = run(["git", "push", "-u", "origin", "main"], quiet=True)
    if pushed.returncode != 0:
        detail = ((pushed.stderr or "") + (pushed.stdout or "")).strip()
        say()
        say("  ✗ 上傳失敗。git 回報的原因：")
        say()
        for line in detail.splitlines()[:12]:
            say(f"      {line}")
        say()
        if "not found" in detail.lower() or "repository not found" in detail.lower():
            say("    GitHub 上找不到這個存放庫。可能是帳號名稱打錯，")
            say("    或剛才建立時沒按到 Create repository。")
        elif "rejected" in detail.lower() or "non-fast-forward" in detail.lower():
            say("    建立存放庫時勾了 README，兩邊對不起來。執行：")
            say("        git push -u origin main --force")
        elif "authentication" in detail.lower() or "denied" in detail.lower():
            say("    登入失敗。再執行一次，登入視窗出現時用瀏覽器登入。")
        diagnose()
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
    # 任何沒預期到的錯誤都要留在畫面上，而不是讓視窗一閃而過。
    # 之前就是這樣：main() 拋例外 → 下面的 input() 根本沒機會執行 →
    # 使用者只看到一串來不及讀的字。
    try:
        code = main()
    except KeyboardInterrupt:
        say()
        say("  已中斷，什麼都沒有上傳。")
        code = 1
    except Exception:
        code = 1
        say()
        say("=" * 58)
        say("  程式出錯了")
        say("=" * 58)
        say()
        for line in traceback.format_exc().splitlines():
            say(f"  {line}")
        say()
        try:
            diagnose()
        except Exception:
            pass

    write_log()
    say()
    say(f"  完整過程已寫入：{LOG_PATH}")
    if code != 0:
        say("  請把這個檔案的內容回報給 Claude。")
    say()
    try:
        input("按 Enter 關閉...")
    except Exception:
        pass
    sys.exit(code)
