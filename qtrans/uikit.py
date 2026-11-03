"""介面共用的小工具。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor

from . import engines as enginesmod

# 停用項目的灰色。深色／淺色主題下都看得出是「不能選」。
DISABLED_COLOR = QColor(128, 128, 128)


def fill_engine_combo(box, cfg: dict, current: str = "",
                      mark_unavailable: bool = True) -> list[tuple[str, str, str]]:
    """把引擎清單填進下拉選單，沒設定好的項目灰掉不給選。

    光靠 item.setEnabled(False) 在某些佈景（尤其深色主題）看起來跟正常項目
    幾乎一樣，所以同時做三件事：停用、塗成灰色、名稱前加鎖頭圖示。
    回傳 engines.listing() 的結果，呼叫端可以繼續用。
    """
    rows = enginesmod.listing(cfg)
    box.blockSignals(True)
    box.clear()
    model = box.model()

    for eid, label, reason in rows:
        text = f"🔒 {label}（未設定）" if reason else label
        box.addItem(text, eid)
        idx = box.count() - 1
        if reason:
            box.setItemData(idx, reason, Qt.ToolTipRole)
            if mark_unavailable:
                box.setItemData(idx, QBrush(DISABLED_COLOR), Qt.ForegroundRole)
                item = model.item(idx) if hasattr(model, "item") else None
                if item is not None:
                    item.setEnabled(False)

    if current:
        at = box.findData(current)
        if at >= 0:
            box.setCurrentIndex(at)
    box.blockSignals(False)
    return rows


def first_available_engine(cfg: dict, preferred: str = "") -> str:
    """回傳目前真的能用的引擎 id，優先用 preferred。"""
    rows = enginesmod.listing(cfg)
    usable = [eid for eid, _label, reason in rows if not reason]
    if preferred and preferred in usable:
        return preferred
    return usable[0] if usable else "google_free"
