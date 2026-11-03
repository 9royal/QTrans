"""翻譯引擎。

新增引擎只要繼承 Engine、實作 translate()，再加上 @register 即可。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import requests

from . import config as cfgmod

# 這些狀態碼代表「現在不行，等一下可能就好了」，應該自動重試。
# 依 Google Gemini API 官方疑難排解指引：只重試暫時性錯誤，
# 用指數退避加隨機抖動，並限制最大次數。
# 400/401/403（金鑰錯、權限不足）重試再多次也沒用，不在此列。
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# 延遲建立的共用 HTTP 連線池
#
# 建立 requests.Session 會載入 TLS 憑證庫，約需數十到數百毫秒。常駐工具若在
# 開機時就做這件事，會被 Windows 標記為高啟動影響；所以推遲到「第一次真的
# 要翻譯」時才建立。之後重複使用同一個連線池，省掉每次重新 TLS 握手。
# --------------------------------------------------------------------------
_session: requests.Session | None = None
_session_lock = threading.Lock()


def http() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                sess = requests.Session()
                sess.headers.update({"User-Agent": UA})
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=4, pool_maxsize=8, max_retries=0)
                sess.mount("https://", adapter)
                sess.mount("http://", adapter)
                _session = sess
    return _session


def close_http() -> None:
    """程式結束時關閉連線池。"""
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
        _session = None


class Cancelled(RuntimeError):
    """使用者已經發出新的翻譯請求，這一筆不必再跑完。"""


@dataclass
class Result:
    """一次翻譯的結果。"""
    text: str = ""
    detected_lang: str = ""
    engine: str = ""
    engine_id: str = ""
    source_text: str = ""
    target_lang: str = ""
    transliteration: str = ""
    # [(詞性, [譯詞, ...]), ...]，只有字典式引擎會填
    dictionary: list = field(default_factory=list)
    # 給使用者看的旁註，例如「Gemini 忙碌，已自動改用 DeepL」
    note: str = ""


class EngineError(RuntimeError):
    """翻譯失敗。retryable 為真代表換個時間或換個引擎可能會成功。"""

    def __init__(self, message: str, retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Engine:
    id = ""
    label = ""
    needs_key = False
    # LLM 類引擎不支援「自動偵測來源語言」以外的細節，但會回報 detected_lang 為空
    is_llm = False
    # 是否支援逐字串流輸出
    supports_stream = False

    def __init__(self, cfg: dict, cancel: threading.Event | None = None):
        self.cfg = cfg
        self.opts = (cfg.get("engines") or {}).get(self.id, {}) or {}
        self.timeout = cfg.get("timeout", 15)
        self.cancel = cancel
        retry_cfg = cfg.get("retry") or {}
        self.retry_attempts = int(retry_cfg.get("attempts", 3))
        self.retry_base = float(retry_cfg.get("base_seconds", 1.0))
        self.retry_cap = float(retry_cfg.get("max_seconds", 8.0))

    # --- 子類別實作 ---
    def translate(self, text: str, src: str, tgt: str) -> Result:  # pragma: no cover
        raise NotImplementedError

    # --- 共用工具 ---
    def unavailable_reason(self) -> str:
        """回傳空字串代表可用，否則回傳不可用的原因。"""
        if self.needs_key and not (self.opts.get("api_key") or "").strip():
            return "尚未設定 API 金鑰"
        return ""

    @staticmethod
    def _chunks(text: str, limit: int) -> list[str]:
        """依段落/句子切塊，避免超過單次請求長度上限。"""
        if len(text) <= limit:
            return [text]
        pieces: list[str] = []
        buf = ""
        # 先按行切，行本身過長再按句尾符號切
        units: list[str] = []
        for line in text.splitlines(keepends=True):
            if len(line) <= limit:
                units.append(line)
            else:
                units.extend(re.findall(r".{1,%d}(?:[。．.!?！？；;]|\s|$)|.{1,%d}" % (limit, limit), line))
        for unit in units:
            if not unit:
                continue
            if len(buf) + len(unit) > limit and buf:
                pieces.append(buf)
                buf = unit
            else:
                buf += unit
        if buf:
            pieces.append(buf)
        return pieces or [text]

    # ---------------------------------------------------------- 重試
    def _wait(self, seconds: float) -> None:
        """可被取消打斷的等待。使用者已經選了新文字就不該還在這裡空等。"""
        if self.cancel is not None:
            if self.cancel.wait(seconds):
                raise Cancelled()
        else:
            time.sleep(seconds)

    def _backoff(self, attempt: int, resp=None) -> float:
        """指數退避加隨機抖動；伺服器若指定了 Retry-After 就聽它的。"""
        if resp is not None:
            hint = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
            if hint:
                try:
                    return max(0.5, min(float(hint), 30.0))
                except (TypeError, ValueError):
                    pass
        delay = min(self.retry_base * (2 ** attempt), self.retry_cap)
        # 抖動：避免多個用戶端在同一刻一起重試，反而把伺服器再壓垮一次
        return delay * (0.75 + random.random() * 0.5)

    def _request(self, method: str, url: str, **kwargs):
        """送出請求，暫時性失敗時自動重試。回傳 Response。"""
        kwargs.setdefault("timeout", self.timeout)
        last: EngineError | None = None

        for attempt in range(self.retry_attempts + 1):
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled()
            try:
                resp = http().request(method, url, **kwargs)
            except requests.RequestException as exc:
                last = EngineError(f"連線失敗：{exc}", retryable=True)
                if attempt < self.retry_attempts:
                    self._wait(self._backoff(attempt))
                    continue
                raise last from exc

            if resp.status_code in RETRYABLE_STATUS and attempt < self.retry_attempts:
                wait = self._backoff(attempt, resp)
                try:
                    resp.close()
                except Exception:
                    pass
                self._wait(wait)
                continue
            return resp

        raise last or EngineError("請求失敗")

    @staticmethod
    def _raise_for_status(resp, label: str = "") -> None:
        if resp.status_code < 400:
            return
        body = ""
        try:
            body = (resp.text or "")[:300]
        except Exception:
            pass
        retryable = resp.status_code in RETRYABLE_STATUS
        if resp.status_code == 429:
            msg = f"{label}請求過於頻繁（429），已自動重試仍未成功。"
        elif resp.status_code == 503:
            msg = f"{label}服務暫時過載（503），已自動重試仍未成功。"
        elif resp.status_code in (401, 403):
            msg = f"{label}金鑰無效或沒有權限（{resp.status_code}）。請檢查 API 金鑰。"
        else:
            msg = f"{label}HTTP {resp.status_code}：{body}"
        raise EngineError(msg, retryable=retryable, status=resp.status_code)

    def _post_json(self, url: str, **kwargs):
        resp = self._request("POST", url, **kwargs)
        self._raise_for_status(resp)
        try:
            return resp.json()
        except ValueError as exc:
            raise EngineError("回應不是合法的 JSON") from exc

    def translate_stream(self, text: str, src: str, tgt: str,
                         on_token: Callable[[str], None],
                         cancel: threading.Event | None = None) -> Result:
        """逐字串流翻譯。不支援串流的引擎退回一次性翻譯。"""
        return self.translate(text, src, tgt)


ENGINES: dict[str, type[Engine]] = {}


def register(cls: type[Engine]) -> type[Engine]:
    ENGINES[cls.id] = cls
    return cls


# --------------------------------------------------------------------------
# Google 翻譯（免金鑰的網頁端點）
# --------------------------------------------------------------------------
@register
class GoogleFree(Engine):
    id = "google_free"
    label = "Google 翻譯（免金鑰）"
    URL = "https://translate.googleapis.com/translate_a/single"
    LIMIT = 1800

    def translate(self, text: str, src: str, tgt: str) -> Result:
        out_parts: list[str] = []
        detected = ""
        translit = ""
        dictionary: list = []

        for idx, chunk in enumerate(self._chunks(text, self.LIMIT)):
            params = [
                ("client", "gtx"),
                ("sl", src or "auto"),
                ("tl", tgt),
                ("dj", "1"),
                ("dt", "t"),
                ("dt", "bd"),
                ("dt", "rm"),
                ("q", chunk),
            ]
            resp = self._request("GET", self.URL, params=params)
            if resp.status_code == 429:
                raise EngineError(
                    "Google 免金鑰端點限制請求（429）。這個端點依對外 IP 限流，"
                    "在學校或公司等共用網路特別容易遇到。建議改用 Gemini 或 DeepL。",
                    retryable=True, status=429)
            self._raise_for_status(resp, "Google 翻譯：")
            try:
                data = resp.json()
            except ValueError as exc:
                raise EngineError("回應格式無法解析") from exc

            sentences = data.get("sentences") or []
            out_parts.append("".join(
                s.get("trans", "") for s in sentences if isinstance(s, dict) and "trans" in s
            ))
            if idx == 0:
                detected = data.get("src", "") or ""
                translit = next(
                    (s.get("translit", "") for s in sentences
                     if isinstance(s, dict) and s.get("translit")), "",
                )
                for entry in (data.get("dict") or []):
                    pos = entry.get("pos", "") or ""
                    terms = entry.get("terms", []) or []
                    if terms:
                        dictionary.append((pos, terms))

        return Result(
            text="".join(out_parts).strip(),
            detected_lang=detected,
            engine=self.label,
            engine_id=self.id,
            source_text=text,
            target_lang=tgt,
            transliteration=translit,
            dictionary=dictionary,
        )


# --------------------------------------------------------------------------
# DeepL
# --------------------------------------------------------------------------
@register
class DeepL(Engine):
    id = "deepl"
    label = "DeepL"
    needs_key = True

    TARGET_MAP = {
        "zh-TW": "ZH-HANT", "zh-CN": "ZH-HANS", "en": "EN-US", "pt": "PT-PT",
    }
    SOURCE_MAP = {"zh-TW": "ZH", "zh-CN": "ZH", "en": "EN", "pt": "PT"}

    def _url(self) -> str:
        host = "api.deepl.com" if self.opts.get("pro") else "api-free.deepl.com"
        return f"https://{host}/v2/translate"

    def translate(self, text: str, src: str, tgt: str) -> Result:
        key = (self.opts.get("api_key") or "").strip()
        if not key:
            raise EngineError("尚未設定 DeepL API 金鑰。")
        payload = {
            "text": [text],
            "target_lang": self.TARGET_MAP.get(tgt, tgt.split("-")[0].upper()),
        }
        if src and src != "auto":
            payload["source_lang"] = self.SOURCE_MAP.get(src, src.split("-")[0].upper())

        data = self._post_json(
            self._url(),
            headers={"Authorization": f"DeepL-Auth-Key {key}",
                     "Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        items = data.get("translations") or []
        if not items:
            raise EngineError("DeepL 未回傳譯文。")
        return Result(
            text=items[0].get("text", "").strip(),
            detected_lang=(items[0].get("detected_source_language", "") or "").lower(),
            engine=self.label,
            engine_id=self.id,
            source_text=text,
            target_lang=tgt,
        )


# --------------------------------------------------------------------------
# LibreTranslate（可自架）
# --------------------------------------------------------------------------
@register
class LibreTranslate(Engine):
    id = "libretranslate"
    label = "LibreTranslate（可自架）"

    CODE_MAP = {"zh-TW": "zt", "zh-CN": "zh"}

    # 官方公開實例一律要金鑰，沒金鑰連過去只會收到 400
    PUBLIC_HOSTS = ("libretranslate.com", "libretranslate.de")

    def unavailable_reason(self) -> str:
        url = (self.opts.get("url") or "").strip()
        if not url:
            return "尚未設定伺服器網址"
        # 填了公開實例卻沒金鑰，等於沒設定 —— 與其讓它進備援鏈白跑一趟再失敗，
        # 不如現在就標示清楚。自架的實例不受影響。
        if any(host in url.lower() for host in self.PUBLIC_HOSTS):
            if not (self.opts.get("api_key") or "").strip():
                return "公開實例需要 API 金鑰（或改填自架的伺服器網址）"
        return ""

    def translate(self, text: str, src: str, tgt: str) -> Result:
        base = (self.opts.get("url") or "").strip().rstrip("/")
        if not base:
            raise EngineError("尚未設定 LibreTranslate 伺服器網址。")
        payload = {
            "q": text,
            "source": self.CODE_MAP.get(src, src.split("-")[0]) if src != "auto" else "auto",
            "target": self.CODE_MAP.get(tgt, tgt.split("-")[0]),
            "format": "text",
        }
        if (self.opts.get("api_key") or "").strip():
            payload["api_key"] = self.opts["api_key"].strip()

        data = self._post_json(
            f"{base}/translate",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        detected = ""
        det = data.get("detectedLanguage")
        if isinstance(det, dict):
            detected = det.get("language", "") or ""
        return Result(
            text=(data.get("translatedText") or "").strip(),
            detected_lang=detected,
            engine=self.label,
            engine_id=self.id,
            source_text=text,
            target_lang=tgt,
        )


# --------------------------------------------------------------------------
# LLM 類引擎共用邏輯
# --------------------------------------------------------------------------
class _LLMEngine(Engine):
    is_llm = True
    needs_key = True
    supports_stream = True

    def _build_prompt(self, text: str, tgt: str) -> str:
        template = (self.opts.get("prompt") or "").strip() or cfgmod.DEFAULT_LLM_PROMPT
        return template.format(text=text, target_name=cfgmod.llm_lang_name(tgt))

    @staticmethod
    def _clean(out: str) -> str:
        out = (out or "").strip()
        # 偶爾模型會包 markdown 圍籬，拆掉
        fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", out, re.S)
        if fence:
            out = fence.group(1).strip()
        return out

    # ---------------------------------------------------------------- 串流
    def _open_stream(self, url: str, headers: dict, payload: dict, label: str = ""):
        """發出串流請求，回傳 Response（呼叫端負責 close）。

        重試發生在「連線建立前」，所以安全 —— 一旦開始吐 token 就不會再重試，
        否則畫面上會出現兩次翻譯接在一起。
        """
        resp = self._request("POST", url, headers=headers, stream=True,
                             data=json.dumps(payload).encode("utf-8"))
        # SSE 的 Content-Type 是 text/event-stream，通常不帶 charset 參數。
        # requests 這時會依 HTTP/1.1 的舊規範退回 ISO-8859-1，於是 UTF-8 的
        # 中文會被逐位元組誤讀成「å¾é«èˆˆ」這種亂碼。明確指定才安全。
        resp.encoding = "utf-8"
        if resp.status_code >= 400:
            try:
                self._raise_for_status(resp, label)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
        return resp

    @staticmethod
    def _sse_payloads(resp, cancel: threading.Event | None) -> Iterator[str]:
        """逐行讀 SSE，吐出每個 data: 後面的內容。

        每一行都檢查取消旗標：使用者連續選取多段文字時，舊的串流要立刻停掉，
        否則譯文會互相混雜，而且繼續燒 API 額度。

        編碼：刻意「不」用 iter_lines(decode_unicode=True)，因為那會沿用
        resp.encoding —— SSE 不帶 charset 時那是 ISO-8859-1，中文會全毀。
        這裡直接拿位元組自己用 UTF-8 解。按行切是安全的：UTF-8 的多位元組
        序列不可能包含 0x0A，所以換行不會把一個字切成兩半。
        """
        try:
            for raw in resp.iter_lines():
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if raw.startswith("data:"):
                    yield raw[5:].strip()
        finally:
            try:
                resp.close()
            except Exception:
                pass


@register
class Gemini(_LLMEngine):
    id = "gemini"
    label = "Gemini（語意翻譯）"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def translate(self, text: str, src: str, tgt: str) -> Result:
        key = (self.opts.get("api_key") or "").strip()
        if not key:
            raise EngineError("尚未設定 Gemini API 金鑰。")
        model = (self.opts.get("model") or "gemini-3.8-flash").strip()
        payload = {
            "contents": [{"role": "user",
                          "parts": [{"text": self._build_prompt(text, tgt)}]}],
            "generationConfig": {"temperature": 0.2},
        }
        data = self._post_json(
            f"{self.BASE}/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            out = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                raise EngineError(f"內容被模型拒絕處理（{blocked}）。") from exc
            raise EngineError(f"回應格式不符：{str(data)[:300]}") from exc

        return Result(text=self._clean(out), engine=f"{self.label} · {model}",
                      engine_id=self.id, source_text=text, target_lang=tgt)

    def translate_stream(self, text: str, src: str, tgt: str,
                         on_token: Callable[[str], None],
                         cancel: threading.Event | None = None) -> Result:
        key = (self.opts.get("api_key") or "").strip()
        if not key:
            raise EngineError("尚未設定 Gemini API 金鑰。")
        model = (self.opts.get("model") or "gemini-3.8-flash").strip()
        payload = {
            "contents": [{"role": "user",
                          "parts": [{"text": self._build_prompt(text, tgt)}]}],
            "generationConfig": {"temperature": 0.2},
        }
        resp = self._open_stream(
            f"{self.BASE}/models/{model}:streamGenerateContent?alt=sse",
            {"x-goog-api-key": key, "Content-Type": "application/json"},
            payload,
        )

        parts: list[str] = []
        blocked = ""
        for data in self._sse_payloads(resp, cancel):
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            reason = (chunk.get("promptFeedback") or {}).get("blockReason")
            if reason:
                blocked = reason
                continue
            for cand in chunk.get("candidates") or []:
                for part in ((cand.get("content") or {}).get("parts") or []):
                    piece = part.get("text") or ""
                    if piece:
                        parts.append(piece)
                        on_token(piece)

        if not parts:
            if blocked:
                raise EngineError(f"內容被模型拒絕處理（{blocked}）。")
            raise EngineError("串流沒有回傳任何內容。")
        return Result(text=self._clean("".join(parts)),
                      engine=f"{self.label} · {model}",
                      engine_id=self.id, source_text=text, target_lang=tgt)

    def list_models(self) -> list[str]:
        """給設定視窗用：列出目前金鑰可用、支援 generateContent 的模型。"""
        key = (self.opts.get("api_key") or "").strip()
        if not key:
            raise EngineError("尚未設定 Gemini API 金鑰。")
        try:
            resp = http().get(f"{self.BASE}/models",
                              headers={"x-goog-api-key": key}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EngineError(f"連線失敗：{exc}") from exc
        if resp.status_code >= 400:
            raise EngineError(f"HTTP {resp.status_code}：{resp.text[:200]}")
        names = []
        for m in resp.json().get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(m.get("name", "").replace("models/", ""))
        return sorted(n for n in names if n)


@register
class OpenAICompatible(_LLMEngine):
    id = "openai"
    label = "OpenAI 相容端點（含本地模型）"

    def translate(self, text: str, src: str, tgt: str) -> Result:
        key = (self.opts.get("api_key") or "").strip()
        base = (self.opts.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        model = (self.opts.get("model") or "gpt-4o-mini").strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": self._build_prompt(text, tgt)}],
        }
        data = self._post_json(f"{base}/chat/completions", headers=headers,
                               data=json.dumps(payload).encode("utf-8"))
        try:
            out = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError(f"回應格式不符：{str(data)[:300]}") from exc
        return Result(text=self._clean(out), engine=f"{self.label} · {model}",
                      engine_id=self.id, source_text=text, target_lang=tgt)

    def translate_stream(self, text: str, src: str, tgt: str,
                         on_token: Callable[[str], None],
                         cancel: threading.Event | None = None) -> Result:
        key = (self.opts.get("api_key") or "").strip()
        base = (self.opts.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        model = (self.opts.get("model") or "gpt-4o-mini").strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "temperature": 0.2,
            "stream": True,
            "messages": [{"role": "user", "content": self._build_prompt(text, tgt)}],
        }
        resp = self._open_stream(f"{base}/chat/completions", headers, payload)

        parts: list[str] = []
        for data in self._sse_payloads(resp, cancel):
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    parts.append(piece)
                    on_token(piece)

        if not parts:
            raise EngineError("串流沒有回傳任何內容。")
        return Result(text=self._clean("".join(parts)),
                      engine=f"{self.label} · {model}",
                      engine_id=self.id, source_text=text, target_lang=tgt)

    def unavailable_reason(self) -> str:
        # 本地模型（Ollama、LM Studio）通常不需要金鑰，只要有 base_url 就算可用
        return "" if (self.opts.get("base_url") or "").strip() else "尚未設定端點網址"


# --------------------------------------------------------------------------
def build(engine_id: str, cfg: dict, cancel: threading.Event | None = None) -> Engine:
    cls = ENGINES.get(engine_id) or ENGINES["google_free"]
    return cls(cfg, cancel)


def fallback_chain(cfg: dict, primary: str) -> list[str]:
    """主引擎暫時性失敗時，依序要改試哪些引擎。

    只收「現在就能用」的引擎（有金鑰或不需金鑰），而且不重複主引擎。
    使用者在設定裡關掉自動備援時回傳空清單。
    """
    if not cfg.get("auto_fallback", True):
        return []
    order = cfg.get("fallback_order") or ["gemini", "deepl", "google_free", "openai"]
    out = []
    for eid in order:
        if eid == primary or eid in out:
            continue
        cls = ENGINES.get(eid)
        if cls and not cls(cfg).unavailable_reason():
            out.append(eid)
    return out


def listing(cfg: dict) -> list[tuple[str, str, str]]:
    """回傳 [(id, 顯示名稱, 不可用原因或空字串), ...]。

    順序跟著設定裡的 fallback_order 走，所以使用者在設定裡調整備援順序之後，
    下拉選單和彈窗圖示列的排列也會一起變 —— 不然「排序」只影響看不見的備援
    邏輯，介面上卻紋風不動，會讓人以為設定沒生效。
    """
    order = list(cfg.get("fallback_order") or [])
    rank = {eid: i for i, eid in enumerate(order)}
    rows = [(eid, cls.label, cls(cfg).unavailable_reason())
            for eid, cls in ENGINES.items()]
    rows.sort(key=lambda row: (rank.get(row[0], len(order)), row[0]))
    return rows
