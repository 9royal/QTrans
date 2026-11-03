"""程式內繪製圖示，免去另外帶二進位檔案。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

ACCENT = "#2563eb"


def app_pixmap(size: int = 64, color: str = ACCENT, glyph: str = "譯") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    radius = size * 0.24
    painter.drawRoundedRect(QRect(1, 1, size - 2, size - 2), radius, radius)

    font = QFont()
    font.setPointSizeF(size * 0.52)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pm.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon


def busy_icon() -> QIcon:
    return QIcon(app_pixmap(64, color="#6b7280", glyph="…"))
