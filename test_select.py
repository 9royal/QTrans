"""選取即翻譯的獨立診斷工具。

完全不依賴 QTrans 的設定，直接測兩件事：
  1. 低階滑鼠監聽能不能裝上、框選能不能被偵測到
  2. 偵測到之後，要等多久才取得到選取的文字

用的是 QTrans 自己的 capture 模組，所以測出來的就是實際行為。
"""
from __future__ import annotations

import ctypes
import io
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

buf = io.StringIO()


def say(line: str = "") -> None:
    print(line, flush=True)
    buf.write(line + "\n")


def finish(code: int = 0) -> None:
    try:
        (ROOT / "SELECT-DEBUG.txt").write_text(buf.getvalue(), encoding="utf-8")
        print()
        print(f"報告已寫入：{ROOT / 'SELECT-DEBUG.txt'}")
        print("請回對話告訴 Claude「跑好了」。")
    except Exception as exc:
        print(f"報告寫入失敗：{exc}")
    print()
    try:
        input("按 Enter 關閉...")
    except Exception:
        pass
    sys.exit(code)


say("選取即翻譯 診斷工具")
say("=" * 56)
say(f"平台：{sys.platform}　Python {sys.version.split()[0]}")
say()

if sys.platform != "win32":
    say("這個工具只能在 Windows 上執行。")
    finish(1)

try:
    from qtrans import capture
except Exception as exc:
    say(f"無法載入 qtrans.capture：{exc}")
    finish(1)

# ---------------------------------------------------------------- 掛鉤
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
HC_ACTION = 0
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]

# 不設 restype 的話，64 位元的 module handle 會被截成 32 位元，
# SetWindowsHookExW 會回報 ERROR_MOD_NOT_FOUND（126）。
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

state = {"hook": None, "down": None, "events": 0, "drags": 0, "thread_id": None}
drag_signal = threading.Event()
drag_info = {}


def hook_proc(n_code, w_param, l_param):
    try:
        if n_code == HC_ACTION:
            state["events"] += 1
            if w_param == WM_LBUTTONDOWN:
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                state["down"] = (ms.pt.x, ms.pt.y)
            elif w_param == WM_LBUTTONUP and state["down"] is not None:
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x0, y0 = state["down"]
                state["down"] = None
                dist = max(abs(ms.pt.x - x0), abs(ms.pt.y - y0))
                if dist >= 12 and not drag_signal.is_set():
                    state["drags"] += 1
                    drag_info["dist"] = dist
                    drag_signal.set()
    except Exception:
        pass
    return user32.CallNextHookEx(state["hook"], n_code, w_param, l_param)


_ref = HOOKPROC(hook_proc)


def message_loop():
    state["thread_id"] = kernel32.GetCurrentThreadId()
    module = kernel32.GetModuleHandleW(None)
    state["hook"] = user32.SetWindowsHookExW(WH_MOUSE_LL, _ref, module, 0)
    if not state["hook"]:
        # 低階掛鉤不需要注入 DLL，hMod 傳 NULL 也合法，再試一次
        state["hook"] = user32.SetWindowsHookExW(WH_MOUSE_LL, _ref, None, 0)
    if not state["hook"]:
        state["error"] = ctypes.get_last_error()
        ready.set()
        return
    ready.set()
    msg = wintypes.MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret in (0, -1):
            break
    if state["hook"]:
        user32.UnhookWindowsHookEx(state["hook"])


ready = threading.Event()
say("[1/3] 安裝滑鼠監聽")
thread = threading.Thread(target=message_loop, daemon=True)
thread.start()
ready.wait(timeout=5)

if not state["hook"]:
    say(f"  ✗ 失敗（錯誤碼 {state.get('error', '?')}）")
    say()
    say("  低階滑鼠監聽被系統拒絕，常見原因：")
    say("   ・防毒軟體或端點防護把它當成鍵盤側錄擋下")
    say("   ・企業或學校的群組原則禁止")
    say("  熱鍵翻譯不受影響，但「選取即翻譯」在這台機器上無法運作。")
    finish(1)

say("  ✓ 成功裝上")
say()

# ---------------------------------------------------------------- 等待框選
say("[2/3] 請現在切換到任何程式，用滑鼠框選一段英文文字")
say()
say("      建議先用「記事本」測試：開記事本 → 打一行英文 → 用滑鼠拖曳選取")
say("      （不要按 Ctrl+C，只要框選就好）")
say()
say("      等待中，最多 45 秒……")
say()

got = drag_signal.wait(timeout=45)

if not got:
    say("  ✗ 45 秒內沒有偵測到任何框選動作")
    say()
    say(f"  期間收到的滑鼠事件數：{state['events']}")
    if state["events"] == 0:
        say("  一個事件都沒收到 —— 掛鉤雖然裝上了，但系統沒有把事件送過來。")
        say("  這通常是防毒軟體在攔截，或是權限問題：")
        say("   ・若你用管理員權限開著某個程式，在那個程式裡的操作不會被看到")
        say("   ・試試看以系統管理員身分執行這個診斷工具")
    else:
        say("  有收到滑鼠事件，但沒有一次符合「框選」的條件。")
        say("  可能是拖曳距離不足 12 px，請試著框選長一點的文字。")
    finish(1)

say(f"  ✓ 偵測到框選！拖曳距離 {drag_info.get('dist')} px")
say()

# ---------------------------------------------------------------- 取字
say("[3/3] 測試不同等待時間下能不能取到文字")
say()
say("      請保持剛才的選取狀態不要動，測試約 5 秒……")
say()
time.sleep(0.3)

results = []
for delay_ms in (50, 150, 300, 500, 800):
    try:
        started = time.time()
        text = capture.get_selection(timeout=1.0, pre_delay=delay_ms / 1000.0)
        took = int((time.time() - started) * 1000)
    except Exception as exc:
        say(f"  {delay_ms:>4} ms → 例外：{type(exc).__name__}: {exc}")
        results.append((delay_ms, None, str(exc)))
        continue

    if text:
        preview = " ".join(text.split())[:50]
        say(f"  {delay_ms:>4} ms → ✓ 取到 {len(text)} 字（耗時 {took} ms）：{preview!r}")
        results.append((delay_ms, text, ""))
    else:
        say(f"  {delay_ms:>4} ms → ✗ 取不到（耗時 {took} ms）")
        results.append((delay_ms, "", ""))
    time.sleep(0.4)

# ---------------------------------------------------------------- 結論
say()
say("=" * 56)
say("[結論]")
say("=" * 56)

ok = [d for d, t, _e in results if t]
if ok:
    say(f"  ✓ 取字成功。最短可用等待時間：{min(ok)} ms")
    say()
    say(f"  請到 QTrans 的 設定 → 選取即翻譯 →「放開滑鼠後等待」")
    say(f"  設成 {max(min(ok) + 100, 200)} ms（留一點餘裕），應該就能正常運作。")
else:
    say("  ✗ 所有等待時間都取不到文字。")
    say()
    say("  掛鉤正常、框選也偵測到了，但 Ctrl+C 複製不到東西。可能原因：")
    say("   ・剛才框選的程式不支援 Ctrl+C（例如某些自繪介面）")
    say("   ・框選的動作其實沒有選到文字（只是拖曳空白處）")
    say("   ・剪貼簿被其他程式鎖住（剪貼簿管理工具、遠端桌面）")
    say()
    say("  請改用「記事本」再測一次：開記事本、打一行英文、用滑鼠拖曳選取。")

say()
say(f"  滑鼠事件總數：{state['events']}　偵測到的框選：{state['drags']}")

if state["thread_id"]:
    user32.PostThreadMessageW(state["thread_id"], 0x0012, 0, 0)
finish(0)
