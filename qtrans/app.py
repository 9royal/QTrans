"""程式主體：把熱鍵、選取擷取、翻譯引擎與介面接起來。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import capture
from . import config as cfgmod
from . import engines as enginesmod
from . import hotkeys as hotkeysmod
from . import keyvault
from .history import History
from .icons import app_icon
from .ui_main import MainWindow
from .ui_popup import PopupWindow, SelectionBadge
from .ui_settings import SettingsDialog
from .worker import Translator

_CJK = re.compile(r"[\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")


def guess_lang(text: str) -> str:
    """極簡的本地語言猜測，給不會回報來源語言的 LLM 引擎用。"""
    sample = text[:400]
    if _KANA.search(sample):
        return "ja"
    if _HANGUL.search(sample):
        return "ko"
    letters = [c for c in sample if c.isalpha() or _CJK.match(c)]
    if letters and len(_CJK.findall(sample)) / len(letters) > 0.3:
        return "zh"
    return "en"


def same_lang(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.split("-")[0].lower() == b.split("-")[0].lower()


# --------------------------------------------------------------------------
class _Bridge(QObject):
    """把熱鍵執行緒的事件搬到 Qt 主執行緒。"""
    action = Signal(str)


class _CaptureSignals(QObject):
    captured = Signal(str, str)   # (選取文字, 後續動作)


class _CaptureTask(QRunnable):
    """在背景擷取選取文字，避免主執行緒被剪貼簿輪詢卡住。"""

    def __init__(self, action: str, signals: _CaptureSignals,
                 pre_delay: float = 0.05, allow_clipboard: bool = True):
        super().__init__()
        self.action, self.signals = action, signals
        self.pre_delay = pre_delay
        self.allow_clipboard = allow_clipboard

    def run(self) -> None:
        try:
            if self.allow_clipboard:
                text = capture.get_selection_or_clipboard(pre_delay=self.pre_delay)
            else:
                # 自動偵測框選：不退回剪貼簿（沒抓到就是沒抓到），
                # 也不干擾使用者正在按的鍵
                text = capture.get_selection_passively(pre_delay=self.pre_delay)
        except Exception:
            text = ""
        self.signals.captured.emit(text, self.action)


# --------------------------------------------------------------------------
class QTransApp(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.cfg = cfgmod.load()

        hist_cfg = self.cfg.get("history", {})
        self.history = History(int(hist_cfg.get("max_items", 1000))) if hist_cfg.get("enabled", True) else None

        self.translator = Translator(self.cfg, self)
        self.translator.done.connect(self._on_translation_done)
        self.translator.failed.connect(self._on_translation_failed)
        self.translator.token.connect(self._on_token)
        self.translator.switched.connect(self._on_switched)

        self.speaker = None
        self._settings_dialog = None
        if self.cfg.get("tts", {}).get("enabled", True):
            self._create_speaker()

        self.popup = PopupWindow(self.cfg)
        self.popup.retranslate_requested.connect(self._on_popup_retranslate)
        self.popup.speak_requested.connect(self._on_popup_speak)
        self.popup.open_main_requested.connect(self._on_popup_to_main)

        # 選取即翻譯用的小圖示
        self.badge = SelectionBadge()
        self.badge.clicked.connect(self._on_badge_clicked)
        self._badge_text = ""

        self.main = MainWindow(self.cfg, self.history)
        self.main.translate_requested.connect(self._on_main_translate)
        self.main.speak_requested.connect(self._speak)
        self.main.settings_requested.connect(self.open_settings)
        self.main.clear_history_requested.connect(lambda: self.history and self.history.clear())

        self._capture_signals = _CaptureSignals(self)
        self._capture_signals.captured.connect(self._on_captured)

        self._bridge = _Bridge(self)
        self._bridge.action.connect(self._on_hotkey_action)
        self.hotkeys = hotkeysmod.HotkeyManager(self._bridge.action.emit)

        # 追蹤請求來源，避免舊結果蓋掉新結果
        self._pending: dict[int, str] = {}        # req_id -> "popup" / "main"
        self._last_source = ""
        self._swap_tried = False

        self._build_tray()
        self._start_hotkeys()
        self._warn_about_unreadable_keys()

    def _warn_about_unreadable_keys(self) -> None:
        """金鑰解不開時明確告知。

        DPAPI 綁 Windows 使用者帳號，換電腦或重裝系統後舊密文一定解不開。
        這不是程式壞了，但一定要讓使用者知道該重新輸入，否則他只會看到
        「尚未設定金鑰」卻想不起來自己早就填過。
        """
        errors = cfgmod.last_decrypt_errors
        if not errors:
            return
        lines = "\n".join(f"・{label}：{msg}" for label, msg in errors)
        QTimer.singleShot(1800, lambda: self.tray.showMessage(
            "有金鑰無法解密，請重新輸入",
            f"{lines}\n\n請到「設定 → 翻譯引擎」重新填入。",
            app_icon(), 9000,
        ))

    # ------------------------------------------------------------ 系統匣
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("QTrans — 桌面即時翻譯")

        menu = QMenu()
        hk = self.cfg.get("hotkeys", {})
        act_popup = menu.addAction(f"翻譯選取文字　{hotkeysmod.describe(hk.get('popup'))}")
        act_popup.triggered.connect(lambda: self._on_hotkey_action("popup"))
        act_main = menu.addAction(f"開啟主視窗　{hotkeysmod.describe(hk.get('main'))}")
        act_main.triggered.connect(self._show_main)
        menu.addSeparator()

        # 直接放在系統匣，開關和現況一眼看得到，不必翻設定
        sel = self.cfg.get("select_to_translate", {}) or {}
        self.act_select = menu.addAction("選取即翻譯（框選文字自動反應）")
        self.act_select.setCheckable(True)
        self.act_select.setChecked(bool(sel.get("enabled", False)))
        self.act_select.toggled.connect(self._toggle_select_to_translate)

        menu.addSeparator()
        menu.addAction("設定…").triggered.connect(self.open_settings)
        menu.addSeparator()
        menu.addAction("結束").triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._show_main()

    def _start_hotkeys(self) -> None:
        hk = self.cfg.get("hotkeys", {})
        bindings = {k: hk.get(k) for k in ("popup", "main", "speak") if isinstance(hk.get(k), dict)}
        sel = self.cfg.get("select_to_translate", {}) or {}
        self.hotkeys.start(
            bindings,
            hk.get("double_ctrl", True),
            hk.get("double_ctrl_interval", 0.45),
            watch_mouse=bool(sel.get("enabled", False)),
            min_drag_px=int(sel.get("min_drag_px", 12)),
            cooldown=float(sel.get("cooldown_seconds", 0.8)),
        )
        if self.hotkeys.failed:
            QTimer.singleShot(1200, lambda: self.tray.showMessage(
                "部分熱鍵註冊失敗",
                "下列熱鍵已被其他程式占用，請到設定中改用別的組合：\n"
                + "\n".join(self.hotkeys.failed),
                app_icon(), 7000,
            ))

    # ------------------------------------------------------------ 熱鍵動作
    def _on_hotkey_action(self, action: str) -> None:
        if action == "double_ctrl":
            action = "main"

        if action == "mouse_select":
            sel = self.cfg.get("select_to_translate", {}) or {}
            delay = max(0.0, float(sel.get("pre_delay_ms", 180)) / 1000.0)
            self._debug_select(f"偵測到框選，等待 {int(delay * 1000)} ms 後取字")
            task = _CaptureTask(action, self._capture_signals,
                                pre_delay=delay, allow_clipboard=False)
        else:
            task = _CaptureTask(action, self._capture_signals)

        QThreadPool.globalInstance().start(task)

    def _debug_select(self, message: str) -> None:
        """把選取即翻譯的過程寫進記錄檔，用來查「框選了卻沒反應」。

        刻意寫在程式資料夾而不是 %APPDATA%，方便直接看、也方便傳給別人看。
        預設關閉，開了才會寫。

        絕對不要把框選到的文字寫進來。使用者一天會框選幾百次，裡面
        什麼都有 —— 密碼、API 金鑰、學生個資 —— 而這個檔案是明文、
        不會過期、而且「方便傳給別人看」。要診斷的是「有沒有取到字」，
        知道長度就夠了，內容是多餘的風險。
        """
        sel = self.cfg.get("select_to_translate", {}) or {}
        if not sel.get("debug"):
            return
        try:
            import time as _time
            log = Path(__file__).resolve().parents[1] / "select-debug.log"
            # 不讓它無限長大。忘了關掉 debug 的話，一個月就是好幾 MB。
            if log.exists() and log.stat().st_size > 512_000:
                log.unlink()
            stamp = _time.strftime("%H:%M:%S")
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {message}\n")
        except Exception:
            pass

    def _on_captured(self, text: str, action: str) -> None:
        text = (text or "").strip()

        if action == "mouse_select":
            self._handle_mouse_selection(text)
            return

        if action == "speak":
            if text:
                self._speak(text, guess_lang(text))
            return

        if action == "main":
            self._show_main()
            if text:
                self.main.load_pair(text, "")
                self._translate(text, self.main.engine_box.currentData(),
                                self.main.src_box.currentData(),
                                self.main.tgt_box.currentData(), origin="main")
            return

        # action == "popup"
        if not text:
            self.tray.showMessage("沒有抓到文字",
                                  "請先用滑鼠選取要翻譯的文字，再按熱鍵。", app_icon(), 3000)
            return

        self.popup.refresh_engines(self.cfg.get("engine", "google_free"))
        self.popup.set_target_lang(self.cfg.get("target_lang", "zh-TW"))
        self.popup.show_loading(text)
        self.popup.show_at_cursor()
        self._translate(text, self.popup.current_engine(),
                        self.cfg.get("source_lang", "auto"),
                        self.popup.current_target(), origin="popup")

    # ------------------------------------------------------ 選取即翻譯
    def _handle_mouse_selection(self, text: str) -> None:
        """滑鼠框選文字後的反應。

        這裡的每一道過濾都是為了「安靜」：使用者框選文字的次數遠多於
        想翻譯的次數，誤跳一次就是一次干擾。
        """
        sel = self.cfg.get("select_to_translate", {}) or {}
        if not sel.get("enabled", False):
            return

        if not text:
            # 實測下來，取不到字絕大多數不是故障：使用者這一次拖曳
            # 根本不是在選文字（拖檔案、拉視窗、調欄寬、拖捲軸都會
            # 觸發掛鉤）。舊版本在這裡建議「調高等待時間」，是誤導。
            self._debug_select(
                "沒有取到文字（多半是這次拖曳本來就不是在選文字，"
                "例如拖檔案、拉視窗、調欄寬）。只有在確定是選文字、"
                "卻一直取不到時，才需要調高『放開滑鼠後等待』。")
            return
        self._debug_select(f"取到 {len(text)} 字")

        if len(text) < int(sel.get("min_chars", 2)):
            self._debug_select("→ 略過：少於最少字數")
            return
        if len(text) > int(sel.get("max_chars", 2000)):
            self._debug_select("→ 略過：超過最多字數")
            return          # 整篇文章通常不是想即時翻譯的對象

        # 選到的已經是目標語言就不要吵（例如中文介面裡隨手框選中文）
        if sel.get("skip_if_target_lang", True):
            if same_lang(guess_lang(text), self.cfg.get("target_lang", "zh-TW")):
                self._debug_select("→ 略過：偵測到的語言就是目標語言")
                return

        # 沒有任何可辨識的文字（純數字、符號、路徑）也跳過
        if not any(ch.isalpha() for ch in text):
            self._debug_select("→ 略過：沒有任何字母")
            return

        self._debug_select(f"→ 執行：{sel.get('mode', 'icon')}")
        self._badge_text = text
        mode = (sel.get("mode", "icon") or "icon").lower()
        if mode == "translate":
            self._translate_selection(text)
        elif mode == "speak":
            self._speak(text, guess_lang(text))
        else:
            self.badge.pop_at_cursor()

    def _on_badge_clicked(self) -> None:
        if self._badge_text:
            self._translate_selection(self._badge_text)

    def _translate_selection(self, text: str) -> None:
        self.popup.refresh_engines(self.cfg.get("engine", "google_free"))
        self.popup.set_target_lang(self.cfg.get("target_lang", "zh-TW"))
        self.popup.show_loading(text)
        self.popup.show_at_cursor()
        self._translate(text, self.popup.current_engine(),
                        self.cfg.get("source_lang", "auto"),
                        self.popup.current_target(), origin="popup")

    # ------------------------------------------------------------ 翻譯流程
    def _resolve_target(self, text: str, engine_id: str, tgt: str) -> str:
        """LLM 引擎的目標語言要在送出前就決定好。

        LLM 不回報來源語言，原本的做法是等譯文回來才發現「原文本來就是目標
        語言」然後整個重譯一次。串流模式下那會變成畫面上的字跑完又全部重跑，
        所以改成先用本地判斷把語言定好，一次就送對。
        """
        if not self.cfg.get("auto_swap_target", True):
            return tgt
        cls = enginesmod.ENGINES.get(engine_id)
        if not (cls and cls.is_llm):
            return tgt      # 其他引擎會回報 detected_lang，仍走事後重試那條路
        if same_lang(guess_lang(text), tgt):
            alt = self.cfg.get("alt_target_lang", "en")
            if not same_lang(alt, tgt):
                return alt
        return tgt

    def _translate(self, text: str, engine_id: str, src: str, tgt: str,
                   origin: str, is_retry: bool = False) -> None:
        if not is_retry:
            self._last_source = text
            self._swap_tried = False

        resolved = self._resolve_target(text, engine_id, tgt)
        is_llm = bool((cls := enginesmod.ENGINES.get(engine_id)) and cls.is_llm)
        if is_llm:
            self._swap_tried = True   # 已在送出前決定語言，不要再事後重譯
        if resolved != tgt:
            if origin == "popup":
                self.popup.set_target_lang(resolved)
            else:
                idx = self.main.tgt_box.findData(resolved)
                if idx >= 0:
                    self.main.tgt_box.setCurrentIndex(idx)

        req_id = self.translator.submit(text, engine_id, src, resolved)
        self._pending[req_id] = origin
        if origin == "main":
            self.main.show_loading()

    def _on_token(self, req_id: int, piece: str) -> None:
        origin = self._pending.get(req_id)
        if origin == "popup":
            self.popup.append_token(piece)
        elif origin == "main":
            self.main.append_token(piece)

    def _on_switched(self, req_id: int, message: str) -> None:
        """備援啟動：讓使用者看到正在換引擎，而不是以為卡住了。"""
        origin = self._pending.get(req_id)
        if origin == "popup":
            self.popup.show_switching(message)
        elif origin == "main":
            self.main.show_switching(message)

    def _on_translation_done(self, req_id: int, result) -> None:
        origin = self._pending.pop(req_id, None)
        self.translator.finish(req_id)
        if origin is None:
            return

        # 自動換目標語言：原文本來就是目標語言時，改譯為備用語言（中英互譯很常用）
        detected = result.detected_lang or guess_lang(result.source_text)
        if (self.cfg.get("auto_swap_target", True) and not self._swap_tried
                and same_lang(detected, result.target_lang)):
            alt = self.cfg.get("alt_target_lang", "en")
            if not same_lang(alt, result.target_lang):
                self._swap_tried = True
                engine_id = (self.popup.current_engine() if origin == "popup"
                             else self.main.engine_box.currentData())
                if origin == "popup":
                    self.popup.set_target_lang(alt)
                else:
                    idx = self.main.tgt_box.findData(alt)
                    if idx >= 0:
                        self.main.tgt_box.setCurrentIndex(idx)
                self._translate(result.source_text, engine_id,
                                self.cfg.get("source_lang", "auto"), alt,
                                origin=origin, is_retry=True)
                return

        if origin == "popup":
            self.popup.show_result(result)
        else:
            self.main.show_result(result)

        if self.history:
            self.history.add(result.source_text, result.text,
                             detected, result.target_lang, result.engine)

        # 對照譯文聽原文發音，練聽力與跟讀時很實用
        if self.cfg.get("tts", {}).get("auto_speak_source") and result.source_text:
            self._speak(result.source_text, detected or guess_lang(result.source_text))

    def _on_translation_failed(self, req_id: int, message: str) -> None:
        origin = self._pending.pop(req_id, None)
        self.translator.finish(req_id)
        if origin == "popup":
            self.popup.show_error(message)
        elif origin == "main":
            self.main.show_error(message)

    # ------------------------------------------------------------ 介面事件
    def _on_popup_retranslate(self, engine_id: str, target: str) -> None:
        if not self._last_source:
            return
        self._swap_tried = True   # 使用者手動指定，不要再自動改語言
        self.popup.show_loading(self._last_source)
        self._translate(self._last_source, engine_id,
                        self.cfg.get("source_lang", "auto"), target,
                        origin="popup", is_retry=True)

    def _on_popup_speak(self, source: bool = True) -> None:
        if source:
            if self._last_source:
                self._speak(self._last_source, guess_lang(self._last_source))
            return
        text = self.popup.result_text()
        if text:
            self._speak(text, self.popup.current_target())

    def _on_popup_to_main(self, source: str, result: str) -> None:
        self._show_main()
        self.main.load_pair(source, result)

    def _on_main_translate(self, text: str, engine_id: str, src: str, tgt: str) -> None:
        self._translate(text, engine_id, src, tgt, origin="main")

    def _speak(self, text: str, lang: str) -> None:
        if self.speaker is None:
            self.tray.showMessage("朗讀無法使用",
                                  "請確認已安裝 PySide6 的多媒體元件，或在設定中關閉朗讀。",
                                  app_icon(), 4000)
            return
        self.speaker.speak(text, lang or "en")

    def _show_main(self) -> None:
        self.main.refresh_engines(self.cfg.get("engine", "google_free"))
        self.main.set_languages(self.cfg.get("source_lang", "auto"),
                                self.cfg.get("target_lang", "zh-TW"))
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    # ------------------------------------------------------------ 設定
    def open_settings(self) -> None:
        existing = getattr(self, "_settings_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        # 刻意用非 modal。modal 的 exec() 會鎖住整個程式的其他視窗，
        # 結果是設定開著時按 Ctrl+Q，彈窗跳出來卻點不動也關不掉，
        # 非得先關設定不可。非 modal 還有個好處：改完提示詞可以立刻試翻譯。
        dialog = SettingsDialog(self.cfg)
        dialog.setModal(False)
        dialog.accepted.connect(lambda d=dialog: self._apply_settings(d))
        dialog.finished.connect(lambda *_: setattr(self, "_settings_dialog", None))
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _apply_settings(self, dialog) -> None:
        new_cfg = dialog.result_config()
        cfgmod.save(new_cfg)

        # 就地更新，讓所有持有這個 dict 參考的元件一起生效
        self.cfg.clear()
        self.cfg.update(new_cfg)
        self._apply_runtime_config()
        self.tray.showMessage("設定已儲存", "所有變更都已立即套用，不需要重新啟動。",
                              app_icon(), 2500)

    def _apply_runtime_config(self) -> None:
        """把設定套用到正在執行的程式。

        目標是「改完就生效，永遠不必重啟」。所以連「朗讀」「歷史紀錄」這種
        會建立或銷毀物件的開關也在這裡處理 —— 之前只更新了音量和筆數上限，
        從關到開並不會真的把功能帶回來，使用者就只好重開程式。
        """
        engine = self.cfg.get("engine", "google_free")

        # --- 朗讀：開關會建立／丟棄 Speaker
        tts_cfg = self.cfg.get("tts", {}) or {}
        if tts_cfg.get("enabled", True):
            if self.speaker is None:
                self._create_speaker()
            if self.speaker is not None:
                self.speaker.set_volume(float(tts_cfg.get("volume", 0.9)))
        elif self.speaker is not None:
            self.speaker.stop()
            self.speaker = None

        # --- 歷史：開關會建立／關閉資料庫
        hist_cfg = self.cfg.get("history", {}) or {}
        if hist_cfg.get("enabled", True):
            if self.history is None:
                try:
                    self.history = History(int(hist_cfg.get("max_items", 1000)))
                except Exception:
                    self.history = None
            else:
                self.history.max_items = int(hist_cfg.get("max_items", 1000))
        elif self.history is not None:
            self.history.close()
            self.history = None
        self.main.history = self.history
        self.main.reload_history()

        # --- 介面
        self.popup._apply_style()
        self.popup.refresh_engines(engine)
        self.main.apply_style()
        self.main.refresh_engines(engine)
        self.main.set_languages(self.cfg.get("source_lang", "auto"),
                                self.cfg.get("target_lang", "zh-TW"))

        # --- 熱鍵與滑鼠監聽
        self._start_hotkeys()
        self._build_tray_menu_labels()

    def _create_speaker(self) -> None:
        try:
            from .tts import Speaker
            self.speaker = Speaker(self, float(self.cfg.get("tts", {}).get("volume", 0.9)))
            self.speaker.error.connect(
                lambda msg: self.tray.showMessage("朗讀失敗", msg, app_icon(), 4000))
        except Exception:
            self.speaker = None   # 缺少 QtMultimedia 時靜默降級

    def _build_tray_menu_labels(self) -> None:
        hk = self.cfg.get("hotkeys", {})
        actions = self.tray.contextMenu().actions()
        if len(actions) >= 2:
            actions[0].setText(f"翻譯選取文字　{hotkeysmod.describe(hk.get('popup'))}")
            actions[1].setText(f"開啟主視窗　{hotkeysmod.describe(hk.get('main'))}")
        act = getattr(self, "act_select", None)
        if act is not None:
            sel = self.cfg.get("select_to_translate", {}) or {}
            act.blockSignals(True)
            act.setChecked(bool(sel.get("enabled", False)))
            act.blockSignals(False)

    MODE_NAMES = {"icon": "顯示小圖示", "translate": "直接翻譯", "speak": "直接朗讀原文"}

    def _toggle_select_to_translate(self, on: bool) -> None:
        """從系統匣直接開關「選取即翻譯」，並明確回報有沒有真的啟動。

        這個功能靠的是低階滑鼠掛鉤，可能被系統或防毒擋下。沒有回報的話，
        使用者只會覺得「開了卻沒反應」，根本無從判斷是設定沒生效還是功能壞了。
        """
        sel = dict(self.cfg.get("select_to_translate") or {})
        sel["enabled"] = bool(on)
        self.cfg["select_to_translate"] = sel
        cfgmod.save(self.cfg)
        self._start_hotkeys()

        if not on:
            self.tray.showMessage("選取即翻譯已關閉",
                                  "回到只用熱鍵翻譯。", app_icon(), 2500)
            return

        if getattr(self.hotkeys, "mouse_hook_active", False):
            mode = self.MODE_NAMES.get(sel.get("mode", "icon"), sel.get("mode", ""))
            self.tray.showMessage(
                "選取即翻譯已開啟",
                f"用滑鼠框選一段非中文文字試試看（反應方式：{mode}）。\n"
                "想改成直接跳譯文或直接朗讀，請到設定 → 選取即翻譯。",
                app_icon(), 5000)
        else:
            self.act_select.blockSignals(True)
            self.act_select.setChecked(False)
            self.act_select.blockSignals(False)
            self.tray.showMessage(
                "選取即翻譯無法啟動",
                "系統拒絕安裝滑鼠監聽，可能被防毒軟體或系統原則擋下。\n"
                "熱鍵翻譯不受影響，仍可正常使用。",
                app_icon(), 7000)

    # ------------------------------------------------------------ 結束
    def quit(self) -> None:
        self.hotkeys.stop()
        self.translator.cancel_pending()
        enginesmod.close_http()
        if self.history:
            self.history.close()
        self.tray.hide()
        self.qapp.quit()


# --------------------------------------------------------------------------
def main() -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("QTrans")
    qapp.setQuitOnLastWindowClosed(False)   # 關掉視窗只是收到系統匣
    qapp.setWindowIcon(app_icon())

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "QTrans", "這台電腦沒有可用的系統匣，程式無法常駐。")
        return 1

    # 單一執行個體
    from PySide6.QtCore import QSharedMemory
    guard = QSharedMemory("QTrans-single-instance-lock")
    if guard.attach():
        guard.detach()
        QMessageBox.information(None, "QTrans", "QTrans 已經在執行中（請看系統匣圖示）。")
        return 0
    if not guard.create(1):
        pass  # 建立失敗就不擋，頂多允許多開

    app = QTransApp(qapp)
    app._guard = guard  # 保留參考，程式結束前不要釋放

    if not app.cfg.get("start_with_windows") or "--show" in sys.argv:
        hk = app.cfg.get("hotkeys", {})
        app.tray.showMessage(
            "QTrans 已啟動",
            f"選取文字後按 {hotkeysmod.describe(hk.get('popup'))} 即可翻譯。",
            app_icon(), 4000,
        )

    return qapp.exec()
