"""滑鼠旁的即時翻譯彈出視窗（對應 QTranslate 的 Ctrl+Q）。"""
from __future__ import annotations

import webbrowser
from urllib.parse import quote

from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from . import config as cfgmod
from . import uikit


class SelectionBadge(QWidget):
    """選取文字後浮現在滑鼠旁的小圖示，點下去才翻譯。

    這是「選取即翻譯」的預設模式。比起一選到字就彈出整個譯文視窗，
    先給一個小圖示安靜得多 —— 你在編輯文件、寫程式時框選文字的次數
    遠多於想翻譯的次數。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        # 絕對不能搶焦點，否則會把使用者剛選好的文字取消掉
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(30, 30)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.button = QToolButton()
        self.button.setObjectName("badgeButton")
        self.button.setText("譯")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setToolTip("翻譯選取的文字")
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.clicked.connect(self._on_click)
        lay.addWidget(self.button)

        self.setStyleSheet("""
        QToolButton#badgeButton {
            background: #2563eb; color: white; border: none;
            border-radius: 8px; font-size: 13pt; font-weight: bold;
        }
        QToolButton#badgeButton:hover { background: #1d4ed8; }
        """)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def pop_at_cursor(self, lifetime_ms: int = 3000) -> None:
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(geo.left(), pos.x() + 12), geo.right() - self.width())
        y = min(max(geo.top(), pos.y() + 12), geo.bottom() - self.height())
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self._timer.start(lifetime_ms)

    def _on_click(self) -> None:
        self._timer.stop()
        self.hide()
        self.clicked.emit()


class PopupWindow(QWidget):
    """無邊框、永遠置頂、失去焦點自動收起的小視窗。"""

    retranslate_requested = Signal(str, str)   # (engine_id, target_lang)
    speak_requested = Signal(bool)             # True=朗讀原文，False=朗讀譯文
    open_main_requested = Signal(str, str)     # (原文, 譯文)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.cfg = cfg
        self.pinned = False
        self._source_text = ""
        self._result_text = ""
        self._streaming = False
        self._stream_buf = ""
        self._last_height = -1
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._build_ui()
        self._apply_style()

    # ------------------------------------------------------------ 介面
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.frame = QFrame(self)
        self.frame.setObjectName("popupFrame")
        outer.addWidget(self.frame)

        root = QVBoxLayout(self.frame)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # --- 上列：引擎 / 目標語言 / 釘選 / 關閉
        top = QHBoxLayout()
        top.setSpacing(6)

        self.engine_box = QComboBox()
        self.engine_box.setFocusPolicy(Qt.NoFocus)
        self.engine_box.currentIndexChanged.connect(self._on_engine_changed)
        top.addWidget(self.engine_box, 1)

        self.lang_box = QComboBox()
        self.lang_box.setFocusPolicy(Qt.NoFocus)
        for code, label in cfgmod.LANGUAGES:
            if code == "auto":
                continue
            self.lang_box.addItem(label, code)
        self.lang_box.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self.lang_box, 1)

        self.pin_btn = QToolButton()
        self.pin_btn.setText("📌")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("釘選（不自動關閉）")
        self.pin_btn.setFocusPolicy(Qt.NoFocus)
        self.pin_btn.toggled.connect(self._on_pin)
        top.addWidget(self.pin_btn)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.clicked.connect(self.hide)
        top.addWidget(close_btn)

        root.addLayout(top)

        # --- 原文（可收合）
        self.source_label = QLabel()
        self.source_label.setObjectName("sourceLabel")
        self.source_label.setWordWrap(True)
        self.source_label.setMaximumHeight(52)
        root.addWidget(self.source_label)

        # --- 譯文
        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setFrameShape(QFrame.NoFrame)
        self.result_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        root.addWidget(self.result_edit, 1)

        # --- 字典 / 轉寫
        self.extra_label = QLabel()
        self.extra_label.setObjectName("extraLabel")
        self.extra_label.setWordWrap(True)
        self.extra_label.hide()
        root.addWidget(self.extra_label)

        # --- 旁註（例如「Gemini 忙碌，已自動改用 DeepL」）
        self.note_label = QLabel()
        self.note_label.setObjectName("noteLabel")
        self.note_label.setWordWrap(True)
        self.note_label.hide()
        root.addWidget(self.note_label)

        # --- 引擎快速切換列（一鍵換引擎並立刻重譯）
        self.engine_bar = QHBoxLayout()
        self.engine_bar.setSpacing(3)
        self.engine_bar.addStretch(1)
        root.addLayout(self.engine_bar)
        self._engine_buttons: dict[str, QToolButton] = {}

        # --- 下列：動作
        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        # 狀態文字自成一行，底下的按鈕才有完整寬度可用
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        bottom.addStretch(1)

        for text, tip, slot in (
            ("複製", "複製譯文", self._copy_result),
            ("原音", "朗讀原文", lambda: self.speak_requested.emit(True)),
            ("譯音", "朗讀譯文", lambda: self.speak_requested.emit(False)),
            ("搜尋", "用瀏覽器搜尋原文", self._web_search),
            ("主視窗", "在主視窗中開啟", self._open_main),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            bottom.addWidget(btn)

        root.addLayout(bottom)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(160)
        self._hide_timer.timeout.connect(self._maybe_hide)

    def _apply_style(self) -> None:
        popup_cfg = self.cfg.get("popup", {})
        size = int(popup_cfg.get("font_size", 13))
        self.setFixedWidth(int(popup_cfg.get("width", 440)))

        font = QFont()
        font.setPointSize(size)
        self.result_edit.setFont(font)

        self.setStyleSheet(f"""
        #popupFrame {{
            background: palette(window);
            border: 1px solid rgba(128,128,128,0.55);
            border-radius: 10px;
        }}
        #sourceLabel {{ color: palette(mid); font-size: {max(9, size - 3)}pt; }}
        #extraLabel  {{ color: palette(mid); font-size: {max(9, size - 3)}pt; }}
        #statusLabel {{ color: palette(mid); font-size: {max(8, size - 4)}pt; }}
        #noteLabel   {{ color: #b45309; font-size: {max(8, size - 4)}pt; }}
        QTextEdit    {{ background: transparent; }}
        QPushButton  {{ padding: 2px 9px; }}
        QToolButton#engineChip {{
            min-width: 24px; padding: 1px 6px; border-radius: 5px;
            border: 1px solid rgba(128,128,128,0.35);
            font-size: {max(8, size - 3)}pt;
        }}
        QToolButton#engineChip:checked {{
            background: palette(highlight); color: palette(highlighted-text);
            border-color: palette(highlight);
        }}
        QToolButton#engineChip:disabled {{ color: rgba(128,128,128,0.5); }}
        """)

    # ------------------------------------------------------------ 狀態同步
    # 圖示列用的短標記。純文字符號，不需要外部圖檔。
    ENGINE_GLYPHS = {
        "google_free": "G",
        "deepl": "D",
        "gemini": "✦",
        "openai": "◎",
        "libretranslate": "L",
    }

    def refresh_engines(self, current_engine: str) -> None:
        current_engine = uikit.first_available_engine(self.cfg, current_engine)
        rows = uikit.fill_engine_combo(self.engine_box, self.cfg, current_engine)
        self._rebuild_engine_bar(rows, current_engine)

    def _rebuild_engine_bar(self, rows, current_engine: str) -> None:
        """重建底部的引擎快速切換列。"""
        while self.engine_bar.count():
            item = self.engine_bar.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._engine_buttons.clear()

        hint = QLabel("引擎")
        hint.setObjectName("statusLabel")
        self.engine_bar.addWidget(hint)

        for eid, label, reason in rows:
            btn = QToolButton()
            btn.setObjectName("engineChip")
            btn.setText(self.ENGINE_GLYPHS.get(eid, label[:1]))
            btn.setCheckable(True)
            btn.setChecked(eid == current_engine)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"{label}\n{reason}" if reason else f"切換到 {label}")
            if reason:
                btn.setEnabled(False)
            btn.clicked.connect(lambda _checked=False, e=eid: self._pick_engine(e))
            self.engine_bar.addWidget(btn)
            self._engine_buttons[eid] = btn

        self.engine_bar.addStretch(1)

    def _pick_engine(self, engine_id: str) -> None:
        """點圖示切換引擎：同步下拉選單並立刻重譯。"""
        for eid, btn in self._engine_buttons.items():
            btn.setChecked(eid == engine_id)
        idx = self.engine_box.findData(engine_id)
        if idx >= 0 and idx != self.engine_box.currentIndex():
            self.engine_box.setCurrentIndex(idx)   # 這會觸發重譯
        elif self._source_text:
            self.retranslate_requested.emit(engine_id, self.current_target())

    def mark_engine(self, engine_id: str) -> None:
        """備援機制換了引擎時，把圖示列的高亮也跟著移過去。"""
        for eid, btn in self._engine_buttons.items():
            btn.setChecked(eid == engine_id)
        idx = self.engine_box.findData(engine_id)
        if idx >= 0:
            self.engine_box.blockSignals(True)
            self.engine_box.setCurrentIndex(idx)
            self.engine_box.blockSignals(False)

    def set_target_lang(self, code: str) -> None:
        self.lang_box.blockSignals(True)
        idx = self.lang_box.findData(code)
        if idx >= 0:
            self.lang_box.setCurrentIndex(idx)
        self.lang_box.blockSignals(False)

    def current_engine(self) -> str:
        return self.engine_box.currentData() or "google_free"

    def current_target(self) -> str:
        return self.lang_box.currentData() or "zh-TW"

    def result_text(self) -> str:
        """目前顯示的譯文（串流中則是已收到的部分）。"""
        return self._result_text or self._stream_buf

    # ------------------------------------------------------------ 顯示內容
    def show_loading(self, source_text: str) -> None:
        self._source_text = source_text
        self._result_text = ""
        self._streaming = False
        self._stream_buf = ""
        if self.cfg.get("popup", {}).get("show_source", True):
            self.source_label.setText(self._elide(source_text, 180))
            self.source_label.show()
        else:
            self.source_label.hide()
        self.result_edit.setPlainText("翻譯中…")
        self.extra_label.hide()
        self.note_label.hide()
        self.status_label.setText("")
        self._resize_to_content()

    def show_switching(self, message: str) -> None:
        """備援啟動時的即時提示，讓使用者知道沒有卡住。"""
        self.note_label.setText(message)
        self.note_label.show()
        if not self._streaming:
            self.result_edit.setPlainText("翻譯中…")
        self._resize_to_content()

    def append_token(self, piece: str) -> None:
        """串流模式：把新收到的片段接到譯文後面。"""
        if not self._streaming:
            self._streaming = True
            self._stream_buf = ""
            self.result_edit.clear()
        self._stream_buf += piece
        self.result_edit.setPlainText(self._stream_buf)
        self.result_edit.moveCursor(QTextCursor.End)
        self._resize_to_content()

    def show_result(self, result) -> None:
        self._streaming = False
        self._result_text = result.text
        # 串流時畫面上已有逐字內容，這裡用清理過的最終版覆蓋（通常完全相同，不會閃動）
        self.result_edit.setPlainText(result.text)

        extras = []
        if result.transliteration:
            extras.append(f"拼音／轉寫：{result.transliteration}")
        for pos, terms in (result.dictionary or [])[:4]:
            label = pos or "其他"
            extras.append(f"{label}：{'、'.join(terms[:6])}")
        if extras:
            self.extra_label.setText("\n".join(extras))
            self.extra_label.show()
        else:
            self.extra_label.hide()

        if result.note:
            self.note_label.setText("ℹ " + result.note)
            self.note_label.show()
        else:
            self.note_label.hide()
        if result.engine_id:
            self.mark_engine(result.engine_id)

        detected = cfgmod.lang_label(result.detected_lang) if result.detected_lang else "—"
        self.status_label.setText(f"{detected} → {cfgmod.lang_label(result.target_lang)}　·　{result.engine}")
        self._resize_to_content()

        if self.cfg.get("popup", {}).get("copy_result_automatically"):
            self._copy_result(silent=True)

    def show_error(self, message: str) -> None:
        self._streaming = False
        self.result_edit.setPlainText(f"⚠ 翻譯失敗\n\n{message}")
        self.extra_label.hide()
        self.status_label.setText("可在上方切換其他引擎重試")
        self._resize_to_content()

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= limit else flat[:limit] + "…"

    def _resize_to_content(self) -> None:
        doc = self.result_edit.document()
        doc.setTextWidth(self.result_edit.viewport().width() or self.width() - 40)
        wanted = int(doc.size().height()) + 14
        max_h = int(self.cfg.get("popup", {}).get("max_height", 420))
        height = max(46, min(wanted, max_h))
        # 串流時這個函式每收到一段就會被呼叫，高度沒變就不要重排版面
        if height == self._last_height:
            return
        self._last_height = height
        self.result_edit.setFixedHeight(height)
        self.adjustSize()

    # ------------------------------------------------------------ 位置
    def show_at_cursor(self) -> None:
        self.adjustSize()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()

        x, y = pos.x() + 14, pos.y() + 18
        if x + self.width() > geo.right():
            x = pos.x() - self.width() - 14
        if y + self.height() > geo.bottom():
            y = pos.y() - self.height() - 18
        x = max(geo.left(), min(x, geo.right() - self.width()))
        y = max(geo.top(), min(y, geo.bottom() - self.height()))

        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ 事件
    def event(self, ev):
        if ev.type() == QEvent.WindowDeactivate:
            if self.cfg.get("popup", {}).get("close_on_focus_lost", True) and not self.pinned:
                self._hide_timer.start()
        return super().event(ev)

    def _maybe_hide(self) -> None:
        if self.pinned or self.isActiveWindow():
            return
        if QApplication.activePopupWidget() is not None:   # 下拉選單展開中
            return
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
            return
        if ev.key() in (Qt.Key_C,) and ev.modifiers() & Qt.ControlModifier:
            self._copy_result()
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_from = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton and getattr(self, "_drag_from", None):
            self.move(ev.globalPosition().toPoint() - self._drag_from)
        super().mouseMoveEvent(ev)

    # ------------------------------------------------------------ 動作
    def _on_pin(self, checked: bool) -> None:
        self.pinned = checked
        self.pin_btn.setToolTip("已釘選，不會自動關閉" if checked else "釘選（不自動關閉）")

    def _on_engine_changed(self, _idx: int) -> None:
        if self._source_text:
            self.retranslate_requested.emit(self.current_engine(), self.current_target())

    def _on_lang_changed(self, _idx: int) -> None:
        if self._source_text:
            self.retranslate_requested.emit(self.current_engine(), self.current_target())

    def _copy_result(self, silent: bool = False) -> None:
        if not self._result_text:
            return
        QApplication.clipboard().setText(self._result_text)
        if not silent:
            self.status_label.setText("已複製譯文")

    def _web_search(self) -> None:
        if not self._source_text:
            return
        template = self.cfg.get("web_search_url", "https://www.google.com/search?q={q}")
        webbrowser.open(template.replace("{q}", quote(self._source_text[:400])))

    def _open_main(self) -> None:
        self.open_main_requested.emit(self._source_text, self._result_text)
        self.hide()
