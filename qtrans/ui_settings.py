"""設定視窗。"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import brand
from . import config as cfgmod
from . import engines as enginesmod
from . import hotkeys as hotkeysmod
from . import keyvault
from . import startup
from . import uikit


KEY_CHOICES = [""] + sorted(hotkeysmod.VK_TABLE.keys(), key=lambda k: (len(k), k))


class HotkeyEditor(QWidget):
    """修飾鍵勾選 + 主鍵下拉。刻意不支援無修飾鍵的熱鍵，避免獨佔常用按鍵。"""

    def __init__(self, binding: dict | None, parent=None):
        super().__init__(parent)
        binding = binding or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.ctrl = QCheckBox("Ctrl")
        self.alt = QCheckBox("Alt")
        self.shift = QCheckBox("Shift")
        self.ctrl.setChecked(bool(binding.get("ctrl", False)))
        self.alt.setChecked(bool(binding.get("alt", False)))
        self.shift.setChecked(bool(binding.get("shift", False)))
        for box in (self.ctrl, self.alt, self.shift):
            lay.addWidget(box)

        self.key = QComboBox()
        for choice in KEY_CHOICES:
            self.key.addItem(choice or "（停用）", choice)
        idx = self.key.findData(str(binding.get("key", "")).upper())
        self.key.setCurrentIndex(max(0, idx))
        lay.addWidget(self.key)
        lay.addStretch(1)

    def value(self) -> dict:
        return {
            "ctrl": self.ctrl.isChecked(),
            "alt": self.alt.isChecked(),
            "shift": self.shift.isChecked(),
            "key": self.key.currentData() or "",
        }


def _scrollable(page: QWidget) -> QScrollArea:
    """把分頁包進捲動區。

    「翻譯引擎」分頁內容很高，在 1080p 或筆電螢幕上會把整個視窗撐到
    超出畫面，底下的「確定／取消」就被擠出螢幕外點不到了。
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(page)
    return area


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QTrans 設定")
        self.cfg = copy.deepcopy(cfg)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        for page, title in ((self._tab_general(), "一般"),
                            (self._tab_hotkeys(), "熱鍵"),
                            (self._tab_selection(), "選取即翻譯"),
                            (self._tab_engines(), "翻譯引擎"),
                            (self._tab_appearance(), "外觀與其他")):
            self.tabs.addTab(_scrollable(page), title)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("確定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # 作者簽名放左下角，和確定／取消同一列。放左邊是因為右邊是
        # 按鈕區，簽名擠在按鈕旁邊會被誤認成第三顆按鈕。
        footer = QHBoxLayout()
        mark = brand.ro_label(18, self)
        if mark is not None:
            footer.addWidget(mark)
        footer.addStretch(1)
        footer.addWidget(buttons)
        root.addLayout(footer)

        # 視窗一律不超過螢幕可用高度，確定鈕永遠看得到
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail is not None:
            self.resize(min(760, int(avail.width() * 0.9)),
                        min(640, int(avail.height() * 0.85)))
            self.setMaximumHeight(avail.height())
        else:
            self.resize(760, 640)

    # ------------------------------------------------------------ 分頁
    def _tab_general(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.engine_box = QComboBox()
        uikit.fill_engine_combo(
            self.engine_box, self.cfg,
            uikit.first_available_engine(self.cfg, self.cfg.get("engine", "")))
        form.addRow("預設引擎", self.engine_box)

        self.src_box = QComboBox()
        for code, label in cfgmod.LANGUAGES:
            self.src_box.addItem(label, code)
        self.src_box.setCurrentIndex(max(0, self.src_box.findData(self.cfg.get("source_lang"))))
        form.addRow("來源語言", self.src_box)

        self.tgt_box = QComboBox()
        self.alt_tgt_box = QComboBox()
        for box in (self.tgt_box, self.alt_tgt_box):
            for code, label in cfgmod.LANGUAGES:
                if code != "auto":
                    box.addItem(label, code)
        self.tgt_box.setCurrentIndex(max(0, self.tgt_box.findData(self.cfg.get("target_lang"))))
        self.alt_tgt_box.setCurrentIndex(max(0, self.alt_tgt_box.findData(self.cfg.get("alt_target_lang"))))
        form.addRow("目標語言", self.tgt_box)

        self.auto_swap = QCheckBox("偵測到的來源語言與目標語言相同時，自動改譯為備用目標語言")
        self.auto_swap.setChecked(bool(self.cfg.get("auto_swap_target", True)))
        form.addRow("", self.auto_swap)
        form.addRow("備用目標語言", self.alt_tgt_box)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(3, 120)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(int(self.cfg.get("timeout", 15)))
        form.addRow("連線逾時", self.timeout_spin)

        self.stream_box = QCheckBox("LLM 引擎逐字輸出（長文不必等整段翻完）")
        self.stream_box.setChecked(bool(self.cfg.get("stream_llm", True)))
        self.stream_box.setToolTip("只影響 Gemini 與 OpenAI 相容端點；其他引擎本來就是一次回傳。")
        form.addRow("", self.stream_box)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 6)
        self.retry_spin.setSuffix(" 次")
        self.retry_spin.setValue(int((self.cfg.get("retry") or {}).get("attempts", 3)))
        self.retry_spin.setToolTip(
            "遇到 429（太頻繁）或 503（過載）時自動重試幾次。\n"
            "每次間隔會逐漸拉長（1 秒、2 秒、4 秒…），這是官方建議的做法。")
        form.addRow("暫時性錯誤重試", self.retry_spin)

        self.fallback_box = QCheckBox("重試後仍失敗時，自動改用其他可用引擎")
        self.fallback_box.setChecked(bool(self.cfg.get("auto_fallback", True)))
        self.fallback_box.setToolTip(
            "例如 Gemini 過載時自動換 DeepL 或 Google，並在彈窗說明換過了。\n"
            "金鑰錯誤不會觸發備援，那種會直接告訴你原因。")
        form.addRow("", self.fallback_box)

        form.addRow("備援順序", self._build_fallback_order())

        self.startup_box = QCheckBox("開機時自動啟動 QTrans")
        self.startup_box.setChecked(startup.is_enabled())
        self.startup_box.setEnabled(startup.IS_WINDOWS)
        form.addRow("", self.startup_box)

        return page

    def _tab_hotkeys(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        note = QLabel(
            "熱鍵由 Windows 獨佔註冊，按下後不會再傳給前景程式。\n"
            "若某組合已被其他程式占用，註冊會失敗，啟動時會在系統匣提示。\n"
            "為避免誤觸，主鍵一定要搭配至少一個修飾鍵。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)

        hk = self.cfg.get("hotkeys", {})
        box = QGroupBox("熱鍵設定")
        form = QFormLayout(box)
        self.hk_popup = HotkeyEditor(hk.get("popup"))
        self.hk_main = HotkeyEditor(hk.get("main"))
        self.hk_speak = HotkeyEditor(hk.get("speak"))
        form.addRow("翻譯選取文字（彈出視窗）", self.hk_popup)
        form.addRow("開啟主視窗", self.hk_main)
        form.addRow("朗讀選取文字", self.hk_speak)
        lay.addWidget(box)

        box2 = QGroupBox("連按兩下 Ctrl")
        form2 = QFormLayout(box2)
        self.double_ctrl = QCheckBox("啟用（連按兩下 Ctrl 開啟主視窗並帶入選取文字）")
        self.double_ctrl.setChecked(bool(hk.get("double_ctrl", True)))
        form2.addRow("", self.double_ctrl)

        self.double_interval = QDoubleSpinBox()
        self.double_interval.setRange(0.15, 1.2)
        self.double_interval.setSingleStep(0.05)
        self.double_interval.setSuffix(" 秒")
        self.double_interval.setValue(float(hk.get("double_ctrl_interval", 0.45)))
        form2.addRow("兩次按下的間隔上限", self.double_interval)
        lay.addWidget(box2)

        lay.addStretch(1)
        return page

    def _build_fallback_order(self) -> QWidget:
        """備援順序清單：由上而下依序嘗試，可用上下鍵調整。"""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        self.order_list = QListWidget()
        self.order_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_list.setMaximumHeight(130)
        self.order_list.setToolTip(
            "主引擎失敗時，由上往下依序嘗試。\n"
            "灰色的是還沒設定好的引擎，會被自動略過。")

        labels = {eid: label for eid, label, _ in enginesmod.listing(self.cfg)}
        reasons = {eid: reason for eid, _, reason in enginesmod.listing(self.cfg)}
        order = list(self.cfg.get("fallback_order") or [])
        for eid in labels:                     # 設定檔沒列到的引擎補在後面
            if eid not in order:
                order.append(eid)

        for eid in order:
            if eid not in labels:
                continue
            item = QListWidgetItem(labels[eid])
            item.setData(Qt.UserRole, eid)
            if reasons.get(eid):
                item.setToolTip(reasons[eid])
                item.setForeground(Qt.gray)
            self.order_list.addItem(item)
        lay.addWidget(self.order_list, 1)

        side = QWidget()
        slay = QVBoxLayout(side)
        slay.setContentsMargins(0, 0, 0, 0)
        up = QPushButton("▲")
        down = QPushButton("▼")
        for b in (up, down):
            b.setFixedWidth(34)
            slay.addWidget(b)
        slay.addStretch(1)
        up.clicked.connect(lambda: self._move_order(-1))
        down.clicked.connect(lambda: self._move_order(1))
        lay.addWidget(side)
        return box

    def _move_order(self, delta: int) -> None:
        row = self.order_list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < self.order_list.count()):
            return
        item = self.order_list.takeItem(row)
        self.order_list.insertItem(new, item)
        self.order_list.setCurrentRow(new)

    def _tab_selection(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        sel = self.cfg.get("select_to_translate", {}) or {}

        note = QLabel(
            "用滑鼠框選文字後自動反應，不必按熱鍵。\n\n"
            "⚠ 請先了解代價：Windows 沒有讀取「他人視窗選取文字」的 API，"
            "所以每次偵測到框選，程式都必須短暫借用剪貼簿一次（送出 Ctrl+C 後再還原）。"
            "如果你常在編輯文件或寫程式時框選文字，這可能造成干擾。\n\n"
            "因此預設是關閉的，而且建議先用「顯示小圖示」模式試試看。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)

        box = QGroupBox("選取即翻譯")
        form = QFormLayout(box)

        self.sel_enabled = QCheckBox("啟用")
        self.sel_enabled.setChecked(bool(sel.get("enabled", False)))
        form.addRow("", self.sel_enabled)

        state = QLabel()
        if sel.get("enabled"):
            if hotkeysmod.IS_WINDOWS:
                state.setText("目前狀態：已啟用。框選一段非中文文字即可看到反應。")
                state.setStyleSheet("color: #15803d;")
            else:
                state.setText("目前狀態：此平台不支援滑鼠監聽。")
                state.setStyleSheet("color: #b45309;")
        else:
            state.setText("目前狀態：未啟用。勾選上方並按確定即可開始，"
                          "也可以直接從系統匣圖示的右鍵選單開關。")
            state.setStyleSheet("color: palette(mid);")
        state.setWordWrap(True)
        form.addRow("", state)

        self.sel_mode = QComboBox()
        self.sel_mode.addItem("顯示小圖示，點了才翻譯（建議）", "icon")
        self.sel_mode.addItem("直接跳出譯文", "translate")
        self.sel_mode.addItem("直接朗讀原文，不翻譯（練發音）", "speak")
        idx = self.sel_mode.findData((sel.get("mode") or "icon"))
        self.sel_mode.setCurrentIndex(max(0, idx))
        form.addRow("反應方式", self.sel_mode)

        self.sel_skip_target = QCheckBox("選到的已經是目標語言就不要反應")
        self.sel_skip_target.setChecked(bool(sel.get("skip_if_target_lang", True)))
        self.sel_skip_target.setToolTip("目標語言設繁中時，框選中文不會跳出來吵。")
        form.addRow("", self.sel_skip_target)

        self.sel_min_chars = QSpinBox()
        self.sel_min_chars.setRange(1, 50)
        self.sel_min_chars.setSuffix(" 字")
        self.sel_min_chars.setValue(int(sel.get("min_chars", 2)))
        form.addRow("最少字數", self.sel_min_chars)

        self.sel_max_chars = QSpinBox()
        self.sel_max_chars.setRange(50, 20000)
        self.sel_max_chars.setSuffix(" 字")
        self.sel_max_chars.setValue(int(sel.get("max_chars", 2000)))
        self.sel_max_chars.setToolTip("超過這個長度就不自動反應（整篇文章通常不是想即時翻譯的對象）。")
        form.addRow("最多字數", self.sel_max_chars)

        self.sel_min_drag = QSpinBox()
        self.sel_min_drag.setRange(4, 200)
        self.sel_min_drag.setSuffix(" px")
        self.sel_min_drag.setValue(int(sel.get("min_drag_px", 12)))
        self.sel_min_drag.setToolTip("滑鼠拖曳距離要超過這個值才算「框選」，避免一般點擊誤觸。")
        form.addRow("最小拖曳距離", self.sel_min_drag)

        self.sel_cooldown = QDoubleSpinBox()
        self.sel_cooldown.setRange(0.0, 5.0)
        self.sel_cooldown.setSingleStep(0.1)
        self.sel_cooldown.setSuffix(" 秒")
        self.sel_cooldown.setValue(float(sel.get("cooldown_seconds", 0.8)))
        self.sel_cooldown.setToolTip("兩次偵測之間的最短間隔，避免連續框選時一直觸發。")
        form.addRow("冷卻時間", self.sel_cooldown)

        self.sel_pre_delay = QSpinBox()
        self.sel_pre_delay.setRange(0, 1000)
        self.sel_pre_delay.setSingleStep(20)
        self.sel_pre_delay.setSuffix(" 毫秒")
        self.sel_pre_delay.setValue(int(sel.get("pre_delay_ms", 180)))
        self.sel_pre_delay.setToolTip(
            "放開滑鼠後要等多久才去取字。\n"
            "瀏覽器、Office、PDF 閱讀器在放開左鍵的瞬間還沒更新完選取狀態，\n"
            "太快去取會什麼都抓不到。若常常框選了卻沒反應，把這個值調高到 300～400。")
        form.addRow("放開滑鼠後等待", self.sel_pre_delay)

        self.sel_debug = QCheckBox("寫入診斷記錄（select-debug.log）")
        self.sel_debug.setChecked(bool(sel.get("debug", False)))
        self.sel_debug.setToolTip(
            "把每次偵測的過程寫到程式資料夾的 select-debug.log，\n"
            "用來查「框選了卻沒反應」是卡在哪一步。查完請關掉。")
        form.addRow("", self.sel_debug)

        lay.addWidget(box)
        lay.addStretch(1)
        return page

    def _tab_engines(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        eng = self.cfg.get("engines", {})

        vault_note = QLabel(f"🔒 金鑰儲存方式：{keyvault.describe_backend()}")
        vault_note.setWordWrap(True)
        vault_note.setStyleSheet("color: palette(mid);")
        lay.addWidget(vault_note)

        if cfgmod.last_decrypt_errors:
            warn = QLabel(
                "⚠ 下列金鑰無法解密（通常是換過電腦或重裝 Windows），請重新輸入：\n"
                + "\n".join(f"・{label}" for label, _ in cfgmod.last_decrypt_errors))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b45309;")
            lay.addWidget(warn)

        # DeepL
        box = QGroupBox("DeepL")
        form = QFormLayout(box)
        self.deepl_key = QLineEdit(eng.get("deepl", {}).get("api_key", ""))
        self.deepl_key.setEchoMode(QLineEdit.Password)
        self.deepl_key.setPlaceholderText("DeepL API 金鑰（免費方案每月 50 萬字元）")
        form.addRow("API 金鑰", self.deepl_key)
        self.deepl_pro = QCheckBox("使用 Pro 端點（api.deepl.com）")
        self.deepl_pro.setChecked(bool(eng.get("deepl", {}).get("pro", False)))
        form.addRow("", self.deepl_pro)
        lay.addWidget(box)

        # Gemini
        box = QGroupBox("Gemini（語意翻譯，長句與學術文字品質較好）")
        form = QFormLayout(box)
        self.gemini_key = QLineEdit(eng.get("gemini", {}).get("api_key", ""))
        self.gemini_key.setEchoMode(QLineEdit.Password)
        form.addRow("API 金鑰", self.gemini_key)

        model_row = QWidget()
        mlay = QHBoxLayout(model_row)
        mlay.setContentsMargins(0, 0, 0, 0)
        self.gemini_model = QLineEdit(eng.get("gemini", {}).get("model", "gemini-3.8-flash"))
        mlay.addWidget(self.gemini_model, 1)
        list_btn = QPushButton("列出可用模型")
        list_btn.clicked.connect(self._list_gemini_models)
        mlay.addWidget(list_btn)
        form.addRow("模型", model_row)
        lay.addWidget(box)

        # OpenAI 相容
        box = QGroupBox("OpenAI 相容端點（官方 API、OpenRouter、Ollama、LM Studio…）")
        form = QFormLayout(box)
        oa = eng.get("openai", {})
        self.openai_base = QLineEdit(oa.get("base_url", "https://api.openai.com/v1"))
        self.openai_base.setPlaceholderText("例如 http://localhost:11434/v1")
        form.addRow("端點網址", self.openai_base)
        self.openai_key = QLineEdit(oa.get("api_key", ""))
        self.openai_key.setEchoMode(QLineEdit.Password)
        self.openai_key.setPlaceholderText("本地模型可留空")
        form.addRow("API 金鑰", self.openai_key)
        self.openai_model = QLineEdit(oa.get("model", "gpt-4o-mini"))
        form.addRow("模型", self.openai_model)
        lay.addWidget(box)

        # LibreTranslate
        box = QGroupBox("LibreTranslate（開源，可自行架設）")
        form = QFormLayout(box)
        lt = eng.get("libretranslate", {})
        self.lt_url = QLineEdit(lt.get("url", "https://libretranslate.com"))
        form.addRow("伺服器網址", self.lt_url)
        self.lt_key = QLineEdit(lt.get("api_key", ""))
        self.lt_key.setEchoMode(QLineEdit.Password)
        form.addRow("API 金鑰", self.lt_key)
        lay.addWidget(box)

        # 提示詞
        box = QGroupBox("LLM 翻譯提示詞（Gemini / OpenAI 相容端點共用）")
        blay = QVBoxLayout(box)
        hint = QLabel("可用變數：{text} 代表原文、{target_name} 代表目標語言名稱。")
        hint.setStyleSheet("color: palette(mid);")
        blay.addWidget(hint)
        self.prompt_edit = QPlainTextEdit(
            eng.get("gemini", {}).get("prompt") or cfgmod.DEFAULT_LLM_PROMPT)
        self.prompt_edit.setMinimumHeight(120)
        blay.addWidget(self.prompt_edit)
        reset_btn = QPushButton("還原預設提示詞")
        reset_btn.clicked.connect(lambda: self.prompt_edit.setPlainText(cfgmod.DEFAULT_LLM_PROMPT))
        blay.addWidget(reset_btn, 0, Qt.AlignLeft)
        lay.addWidget(box)

        lay.addStretch(1)
        return page

    def _tab_appearance(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        popup = self.cfg.get("popup", {})

        self.popup_width = QSpinBox()
        self.popup_width.setRange(260, 1200)
        self.popup_width.setSuffix(" px")
        self.popup_width.setValue(int(popup.get("width", 440)))
        form.addRow("彈出視窗寬度", self.popup_width)

        self.popup_max_h = QSpinBox()
        self.popup_max_h.setRange(120, 1200)
        self.popup_max_h.setSuffix(" px")
        self.popup_max_h.setValue(int(popup.get("max_height", 420)))
        form.addRow("譯文區最大高度", self.popup_max_h)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)
        self.font_size.setSuffix(" pt")
        self.font_size.setValue(int(popup.get("font_size", 13)))
        form.addRow("字型大小", self.font_size)

        self.show_source = QCheckBox("在彈出視窗中一併顯示原文")
        self.show_source.setChecked(bool(popup.get("show_source", True)))
        form.addRow("", self.show_source)

        self.close_on_blur = QCheckBox("點到其他視窗時自動收起（可用 📌 釘選）")
        self.close_on_blur.setChecked(bool(popup.get("close_on_focus_lost", True)))
        form.addRow("", self.close_on_blur)

        self.auto_copy = QCheckBox("翻譯完成後自動把譯文放進剪貼簿")
        self.auto_copy.setChecked(bool(popup.get("copy_result_automatically", False)))
        form.addRow("", self.auto_copy)

        tts = self.cfg.get("tts", {})
        self.tts_enabled = QCheckBox("啟用朗讀功能")
        self.tts_enabled.setChecked(bool(tts.get("enabled", True)))
        form.addRow("", self.tts_enabled)

        self.tts_volume = QDoubleSpinBox()
        self.tts_volume.setRange(0.0, 1.0)
        self.tts_volume.setSingleStep(0.1)
        self.tts_volume.setValue(float(tts.get("volume", 0.9)))
        form.addRow("朗讀音量", self.tts_volume)

        self.tts_auto_source = QCheckBox("翻譯完成後自動朗讀「原文」")
        self.tts_auto_source.setChecked(bool(tts.get("auto_speak_source", False)))
        self.tts_auto_source.setToolTip(
            "看著譯文、同時聽原文發音。練聽力或跟讀時很好用。")
        form.addRow("", self.tts_auto_source)

        hist = self.cfg.get("history", {})
        self.history_enabled = QCheckBox("記錄翻譯歷史")
        self.history_enabled.setChecked(bool(hist.get("enabled", True)))
        form.addRow("", self.history_enabled)

        self.history_max = QSpinBox()
        self.history_max.setRange(50, 20000)
        self.history_max.setValue(int(hist.get("max_items", 1000)))
        form.addRow("歷史筆數上限", self.history_max)

        self.search_url = QLineEdit(self.cfg.get("web_search_url", ""))
        self.search_url.setPlaceholderText("以 {q} 代表查詢字串")
        form.addRow("網頁搜尋網址", self.search_url)

        return page

    # ------------------------------------------------------------ 動作
    def _list_gemini_models(self) -> None:
        probe = copy.deepcopy(self.cfg)
        probe.setdefault("engines", {}).setdefault("gemini", {})["api_key"] = self.gemini_key.text().strip()
        engine = enginesmod.Gemini(probe)
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            names = engine.list_models()
        except Exception as exc:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "無法取得模型清單", str(exc))
            return
        QGuiApplication.restoreOverrideCursor()
        if not names:
            QMessageBox.information(self, "模型清單", "這組金鑰目前沒有可用的模型。")
            return
        current = self.gemini_model.text().strip()
        pre = names.index(current) if current in names else 0
        choice, ok = QInputDialog.getItem(self, "選擇模型", "可用模型：", names, pre, False)
        if ok and choice:
            self.gemini_model.setText(choice)

    # ------------------------------------------------------------ 輸出
    def result_config(self) -> dict:
        cfg = self.cfg
        cfg["engine"] = self.engine_box.currentData()
        cfg["source_lang"] = self.src_box.currentData()
        cfg["target_lang"] = self.tgt_box.currentData()
        cfg["alt_target_lang"] = self.alt_tgt_box.currentData()
        cfg["auto_swap_target"] = self.auto_swap.isChecked()
        cfg["timeout"] = self.timeout_spin.value()
        cfg["stream_llm"] = self.stream_box.isChecked()

        retry = dict(cfg.get("retry") or {})
        retry["attempts"] = self.retry_spin.value()
        cfg["retry"] = retry
        cfg["auto_fallback"] = self.fallback_box.isChecked()
        cfg["fallback_order"] = [
            self.order_list.item(i).data(Qt.UserRole)
            for i in range(self.order_list.count())
        ]

        cfg["select_to_translate"] = {
            "enabled": self.sel_enabled.isChecked(),
            "mode": self.sel_mode.currentData() or "icon",
            "min_chars": self.sel_min_chars.value(),
            "max_chars": self.sel_max_chars.value(),
            "min_drag_px": self.sel_min_drag.value(),
            "cooldown_seconds": self.sel_cooldown.value(),
            "skip_if_target_lang": self.sel_skip_target.isChecked(),
            "pre_delay_ms": self.sel_pre_delay.value(),
            "debug": self.sel_debug.isChecked(),
        }

        cfg["hotkeys"] = {
            "popup": self.hk_popup.value(),
            "main": self.hk_main.value(),
            "speak": self.hk_speak.value(),
            "double_ctrl": self.double_ctrl.isChecked(),
            "double_ctrl_interval": self.double_interval.value(),
        }

        prompt = self.prompt_edit.toPlainText().strip() or cfgmod.DEFAULT_LLM_PROMPT
        cfg["engines"] = {
            "deepl": {"api_key": self.deepl_key.text().strip(), "pro": self.deepl_pro.isChecked()},
            "gemini": {
                "api_key": self.gemini_key.text().strip(),
                "model": self.gemini_model.text().strip() or "gemini-3.8-flash",
                "prompt": prompt,
            },
            "openai": {
                "api_key": self.openai_key.text().strip(),
                "base_url": self.openai_base.text().strip(),
                "model": self.openai_model.text().strip() or "gpt-4o-mini",
                "prompt": prompt,
            },
            "libretranslate": {
                "url": self.lt_url.text().strip(),
                "api_key": self.lt_key.text().strip(),
            },
        }

        cfg["popup"] = {
            "width": self.popup_width.value(),
            "max_height": self.popup_max_h.value(),
            "font_size": self.font_size.value(),
            "show_source": self.show_source.isChecked(),
            "close_on_focus_lost": self.close_on_blur.isChecked(),
            "copy_result_automatically": self.auto_copy.isChecked(),
        }
        cfg["tts"] = {
            "enabled": self.tts_enabled.isChecked(),
            "volume": self.tts_volume.value(),
            "auto_speak_source": self.tts_auto_source.isChecked(),
        }
        cfg["history"] = {"enabled": self.history_enabled.isChecked(),
                          "max_items": self.history_max.value()}
        cfg["web_search_url"] = self.search_url.text().strip() or "https://www.google.com/search?q={q}"

        wanted = self.startup_box.isChecked()
        if startup.IS_WINDOWS and wanted != startup.is_enabled():
            ok, message = startup.set_enabled(wanted)
            if not ok:
                QMessageBox.warning(self, "開機啟動設定失敗", message)
        cfg["start_with_windows"] = wanted
        return cfg
