"""主視窗：手動輸入翻譯、比對多引擎、查歷史紀錄。"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QStatusBar, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from . import brand
from . import config as cfgmod
from . import uikit
from .icons import app_icon


class MainWindow(QMainWindow):
    translate_requested = Signal(str, str, str, str)  # (text, engine, src, tgt)
    speak_requested = Signal(str, str)                # (text, lang)
    settings_requested = Signal()
    clear_history_requested = Signal()

    def __init__(self, cfg: dict, history, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.history = history
        self._streaming = False
        self._stream_buf = ""
        self.setWindowTitle("QTrans — 桌面即時翻譯")
        self.setWindowIcon(app_icon())
        self.resize(900, 620)
        self._build_ui()
        self._build_menu()
        self.refresh_engines(cfg.get("engine", "google_free"))
        self.reload_history()

    # ------------------------------------------------------------ 介面
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(8)

        # --- 工具列
        bar = QHBoxLayout()
        bar.setSpacing(6)

        bar.addWidget(QLabel("引擎"))
        self.engine_box = QComboBox()
        self.engine_box.setMinimumWidth(210)
        bar.addWidget(self.engine_box)

        bar.addSpacing(10)
        bar.addWidget(QLabel("來源"))
        self.src_box = QComboBox()
        for code, label in cfgmod.LANGUAGES:
            self.src_box.addItem(label, code)
        bar.addWidget(self.src_box)

        self.swap_btn = QToolButton()
        self.swap_btn.setText("⇄")
        self.swap_btn.setToolTip("對調來源與目標語言（並把譯文移到左邊）")
        self.swap_btn.clicked.connect(self._swap)
        bar.addWidget(self.swap_btn)

        bar.addWidget(QLabel("目標"))
        self.tgt_box = QComboBox()
        for code, label in cfgmod.LANGUAGES:
            if code == "auto":
                continue
            self.tgt_box.addItem(label, code)
        bar.addWidget(self.tgt_box)

        bar.addStretch(1)

        self.translate_btn = QPushButton("翻譯")
        self.translate_btn.setDefault(True)
        self.translate_btn.setToolTip("Ctrl+Enter")
        self.translate_btn.clicked.connect(self._do_translate)
        bar.addWidget(self.translate_btn)

        root.addLayout(bar)

        # --- 文字區 + 歷史
        splitter = QSplitter(Qt.Horizontal)

        texts = QWidget()
        tlay = QVBoxLayout(texts)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(6)

        font = QFont()
        font.setPointSize(int(self.cfg.get("popup", {}).get("font_size", 13)))

        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("在這裡輸入或貼上要翻譯的文字…（Ctrl+Enter 翻譯）")
        self.source_edit.setFont(font)
        tlay.addWidget(self.source_edit, 1)

        row = QHBoxLayout()
        self.status_line = QLabel("")
        self.status_line.setStyleSheet("color: palette(mid);")
        row.addWidget(self.status_line, 1)
        for text, slot in (("複製譯文", self._copy_result),
                           ("朗讀原文", lambda: self._speak(True)),
                           ("朗讀譯文", lambda: self._speak(False))):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        tlay.addLayout(row)

        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setFont(font)
        self.result_edit.setPlaceholderText("譯文會顯示在這裡")
        tlay.addWidget(self.result_edit, 1)

        splitter.addWidget(texts)

        # --- 歷史
        side = QWidget()
        slay = QVBoxLayout(side)
        slay.setContentsMargins(0, 0, 0, 0)
        slay.setSpacing(6)
        slay.addWidget(QLabel("歷史紀錄"))

        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText("搜尋歷史…")
        self.history_filter.textChanged.connect(self.reload_history)
        slay.addWidget(self.history_filter)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self._load_history_item)
        slay.addWidget(self.history_list, 1)

        clear_btn = QPushButton("清空歷史")
        clear_btn.clicked.connect(self._confirm_clear_history)
        slay.addWidget(clear_btn)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 260])
        root.addWidget(splitter, 1)

        status = QStatusBar()
        self.setStatusBar(status)
        # 作者簽名放狀態列最右邊。addPermanentWidget 的好處是暫時訊息
        # （「翻譯中…」之類）不會把它蓋掉，也不會把它擠走。
        mark = brand.ro_label(18, status)
        if mark is not None:
            status.addPermanentWidget(mark)

        act = QAction(self)
        act.setShortcut(QKeySequence("Ctrl+Return"))
        act.triggered.connect(self._do_translate)
        self.addAction(act)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        m_file = menu.addMenu("檔案(&F)")
        act_settings = QAction("設定(&S)…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self.settings_requested)
        m_file.addAction(act_settings)
        m_file.addSeparator()
        act_hide = QAction("隱藏到系統匣(&H)", self)
        act_hide.setShortcut(QKeySequence("Esc"))
        act_hide.triggered.connect(self.hide)
        m_file.addAction(act_hide)

        m_help = menu.addMenu("說明(&H)")
        act_about = QAction("關於 QTrans", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    # ------------------------------------------------------------ 對外
    def refresh_engines(self, current: str) -> None:
        uikit.fill_engine_combo(self.engine_box, self.cfg,
                                uikit.first_available_engine(self.cfg, current))

    def apply_style(self) -> None:
        """套用字型等外觀設定，讓設定改完立刻生效而不必重啟。"""
        font = QFont()
        font.setPointSize(int(self.cfg.get("popup", {}).get("font_size", 13)))
        self.source_edit.setFont(font)
        self.result_edit.setFont(font)

    def set_languages(self, src: str, tgt: str) -> None:
        i = self.src_box.findData(src)
        if i >= 0:
            self.src_box.setCurrentIndex(i)
        j = self.tgt_box.findData(tgt)
        if j >= 0:
            self.tgt_box.setCurrentIndex(j)

    def load_pair(self, source: str, result: str = "") -> None:
        self.source_edit.setPlainText(source)
        self.result_edit.setPlainText(result)

    def show_loading(self) -> None:
        self._streaming = False
        self._stream_buf = ""
        self.result_edit.setPlainText("翻譯中…")
        self.status_line.setText("")
        self.translate_btn.setEnabled(False)

    def show_switching(self, message: str) -> None:
        self.status_line.setText("ℹ " + message)
        if not getattr(self, "_streaming", False):
            self.result_edit.setPlainText("翻譯中…")

    def append_token(self, piece: str) -> None:
        """串流模式：把新收到的片段接到譯文後面。"""
        if not getattr(self, "_streaming", False):
            self._streaming = True
            self._stream_buf = ""
            self.result_edit.clear()
        self._stream_buf += piece
        self.result_edit.setPlainText(self._stream_buf)
        self.result_edit.moveCursor(QTextCursor.End)

    def show_result(self, result) -> None:
        self._streaming = False
        self.translate_btn.setEnabled(True)
        self.result_edit.setPlainText(result.text)
        if getattr(result, "note", ""):
            self.statusBar().showMessage("ℹ " + result.note, 8000)
        detected = cfgmod.lang_label(result.detected_lang) if result.detected_lang else "—"
        extras = []
        if result.transliteration:
            extras.append(f"轉寫：{result.transliteration}")
        for pos, terms in (result.dictionary or [])[:3]:
            extras.append(f"{pos or '其他'}：{'、'.join(terms[:6])}")
        suffix = "　|　" + "　".join(extras) if extras else ""
        self.status_line.setText(
            f"{detected} → {cfgmod.lang_label(result.target_lang)}　·　{result.engine}{suffix}")
        self.reload_history()

    def show_error(self, message: str) -> None:
        self._streaming = False
        self.translate_btn.setEnabled(True)
        self.result_edit.setPlainText(f"⚠ 翻譯失敗\n\n{message}")
        self.status_line.setText("")

    def reload_history(self) -> None:
        if not self.history:
            return
        self.history_list.clear()
        for entry in self.history.recent(200, self.history_filter.text()):
            stamp = time.strftime("%m/%d %H:%M", time.localtime(entry.ts))
            preview = " ".join(entry.source.split())[:34]
            item = QListWidgetItem(f"{stamp}  {preview}")
            item.setToolTip(f"{entry.source[:400]}\n\n→ {entry.result[:400]}")
            item.setData(Qt.UserRole, (entry.source, entry.result))
            self.history_list.addItem(item)

    # ------------------------------------------------------------ 動作
    def _do_translate(self) -> None:
        text = self.source_edit.toPlainText().strip()
        if not text:
            return
        self.translate_requested.emit(
            text, self.engine_box.currentData() or "google_free",
            self.src_box.currentData() or "auto",
            self.tgt_box.currentData() or "zh-TW",
        )

    def _swap(self) -> None:
        src, tgt = self.src_box.currentData(), self.tgt_box.currentData()
        result = self.result_edit.toPlainText().strip()
        if result and not result.startswith("⚠"):
            self.source_edit.setPlainText(result)
            self.result_edit.clear()
        if src != "auto":
            i = self.tgt_box.findData(src)
            if i >= 0:
                self.tgt_box.setCurrentIndex(i)
        j = self.src_box.findData(tgt)
        if j >= 0:
            self.src_box.setCurrentIndex(j)

    def _copy_result(self) -> None:
        text = self.result_edit.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage("已複製譯文", 2000)

    def _speak(self, source: bool) -> None:
        if source:
            text = self.source_edit.toPlainText().strip()
            lang = self.src_box.currentData()
            lang = "en" if lang == "auto" else lang
        else:
            text = self.result_edit.toPlainText().strip()
            lang = self.tgt_box.currentData()
        if text:
            self.speak_requested.emit(text, lang)

    def _load_history_item(self, item: QListWidgetItem) -> None:
        source, result = item.data(Qt.UserRole)
        self.load_pair(source, result)

    def _confirm_clear_history(self) -> None:
        reply = QMessageBox.question(self, "清空歷史", "確定要刪除全部歷史紀錄嗎？此動作無法復原。")
        if reply == QMessageBox.Yes:
            self.clear_history_requested.emit()
            self.reload_history()

    def _about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("關於 QTrans")
        box.setText("QTrans — 類 QTranslate 的桌面即時翻譯工具")
        box.setInformativeText(
            "選取任何視窗中的文字後按下熱鍵，即可在滑鼠旁顯示譯文。\n"
            "支援 Google（免金鑰）、Groq、DeepL、Gemini 與 LibreTranslate。\n\n"
            "作者：Ro")
        bg = box.palette().color(box.backgroundRole())
        pm = brand.ro_pixmap(56, dark=bg.lightness() < 128,
                             ratio=box.devicePixelRatioF())
        if pm is not None:
            box.setIconPixmap(pm)
        else:
            box.setIcon(QMessageBox.Information)
        box.exec()

    def closeEvent(self, ev) -> None:
        # 關閉視窗只收到系統匣，不結束程式
        ev.ignore()
        self.hide()
