"""全域熱鍵。

Windows 走原生 API，這點很重要：
  * RegisterHotKey  —— 作業系統「獨佔」該組合鍵，按下後不會再傳給前景程式。
    若改用一般的鍵盤監聽套件，按 Ctrl+Q 會同時觸發翻譯「和」原程式的 Ctrl+Q
    （很多程式是結束），這正是多數自製翻譯小工具難用的原因。
  * WH_KEYBOARD_LL 低階掛鉤 —— 只用來偵測「連按兩下 Ctrl」。
    單按 Ctrl 本來就沒有作用，所以這裡「不」攔截按鍵，一律往下傳遞。

兩者在同一條執行緒的訊息迴圈中運作（掛鉤的回呼必須有訊息迴圈才會被呼叫）。
"""
from __future__ import annotations

import ctypes
import string
import sys
import threading
import time
from typing import Callable

IS_WINDOWS = sys.platform == "win32"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000

# 可用於熱鍵的按鍵 -> 虛擬鍵碼
VK_TABLE: dict[str, int] = {ch: ord(ch) for ch in string.ascii_uppercase}
VK_TABLE.update({str(d): 0x30 + d for d in range(10)})
VK_TABLE.update({
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "`": 0xC0, "\\": 0xDC, "[": 0xDB, "]": 0xDD,
})


def describe(binding: dict | None) -> str:
    """把設定裡的熱鍵描述成 'Ctrl+Shift+Q' 這種人看得懂的字串。"""
    if not binding or not (binding.get("key") or "").strip():
        return "（停用）"
    parts = []
    if binding.get("ctrl"):
        parts.append("Ctrl")
    if binding.get("alt"):
        parts.append("Alt")
    if binding.get("shift"):
        parts.append("Shift")
    if binding.get("win"):
        parts.append("Win")
    parts.append(str(binding["key"]).upper())
    return "+".join(parts)


def _to_vk(binding: dict) -> int | None:
    return VK_TABLE.get(str(binding.get("key", "")).upper().strip())


def _to_mods(binding: dict) -> int:
    mods = 0
    if binding.get("ctrl"):
        mods |= MOD_CONTROL
    if binding.get("alt"):
        mods |= MOD_ALT
    if binding.get("shift"):
        mods |= MOD_SHIFT
    if binding.get("win"):
        mods |= MOD_WIN
    return mods


# ---------------------------------------------------------------- Windows
if IS_WINDOWS:
    from ctypes import wintypes   # 只有 Windows 才有這個模組

    LRESULT = ctypes.c_ssize_t
    ULONG_PTR = ctypes.c_size_t

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
    WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
    WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
    VK_LCONTROL, VK_RCONTROL, VK_CONTROL = 0xA2, 0xA3, 0x11
    HC_ACTION = 0

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = LRESULT
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostThreadMessageW.restype = wintypes.BOOL

    # 這兩行不能省。ctypes 沒指定 restype 時預設是 c_int（32 位元），
    # 而 64 位元 Windows 的 module handle 是 64 位元指標 —— 回傳值會被
    # 攔腰截斷成無效的 handle，SetWindowsHookExW 收到後回報
    # ERROR_MOD_NOT_FOUND（126），掛鉤就永遠裝不上。
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def _install_hook(hook_id: int, proc) -> int:
        """安裝低階掛鉤。

        WH_KEYBOARD_LL 與 WH_MOUSE_LL 的掛鉤程序就在本行程內，不需要注入
        DLL，所以 hMod 傳目前的模組或 NULL 都可以。先用模組 handle，
        失敗再退回 NULL，兩條路都走不通才真的是被系統擋下。
        """
        module = _kernel32.GetModuleHandleW(None)
        handle = _user32.SetWindowsHookExW(hook_id, proc, module, 0)
        if not handle:
            handle = _user32.SetWindowsHookExW(hook_id, proc, None, 0)
        return handle

    class HotkeyManager:
        """在自己的執行緒裡註冊熱鍵並跑訊息迴圈。"""

        def __init__(self, on_action: Callable[[str], None]):
            self._on_action = on_action
            self._thread: threading.Thread | None = None
            self._thread_id: int | None = None
            self._ready = threading.Event()
            self._bindings: dict[str, dict] = {}
            self._double_ctrl = False
            self._interval = 0.45
            self._hook = None
            self._hook_ref = None       # 必須保留參考，否則回呼會被 GC
            self._mouse_hook = None
            self._mouse_hook_ref = None
            self._id_to_action: dict[int, str] = {}
            self.failed: list[str] = []  # 註冊失敗（通常是被其他程式占用）
            self.mouse_hook_active = False   # 滑鼠監聽是否真的裝上了

            # 雙擊 Ctrl 的狀態機
            self._ctrl_down = False
            self._other_key_since_ctrl = False
            self._last_ctrl_tap = 0.0

            # 選取即翻譯（滑鼠拖曳）的狀態機
            self._watch_mouse = False
            self._min_drag = 12
            self._cooldown = 0.8
            self._drag_from: tuple[int, int] | None = None
            self._last_select_fire = 0.0

        # ------------------------------------------------ 對外
        def start(self, bindings: dict, double_ctrl: bool, interval: float,
                  watch_mouse: bool = False, min_drag_px: int = 12,
                  cooldown: float = 0.8) -> None:
            self.stop()
            self._bindings = bindings or {}
            self._double_ctrl = bool(double_ctrl)
            self._interval = float(interval or 0.45)
            self._watch_mouse = bool(watch_mouse)
            self._min_drag = max(4, int(min_drag_px))
            self._cooldown = max(0.0, float(cooldown))
            self.failed = []
            self.mouse_hook_active = False
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="qtrans-hotkeys", daemon=True)
            self._thread.start()
            self._ready.wait(timeout=3.0)

        def stop(self) -> None:
            if self._thread and self._thread.is_alive() and self._thread_id:
                _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
                self._thread.join(timeout=2.0)
            self._thread = None
            self._thread_id = None

        def reload(self, bindings: dict, double_ctrl: bool, interval: float,
                   watch_mouse: bool = False, min_drag_px: int = 12,
                   cooldown: float = 0.8) -> None:
            self.start(bindings, double_ctrl, interval, watch_mouse,
                       min_drag_px, cooldown)

        # ------------------------------------------------ 內部
        def _run(self) -> None:
            self._thread_id = _kernel32.GetCurrentThreadId()
            self._id_to_action = {}

            hotkey_id = 1
            for action, binding in self._bindings.items():
                if not isinstance(binding, dict):
                    continue
                vk = _to_vk(binding)
                mods = _to_mods(binding)
                if vk is None or mods == 0:
                    continue  # 沒設按鍵、或沒有修飾鍵（太容易誤觸）就跳過
                if _user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                    self._id_to_action[hotkey_id] = action
                    hotkey_id += 1
                else:
                    self.failed.append(f"{action}（{describe(binding)}）")

            if self._double_ctrl:
                self._hook_ref = HOOKPROC(self._hook_proc)
                self._hook = _install_hook(WH_KEYBOARD_LL, self._hook_ref)
                if not self._hook:
                    self.failed.append(
                        f"連按兩下 Ctrl 的鍵盤監聽（錯誤碼 {ctypes.get_last_error()}）")

            if self._watch_mouse:
                self._mouse_hook_ref = HOOKPROC(self._mouse_proc)
                self._mouse_hook = _install_hook(WH_MOUSE_LL, self._mouse_hook_ref)
                self.mouse_hook_active = bool(self._mouse_hook)
                if not self._mouse_hook:
                    self.failed.append(
                        f"選取即翻譯的滑鼠監聽（錯誤碼 {ctypes.get_last_error()}）")

            self._ready.set()

            msg = wintypes.MSG()
            while True:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    action = self._id_to_action.get(int(msg.wParam))
                    if action:
                        self._fire(action)

            for hid in self._id_to_action:
                _user32.UnregisterHotKey(None, hid)
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            if self._mouse_hook:
                _user32.UnhookWindowsHookEx(self._mouse_hook)
                self._mouse_hook = None
            self._hook_ref = None
            self._mouse_hook_ref = None

        def _mouse_proc(self, n_code, w_param, l_param):
            """偵測「拖曳選取文字」：左鍵按下後移動一段距離才放開。

            和鍵盤掛鉤一樣，這裡絕不攔截事件，一律往下傳 —— 攔了滑鼠會
            讓整台電腦的點擊失效。回呼內只做座標比對，耗時的取字工作
            交給上層丟到背景執行緒。
            """
            try:
                if n_code == HC_ACTION:
                    if w_param == WM_LBUTTONDOWN:
                        ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        self._drag_from = (ms.pt.x, ms.pt.y)
                    elif w_param == WM_LBUTTONUP and self._drag_from is not None:
                        ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        x0, y0 = self._drag_from
                        self._drag_from = None
                        dist = max(abs(ms.pt.x - x0), abs(ms.pt.y - y0))
                        now = time.time()
                        if (dist >= self._min_drag
                                and now - self._last_select_fire >= self._cooldown):
                            self._last_select_fire = now
                            self._fire("mouse_select")
            except Exception:
                pass
            return _user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)

        def _hook_proc(self, n_code, w_param, l_param):
            # 這個回呼必須「快」，否則 Windows 會因逾時把掛鉤拆掉。
            # 這裡只做狀態判斷與送出訊號，不做任何耗時工作。
            try:
                if n_code == HC_ACTION:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = kb.vkCode
                    is_ctrl = vk in (VK_LCONTROL, VK_RCONTROL, VK_CONTROL)
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if is_ctrl:
                            if not self._ctrl_down:
                                self._ctrl_down = True
                                self._other_key_since_ctrl = False
                        else:
                            self._other_key_since_ctrl = True
                    elif w_param in (WM_KEYUP, WM_SYSKEYUP) and is_ctrl:
                        self._ctrl_down = False
                        if not self._other_key_since_ctrl:
                            now = time.time()
                            if now - self._last_ctrl_tap <= self._interval:
                                self._last_ctrl_tap = 0.0
                                self._fire("double_ctrl")
                            else:
                                self._last_ctrl_tap = now
                        else:
                            self._last_ctrl_tap = 0.0
            except Exception:
                pass
            # 永遠往下傳，絕不攔截一般按鍵
            return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        def _fire(self, action: str) -> None:
            try:
                self._on_action(action)
            except Exception:
                pass


# ------------------------------------------------------------ 非 Windows 備援
else:
    class HotkeyManager:  # type: ignore[no-redef]
        """非 Windows 的簡易備援，使用 pynput；無法獨佔按鍵。"""

        def __init__(self, on_action: Callable[[str], None]):
            self._on_action = on_action
            self._listener = None
            self.failed: list[str] = []
            self.mouse_hook_active = False   # 此平台不支援，恆為 False

        def start(self, bindings: dict, double_ctrl: bool, interval: float,
                  watch_mouse: bool = False, min_drag_px: int = 12,
                  cooldown: float = 0.8) -> None:
            self.stop()
            try:
                from pynput import keyboard
            except Exception:
                self.failed = ["此平台需要安裝 pynput 才能使用全域熱鍵"]
                return

            combos = {}
            for action, binding in (bindings or {}).items():
                if not isinstance(binding, dict) or not binding.get("key"):
                    continue
                mods = []
                if binding.get("ctrl"):
                    mods.append("<ctrl>")
                if binding.get("alt"):
                    mods.append("<alt>")
                if binding.get("shift"):
                    mods.append("<shift>")
                if binding.get("win"):
                    mods.append("<cmd>")
                if not mods:
                    continue
                combo = "+".join(mods + [str(binding["key"]).lower()])
                combos[combo] = (lambda a=action: self._on_action(a))

            if combos:
                self._listener = keyboard.GlobalHotKeys(combos)
                self._listener.daemon = True
                self._listener.start()

        def stop(self) -> None:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

        def reload(self, bindings: dict, double_ctrl: bool, interval: float,
                   watch_mouse: bool = False, min_drag_px: int = 12,
                   cooldown: float = 0.8) -> None:
            self.start(bindings, double_ctrl, interval, watch_mouse,
                       min_drag_px, cooldown)
