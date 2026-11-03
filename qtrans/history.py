"""翻譯歷史紀錄（SQLite，純標準函式庫）。"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .config import HISTORY_PATH, CONFIG_DIR


@dataclass
class Entry:
    rowid: int
    ts: float
    source: str
    result: str
    src_lang: str
    tgt_lang: str
    engine: str


class History:
    def __init__(self, max_items: int = 1000):
        self.max_items = max_items
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(HISTORY_PATH), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS history (
                   ts REAL, source TEXT, result TEXT,
                   src_lang TEXT, tgt_lang TEXT, engine TEXT)"""
        )
        self._conn.commit()

    def add(self, source: str, result: str, src_lang: str, tgt_lang: str, engine: str) -> None:
        if not (source or "").strip() or not (result or "").strip():
            return
        self._conn.execute(
            "INSERT INTO history (ts, source, result, src_lang, tgt_lang, engine)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), source, result, src_lang, tgt_lang, engine),
        )
        self._conn.execute(
            "DELETE FROM history WHERE rowid NOT IN "
            "(SELECT rowid FROM history ORDER BY ts DESC LIMIT ?)",
            (self.max_items,),
        )
        self._conn.commit()

    def recent(self, limit: int = 200, keyword: str = "") -> list[Entry]:
        sql = ("SELECT rowid, ts, source, result, src_lang, tgt_lang, engine FROM history")
        args: list = []
        if keyword.strip():
            sql += " WHERE source LIKE ? OR result LIKE ?"
            like = f"%{keyword.strip()}%"
            args += [like, like]
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [Entry(*row) for row in self._conn.execute(sql, args).fetchall()]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM history")
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
