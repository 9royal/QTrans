"""朗讀。

取 Google 翻譯的 TTS 端點（免金鑰），下載後用 Qt 的播放器播。
該端點單次長度上限約 200 字元，所以切塊下載後把 MP3 位元組直接相接
（MP3 以影格為單位，直接串接即可正常播放）。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QUrl, QRunnable, Signal, QThreadPool
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

TTS_URL = "https://translate.google.com/translate_tts"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CHUNK = 180


def _split(text: str, limit: int = CHUNK) -> list[str]:
    words = text.replace("\r", " ").replace("\n", " ").split(" ")
    chunks, buf = [], ""
    for word in words:
        while len(word) > limit:           # 超長單字（例如中文長句沒有空白）直接硬切
            chunks.append(word[:limit])
            word = word[limit:]
        if len(buf) + len(word) + 1 > limit and buf:
            chunks.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}".strip()
    if buf:
        chunks.append(buf)
    return chunks


class _Signals(QObject):
    ready = Signal(int, str)     # (序號, 檔案路徑)
    failed = Signal(int, str)


class _FetchTask(QRunnable):
    def __init__(self, seq: int, text: str, lang: str,
                 folder: Path, signals: _Signals):
        super().__init__()
        self.seq, self.text, self.lang = seq, text, lang
        self.folder, self.signals = folder, signals

    def run(self) -> None:
        try:
            audio = b""
            for part in _split(self.text):
                if not part.strip():
                    continue
                resp = requests.get(
                    TTS_URL,
                    params={"ie": "UTF-8", "client": "tw-ob", "tl": self.lang, "q": part},
                    headers={"User-Agent": UA, "Referer": "https://translate.google.com/"},
                    timeout=15,
                )
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                audio += resp.content
            if not audio:
                raise RuntimeError("沒有取得音訊內容")

            # 每次都用新檔名 —— 原因見 Speaker._play 的說明
            path = self.folder / f"tts-{self.seq}.mp3"
            path.write_bytes(audio)
            self.signals.ready.emit(self.seq, str(path))
        except Exception as exc:
            self.signals.failed.emit(self.seq, str(exc))


class Speaker(QObject):
    """非同步朗讀；同一時間只播最後一次要求。"""

    error = Signal(str)

    def __init__(self, parent=None, volume: float = 0.9):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(max(0.0, min(1.0, volume)))
        self._player.setAudioOutput(self._audio)
        self._signals = _Signals(self)
        self._signals.ready.connect(self._play)
        self._signals.failed.connect(self._on_failed)

        self._seq = 0
        self._folder = Path(tempfile.gettempdir()) / "qtrans-tts"
        try:
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._folder = Path(tempfile.gettempdir())

    def set_volume(self, volume: float) -> None:
        self._audio.setVolume(max(0.0, min(1.0, volume)))

    def speak(self, text: str, lang: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.stop()
        lang = (lang or "en").split("-")[0] if lang not in ("zh-TW", "zh-CN") else lang
        self._seq += 1
        QThreadPool.globalInstance().start(
            _FetchTask(self._seq, text, lang, self._folder, self._signals))

    def stop(self) -> None:
        try:
            self._player.stop()
        except Exception:
            pass

    def _on_failed(self, seq: int, message: str) -> None:
        if seq == self._seq:            # 舊請求的錯誤不用吵使用者
            self.error.emit(message)

    def _play(self, seq: int, path: str) -> None:
        """播放剛下載好的音訊。

        這裡有兩個一定要做的動作，少一個就會播到錯的聲音：

        1. 丟掉過期的請求（seq != self._seq）。先按「朗讀譯文」再按「朗讀原文」
           時，兩個下載同時在跑，誰先回來不一定。

        2. 先 setSource(QUrl()) 清空再設定新檔案。QMediaPlayer 認為「URL 沒變
           就是同一個來源」，若兩次路徑相同，它會直接重播記憶體裡緩衝的舊音訊，
           完全不理會磁碟上的檔案已經換過了 —— 這正是「朗讀原文卻念出剛才那段
           中文譯文」的成因。配合每次換檔名，雙重保險。
        """
        if seq != self._seq:
            return
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self._cleanup(keep=seq)

    def _cleanup(self, keep: int) -> None:
        """刪掉不再需要的暫存音檔，別讓它在暫存資料夾裡無限累積。"""
        try:
            for old in self._folder.glob("tts-*.mp3"):
                try:
                    num = int(old.stem.split("-")[1])
                except (IndexError, ValueError):
                    continue
                if num < keep:
                    try:
                        old.unlink()
                    except OSError:
                        pass        # Windows 上播放中的檔案可能被鎖住，下次再清
        except Exception:
            pass
