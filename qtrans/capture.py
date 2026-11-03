"""擷取「目前選取的文字」。

做法與 QTranslate 相同：先備份剪貼簿 -> 放入哨兵字串 -> 模擬 Ctrl+C
-> 輪詢剪貼簿直到內容改變 -> 取出文字 -> 還原剪貼簿。

重點：熱鍵被 RegisterHotKey 攔下時，使用者手上可能仍按著 Ctrl，
送出 Ctrl+C 前必須先把所有修飾鍵「鬆開」，否則會變成 Ctrl+Shift+C 之類的組合。
"""
from __future__ import annotations

import sys
import time

import pyperclip

IS_WINDOWS = sys.platform == "win32"
SENTINEL = "\x00\x01QTRANS-EMPTY\x01\x00"

if IS_WINDOWS:
    import ctypes

    _user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_LCONTROL, VK_RCONTROL = 0x11, 0xA2, 0xA3
    VK_SHIFT, VK_LSHIFT, VK_RSHIFT = 0x10, 0xA0, 0xA1
    VK_MENU, VK_LMENU, VK_RMENU = 0x12, 0xA4, 0xA5
    VK_LWIN, VK_RWIN = 0x5B, 0x5C
    VK_C = 0x43

    _MODIFIERS = (VK_CONTROL, VK_LCONTROL, VK_RCONTROL, VK_SHIFT, VK_LSHIFT,
                  VK_RSHIFT, VK_MENU, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)

    # restype 一定要宣告：預設的 c_int 會把 c_short 的回傳值解讀錯誤
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short

    def _release_modifiers() -> None:
        for vk in _MODIFIERS:
            _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    def _any_modifier_down() -> bool:
        """使用者此刻是否按著任何修飾鍵。

        用來判斷「他正在用鍵盤做事」。自動取字若在這種時候插進去，
        會把他的按鍵序列打斷 —— 最嚴重的情況是他正要按 Ctrl+C，
        我們卻先送出 Ctrl 的 keyup，於是他的 C 變成單獨的字元輸入，
        把選取的文字整段取代掉。
        """
        for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN):
            if _user32.GetAsyncKeyState(vk) & 0x8000:
                return True
        return False

    def _send_copy() -> None:
        _user32.keybd_event(VK_CONTROL, 0, 0, 0)
        _user32.keybd_event(VK_C, 0, 0, 0)
        _user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

else:  # 非 Windows 的開發備援
    try:
        from pynput.keyboard import Controller, Key

        _kb = Controller()

        def _release_modifiers() -> None:
            for key in (Key.ctrl, Key.ctrl_l, Key.ctrl_r, Key.shift,
                        Key.alt, Key.alt_l, Key.cmd):
                try:
                    _kb.release(key)
                except Exception:
                    pass

        def _send_copy() -> None:
            mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
            with _kb.pressed(mod):
                _kb.press("c")
                _kb.release("c")
        def _any_modifier_down() -> bool:
            return False     # 此平台無法查詢，一律視為沒有按著

    except Exception:  # pragma: no cover - 缺少 pynput 時不致讓程式起不來
        def _release_modifiers() -> None:
            pass

        def _any_modifier_down() -> bool:
            return False

        def _send_copy() -> None:
            raise RuntimeError("此平台缺少可用的按鍵模擬後端（請安裝 pynput）。")


def _read_clipboard() -> str:
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def _write_clipboard(value: str) -> None:
    try:
        pyperclip.copy(value)
    except Exception:
        pass


class UserIsTyping(RuntimeError):
    """使用者正在用鍵盤做事，這次自動取字必須放棄。"""


def get_selection(timeout: float = 0.7, restore: bool = True,
                  pre_delay: float = 0.05, release_modifiers: bool = True,
                  abort_if_typing: bool = False) -> str:
    """送出 Ctrl+C 取得選取文字；沒有選取時回傳空字串。

    pre_delay
        送出 Ctrl+C 之前的等待時間。按熱鍵時使用者早就選好字了，50 毫秒就夠；
        但「滑鼠放開就自動取字」的情況不同 —— 瀏覽器、Office、PDF 閱讀器在
        放開左鍵的瞬間還沒更新完選取狀態，太快送會什麼都複製不到。

    release_modifiers
        送 Ctrl+C 前先把修飾鍵全部鬆開。熱鍵路徑一定要這樣做（使用者手指
        還在 Ctrl+Q 上，不鬆開會送成 Ctrl+Shift+C）；但自動路徑絕對不能，
        因為使用者根本沒按熱鍵，硬送 keyup 只會打斷他正在輸入的組合鍵。

    abort_if_typing
        偵測到使用者正按著修飾鍵就放棄這次取字。自動路徑專用。
    """
    if abort_if_typing and _any_modifier_down():
        raise UserIsTyping()

    backup = _read_clipboard()
    _write_clipboard(SENTINEL)

    if release_modifiers:
        _release_modifiers()
    time.sleep(max(0.0, pre_delay))

    # 等待期間使用者可能才開始按鍵（例如框選完隨即按 Ctrl+C）。
    # 這時插進去會毀掉他的輸入，所以還原剪貼簿後安靜退場。
    if abort_if_typing and _any_modifier_down():
        _write_clipboard(backup)
        raise UserIsTyping()

    try:
        _send_copy()
    except Exception:
        _write_clipboard(backup)
        raise

    text = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.03)
        current = _read_clipboard()
        if current and current != SENTINEL:
            text = current
            break

    if restore:
        _write_clipboard(backup)
    return text.strip()


def get_selection_or_clipboard(timeout: float = 0.7,
                               pre_delay: float = 0.05) -> str:
    """先試選取文字，沒有就退回使用剪貼簿現有內容。

    這個降級只適合「使用者主動按熱鍵」的情境 —— 他按了就是想翻譯某個東西，
    拿剪貼簿裡的內容來頂還算合理。自動偵測框選時千萬別用這個：沒抓到卻拿
    上次複製的舊內容去翻，使用者只會看到莫名其妙的譯文。
    """
    backup = _read_clipboard()
    text = get_selection(timeout=timeout, restore=True, pre_delay=pre_delay)
    if text:
        return text
    return (backup or "").strip()


def get_selection_passively(timeout: float = 0.7, pre_delay: float = 0.18) -> str:
    """自動偵測框選時專用的取字。

    和熱鍵路徑的差別只有兩點，但兩點都攸關「會不會弄壞使用者的東西」：
    不強制鬆開修飾鍵，以及偵測到使用者在按鍵就放棄。
    """
    try:
        return get_selection(timeout=timeout, restore=True, pre_delay=pre_delay,
                             release_modifiers=False, abort_if_typing=True)
    except UserIsTyping:
        return ""
