"""把翻譯丟到背景執行緒，避免介面卡住。

三層韌性，由內而外：

  1. 單一請求的重試 —— 429／503 等暫時性錯誤在 engines.py 裡指數退避重試。
  2. 引擎備援 —— 重試完還是失敗，就自動換下一個可用引擎（本檔）。
  3. 取消 —— 使用者選了新文字，舊的整串動作立刻停掉。

備援只在「暫時性失敗」時啟動。金鑰打錯（401/403）換幾個引擎都一樣，
那種要直接把原因告訴使用者，不要用備援把問題藏起來。
"""
from __future__ import annotations

import itertools
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import engines

_counter = itertools.count(1)


class TranslateSignals(QObject):
    done = Signal(int, object)      # (request_id, engines.Result)
    failed = Signal(int, str)       # (request_id, 錯誤訊息)
    token = Signal(int, str)        # (request_id, 新增的片段)
    switched = Signal(int, str)     # (request_id, 「正在改用 X…」)


class _TranslateTask(QRunnable):
    def __init__(self, req_id: int, cfg: dict, chain: list[str], text: str,
                 src: str, tgt: str, signals: TranslateSignals,
                 stream: bool, cancel: threading.Event):
        super().__init__()
        self.req_id, self.cfg, self.chain = req_id, cfg, chain
        self.text, self.src, self.tgt = text, src, tgt
        self.signals = signals
        self.stream = stream
        self.cancel = cancel
        self._emitted_any_token = False

    # ------------------------------------------------------------------
    def run(self) -> None:
        # [(引擎名稱, 錯誤)]，全部失敗時一起回報，使用者才知道每一家各出了什麼事
        errors: list[tuple[str, Exception]] = []

        for index, engine_id in enumerate(self.chain):
            if self.cancel.is_set():
                return

            engine = engines.build(engine_id, self.cfg, self.cancel)
            label = getattr(type(engine), "label", engine_id)

            if index > 0:
                # 串流已經吐過字再換引擎，畫面會變成兩段譯文接在一起
                if self._emitted_any_token:
                    break
                self.signals.switched.emit(self.req_id, f"改用 {label}…")

            try:
                result = self._translate_with(engine)
            except engines.Cancelled:
                return
            except Exception as exc:
                # 不論哪一種錯誤都繼續試下一個引擎。
                # 這裡曾經寫成「不可重試就放棄」，但那個推理是錯的：
                # Gemini 金鑰過期，不代表 DeepL 或 Groq 不能用。結果是主引擎
                # 一回非暫時性錯誤，備援就整串放棄，使用者只看到「翻譯失敗」。
                errors.append((label, exc))
                continue

            if self.cancel.is_set():
                return
            if index > 0 and errors:
                failed_label = errors[0][0]
                result.note = f"{failed_label}無法使用，已自動改用 {label}"
            self.signals.done.emit(self.req_id, result)
            return

        if not self.cancel.is_set():
            self.signals.failed.emit(self.req_id, self._describe(errors))

    @staticmethod
    def _describe(errors: list[tuple[str, Exception]]) -> str:
        """把每個引擎的失敗原因列出來。

        只講第一個引擎的話，使用者會以為換個引擎就好；全部列出來才看得出
        是「這家壞了」還是「網路整個不通」。
        """
        if not errors:
            return "翻譯失敗"
        if len(errors) == 1:
            return str(errors[0][1])
        lines = ["所有引擎都無法完成翻譯：", ""]
        for label, exc in errors:
            lines.append(f"・{label}：{exc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _translate_with(self, engine: engines.Engine) -> engines.Result:
        if self.stream and engine.supports_stream:
            result = engine.translate_stream(
                self.text, self.src, self.tgt,
                on_token=self._emit_token, cancel=self.cancel)
        else:
            result = engine.translate(self.text, self.src, self.tgt)
        if not result.text:
            raise engines.EngineError("引擎回傳空白譯文。", retryable=True)
        return result

    def _emit_token(self, piece: str) -> None:
        if not self.cancel.is_set():
            self._emitted_any_token = True
            self.signals.token.emit(self.req_id, piece)


class Translator(QObject):
    """對外介面：submit() 回傳 request_id，結果用訊號送回主執行緒。"""

    done = Signal(int, object)
    failed = Signal(int, str)
    token = Signal(int, str)
    switched = Signal(int, str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._signals = TranslateSignals(self)
        self._signals.done.connect(self.done)
        self._signals.failed.connect(self.failed)
        self._signals.token.connect(self.token)
        self._signals.switched.connect(self.switched)
        self._cancels: dict[int, threading.Event] = {}

    def submit(self, text: str, engine_id: str, src: str, tgt: str,
               stream: bool | None = None, allow_fallback: bool = True) -> int:
        self.cancel_pending()

        req_id = next(_counter)
        chain = [engine_id]
        if allow_fallback:
            chain += engines.fallback_chain(self.cfg, engine_id)

        if stream is None:
            stream = bool(self.cfg.get("stream_llm", True))

        cancel = threading.Event()
        self._cancels[req_id] = cancel
        QThreadPool.globalInstance().start(
            _TranslateTask(req_id, self.cfg, chain, text, src, tgt,
                           self._signals, stream, cancel)
        )
        return req_id

    def will_stream(self, engine_id: str) -> bool:
        """這個引擎在目前設定下會不會走串流（介面要先清空譯文區）。"""
        if not self.cfg.get("stream_llm", True):
            return False
        cls = engines.ENGINES.get(engine_id)
        return bool(cls and cls.supports_stream)

    def cancel_pending(self) -> None:
        for event in self._cancels.values():
            event.set()
        self._cancels.clear()

    def finish(self, req_id: int) -> None:
        self._cancels.pop(req_id, None)
