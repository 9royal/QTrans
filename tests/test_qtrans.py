"""QTrans 驗證測試。

涵蓋：匯入、設定、金鑰加密與遷移、引擎回應解析、SSE 串流、取消機制、
長文切塊、熱鍵對應、語言判斷、歷史紀錄、朗讀切塊。
"""
import json
import os
import sys
import pathlib
import tempfile
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT)

_tmp = tempfile.mkdtemp(prefix="qtrans-test-")
os.environ["XDG_CONFIG_HOME"] = _tmp
os.environ.pop("APPDATA", None)

import qt_stub
qt_stub.install()

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
    except Exception as exc:
        FAIL.append((name, f"{type(exc).__name__}: {exc}", traceback.format_exc()))


def run(group):
    for name, fn in group:
        check(name, fn)


def _patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    return lambda: setattr(obj, name, old)


class FakeResp:
    def __init__(self, payload=None, status=200, text="", headers=None):
        self._payload, self.status_code = payload, status
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def close(self):
        pass

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeStream:
    """模擬 requests 的串流回應。

    刻意忠實重現兩個真實行為，否則測試會漏掉編碼問題：
      1. iter_lines() 吐的是「位元組」，不是字串。
      2. SSE 的 Content-Type 不帶 charset 時，requests 會把 encoding
         退回 ISO-8859-1 —— 用它解 UTF-8 中文就會變成亂碼。
    早期版本的假串流直接吐 str，繞過了解碼層，所以沒抓到那個 bug。
    """

    def __init__(self, lines, status=200, text="", headers=None,
                 encoding="ISO-8859-1"):
        self._lines = [ln.encode("utf-8") if isinstance(ln, str) else ln
                       for ln in lines]
        self.status_code, self.text = status, text
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.encoding = encoding
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for raw in self._lines:
            # decode_unicode=True 會沿用 self.encoding，正是當初的坑
            yield raw.decode(self.encoding, "replace") if decode_unicode else raw

    def close(self):
        self.closed = True


class FakeSession:
    """假的連線池。engines 現在統一走 request()，但保留 get/post 以防萬一。"""

    def __init__(self, get=None, post=None):
        self._get, self._post = get, post
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method.upper(), url))
        if method.upper() == "GET":
            return self._get(url, **kw)
        return self._post(url, **kw)

    def get(self, url, **kw):
        return self._get(url, **kw)

    def post(self, url, **kw):
        return self._post(url, **kw)


# ---------------------------------------------------------------- 1. 匯入
MODULES = ["keyvault", "config", "engines", "capture", "history", "tts", "icons",
           "worker", "ui_popup", "ui_main", "startup", "ui_settings",
           "hotkeys", "app"]
for _m in MODULES:
    check(f"匯入 qtrans.{_m}", lambda m=_m: __import__(f"qtrans.{m}", fromlist=["x"]))

if FAIL:
    print("匯入階段就失敗，後續測試略過\n")
    for n, msg, tb in FAIL:
        print(f"✗ {n}\n{tb}")
    sys.exit(1)

from qtrans import config as cfgmod
from qtrans import engines, hotkeys, keyvault, tts
from qtrans import app as appmod
from qtrans.history import History


# ------------------------------------------------- 2. 金鑰保險箱 (keyvault)
def t_vault_prefix():
    assert keyvault.is_encrypted("dpapi:v1:abc")
    assert not keyvault.is_encrypted("sk-plain-key")
    assert not keyvault.is_encrypted("")


def t_vault_empty():
    assert keyvault.encrypt("") == ""
    assert keyvault.decrypt("") == ("", "")


def t_vault_plaintext_passthrough():
    """舊版明文設定檔要能無痛讀取（遷移路徑）。"""
    plain, err = keyvault.decrypt("sk-old-plaintext-key")
    assert plain == "sk-old-plaintext-key" and err == ""


def t_vault_roundtrip_with_fake_dpapi():
    """用假的 DPAPI 後端驗證加解密邏輯（Linux 上沒有真的 DPAPI）。"""
    store = {}

    def fake_protect(raw):
        store["last"] = raw
        return b"ENC" + raw[::-1]

    def fake_unprotect(cipher):
        assert cipher.startswith(b"ENC")
        return cipher[3:][::-1]

    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_protect", fake_protect),
                _patch(keyvault, "_unprotect", fake_unprotect)]
    try:
        enc = keyvault.encrypt("sk-secret-12345")
        assert keyvault.is_encrypted(enc), enc
        assert "sk-secret-12345" not in enc, "明文洩漏在密文裡"
        plain, err = keyvault.decrypt(enc)
        assert plain == "sk-secret-12345" and err == "", (plain, err)
    finally:
        for r in restores:
            r()


def t_vault_no_double_encrypt():
    """已經是密文的值不可以再被加密一次（存檔兩次就會解不開）。"""
    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_protect", lambda raw: b"ENC" + raw)]
    try:
        once = keyvault.encrypt("abc")
        twice = keyvault.encrypt(once)
        assert once == twice, "重複加密了"
    finally:
        for r in restores:
            r()


def t_vault_decrypt_failure_is_graceful():
    """換電腦後解密失敗必須回傳錯誤訊息，而不是丟出例外。"""
    def boom(cipher):
        raise keyvault.VaultError("DPAPI 解密失敗：測試")

    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_unprotect", boom)]
    try:
        plain, err = keyvault.decrypt("dpapi:v1:AAAA")
        assert plain == "" and "解密失敗" in err, (plain, err)
    finally:
        for r in restores:
            r()


def t_vault_corrupt_base64():
    restores = [_patch(keyvault, "AVAILABLE", True)]
    try:
        plain, err = keyvault.decrypt("dpapi:v1:!!!not-base64!!!")
        assert plain == "" and "損毀" in err, (plain, err)
    finally:
        for r in restores:
            r()


def t_vault_non_windows_honest():
    """非 Windows 不假裝有加密：encrypt 不加前綴。"""
    restore = _patch(keyvault, "AVAILABLE", False)
    try:
        assert keyvault.encrypt("abc") == "abc"
        assert not keyvault.is_encrypted(keyvault.encrypt("abc"))
    finally:
        restore()


run([("金鑰：前綴判斷", t_vault_prefix),
     ("金鑰：空值", t_vault_empty),
     ("金鑰：舊版明文可讀", t_vault_plaintext_passthrough),
     ("金鑰：加解密往返", t_vault_roundtrip_with_fake_dpapi),
     ("金鑰：不重複加密", t_vault_no_double_encrypt),
     ("金鑰：解密失敗優雅降級", t_vault_decrypt_failure_is_graceful),
     ("金鑰：Base64 損毀", t_vault_corrupt_base64),
     ("金鑰：非 Windows 誠實標示", t_vault_non_windows_honest)])


# ---------------------------------------------------------------- 3. 設定
def t_defaults():
    cfg = cfgmod.load()
    assert cfg["engine"] == "google_free", cfg["engine"]
    assert cfg["hotkeys"]["popup"]["key"] == "Q"
    assert cfg["engines"]["gemini"]["model"]
    assert cfg["stream_llm"] is True


def t_merge_keeps_defaults():
    cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfgmod.CONFIG_PATH.write_text(
        json.dumps({"target_lang": "ja", "popup": {"width": 999}}), encoding="utf-8")
    cfg = cfgmod.load()
    assert cfg["target_lang"] == "ja"
    assert cfg["popup"]["width"] == 999
    assert cfg["popup"]["font_size"] == 13, "巢狀預設值沒補回來"
    assert cfg["engine"] == "google_free"
    cfgmod.CONFIG_PATH.unlink()


def t_save_roundtrip():
    cfg = cfgmod.load()
    cfg["target_lang"] = "ko"
    cfgmod.save(cfg)
    assert cfgmod.load()["target_lang"] == "ko"
    raw = cfgmod.CONFIG_PATH.read_text(encoding="utf-8")
    # 預設提示詞含中文，存檔必須維持可讀，不能變成 \uXXXX
    assert "臺灣用語" in raw, "中文被轉成跳脫字元"
    assert "\\u" not in raw
    cfgmod.CONFIG_PATH.unlink()


def t_corrupt_config():
    cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfgmod.CONFIG_PATH.write_text("{ 壞掉的 json", encoding="utf-8")
    assert cfgmod.load()["engine"] == "google_free", "設定檔損毀時應回退預設值"
    cfgmod.CONFIG_PATH.unlink()


def t_config_encrypts_keys_on_disk():
    """最關鍵的一項：金鑰絕不以明文落地，但記憶體中仍是明文可用。"""
    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_protect", lambda raw: b"ENC" + raw[::-1]),
                _patch(keyvault, "_unprotect", lambda c: c[3:][::-1])]
    try:
        cfg = cfgmod.load()
        cfg["engines"]["gemini"]["api_key"] = "AIza-SECRET-KEY"
        cfg["engines"]["deepl"]["api_key"] = "deepl-SECRET"
        cfgmod.save(cfg)

        raw = cfgmod.CONFIG_PATH.read_text(encoding="utf-8")
        assert "AIza-SECRET-KEY" not in raw, "Gemini 金鑰明文落地了"
        assert "deepl-SECRET" not in raw, "DeepL 金鑰明文落地了"
        assert "dpapi:v1:" in raw, "金鑰沒有被加密"

        # 呼叫端手上那份 dict 不可以被改成密文（還要拿去發請求）
        assert cfg["engines"]["gemini"]["api_key"] == "AIza-SECRET-KEY", \
            "save() 污染了呼叫端的 dict"

        back = cfgmod.load()
        assert back["engines"]["gemini"]["api_key"] == "AIza-SECRET-KEY"
        assert back["engines"]["deepl"]["api_key"] == "deepl-SECRET"
        assert cfgmod.last_decrypt_errors == []
        cfgmod.CONFIG_PATH.unlink()
    finally:
        for r in restores:
            r()


def t_config_migrates_plaintext():
    """舊版明文設定檔：第一次讀進來照舊可用，存檔後就被加密。"""
    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_protect", lambda raw: b"ENC" + raw[::-1]),
                _patch(keyvault, "_unprotect", lambda c: c[3:][::-1])]
    try:
        cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cfgmod.CONFIG_PATH.write_text(json.dumps(
            {"engines": {"gemini": {"api_key": "OLD-PLAINTEXT-KEY"}}}), encoding="utf-8")

        cfg = cfgmod.load()
        assert cfg["engines"]["gemini"]["api_key"] == "OLD-PLAINTEXT-KEY", "舊金鑰讀不到"

        cfgmod.save(cfg)
        raw = cfgmod.CONFIG_PATH.read_text(encoding="utf-8")
        assert "OLD-PLAINTEXT-KEY" not in raw, "遷移後明文還留在檔案裡"
        assert cfgmod.load()["engines"]["gemini"]["api_key"] == "OLD-PLAINTEXT-KEY"
        cfgmod.CONFIG_PATH.unlink()
    finally:
        for r in restores:
            r()


def t_config_reports_decrypt_errors():
    """解不開的金鑰要被清空並記錄，供介面提示使用者重新輸入。"""
    def boom(cipher):
        raise keyvault.VaultError("測試用解密失敗")

    restores = [_patch(keyvault, "AVAILABLE", True),
                _patch(keyvault, "_unprotect", boom)]
    try:
        cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cfgmod.CONFIG_PATH.write_text(json.dumps(
            {"engines": {"gemini": {"api_key": "dpapi:v1:QUFBQQ=="}}}), encoding="utf-8")
        cfg = cfgmod.load()
        assert cfg["engines"]["gemini"]["api_key"] == "", "解不開的金鑰沒被清空"
        assert len(cfgmod.last_decrypt_errors) == 1
        assert "Gemini" in cfgmod.last_decrypt_errors[0][0]
        cfgmod.CONFIG_PATH.unlink()
    finally:
        for r in restores:
            r()


run([("設定：預設值", t_defaults), ("設定：巢狀合併", t_merge_keeps_defaults),
     ("設定：存取往返", t_save_roundtrip), ("設定：損毀時回退", t_corrupt_config),
     ("設定：金鑰加密落地", t_config_encrypts_keys_on_disk),
     ("設定：明文自動遷移", t_config_migrates_plaintext),
     ("設定：回報解密失敗", t_config_reports_decrypt_errors)])


# ---------------------------------------------------------------- 4. 連線池
def t_lazy_session():
    engines.close_http()
    assert engines._session is None, "連線池應該延遲建立"
    s1 = engines.http()
    assert engines._session is not None
    assert engines.http() is s1, "連線池應該重複使用"
    engines.close_http()
    assert engines._session is None


run([("連線池：延遲建立並重用", t_lazy_session)])


# ---------------------------------------------------------------- 5. 切塊
def t_chunks_short():
    assert engines.Engine._chunks("hello", 100) == ["hello"]


def t_chunks_roundtrip_lines():
    text = "\n".join(f"第 {i} 行的內容測試" * 5 for i in range(40))
    parts = engines.Engine._chunks(text, 200)
    assert "".join(parts) == text, "多行切塊後無法還原原文"


def t_chunks_roundtrip_longline():
    text = "這是一個沒有換行的超長句子，用來測試切塊是否會掉字。" * 60 + "\n"
    parts = engines.Engine._chunks(text, 150)
    assert "".join(parts) == text, (
        f"超長行切塊掉字：原文 {len(text)} 字，切回來 {len(''.join(parts))} 字")


run([("切塊：短文不切", t_chunks_short),
     ("切塊：多行可還原", t_chunks_roundtrip_lines),
     ("切塊：超長單行可還原", t_chunks_roundtrip_longline)])


# ---------------------------------------------------------------- 6. 引擎
BASE_CFG = cfgmod._deep_merge(cfgmod.load(), {
    # 大多數測試在意的是「解析對不對」，不是重試。把重試關掉，
    # 測試才不會真的去睡好幾秒。重試另有專門的測試。
    "retry": {"attempts": 0},
    "auto_fallback": False,
})


def t_google_parse():
    payload = {
        "sentences": [{"trans": "你好，"}, {"trans": "世界。"},
                      {"translit": "Nǐ hǎo, shìjiè."}],
        "dict": [{"pos": "interjection", "terms": ["你好", "哈囉", "喂"]},
                 {"pos": "noun", "terms": ["問候"]}],
        "src": "en",
    }
    restore = _patch(engines, "http",
                     lambda: FakeSession(get=lambda *a, **k: FakeResp(payload)))
    try:
        res = engines.GoogleFree(BASE_CFG).translate("Hello, world.", "auto", "zh-TW")
    finally:
        restore()
    assert res.text == "你好，世界。", repr(res.text)
    assert res.detected_lang == "en"
    assert res.transliteration == "Nǐ hǎo, shìjiè."
    assert res.dictionary[0][1][:2] == ["你好", "哈囉"]


def t_google_429():
    restore = _patch(engines, "http",
                     lambda: FakeSession(get=lambda *a, **k: FakeResp({}, status=429)))
    try:
        engines.GoogleFree(BASE_CFG).translate("x", "auto", "zh-TW")
    except engines.EngineError as exc:
        assert "429" in str(exc)
        return
    finally:
        restore()
    raise AssertionError("429 應該要丟出 EngineError")


def t_google_multichunk():
    calls = []

    def fake_get(url, params=None, **k):
        q = dict((kk, vv) for kk, vv in params if kk == "q")["q"]
        calls.append(q)
        return FakeResp({"sentences": [{"trans": f"[{len(calls)}]"}], "src": "en"})

    restore = _patch(engines, "http", lambda: FakeSession(get=fake_get))
    try:
        res = engines.GoogleFree(BASE_CFG).translate("word " * 900, "auto", "zh-TW")
    finally:
        restore()
    assert len(calls) > 1, "長文沒有被切成多段"
    assert res.text == "".join(f"[{i+1}]" for i in range(len(calls)))


def t_deepl_parse():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"deepl": {"api_key": "k"}}})
    seen = {}

    def fake_post(url, **k):
        seen["url"] = url
        seen["body"] = json.loads(k["data"].decode())
        return FakeResp({"translations": [
            {"detected_source_language": "EN", "text": "你好"}]})

    restore = _patch(engines, "http", lambda: FakeSession(post=fake_post))
    try:
        res = engines.DeepL(cfg).translate("Hello", "auto", "zh-TW")
    finally:
        restore()
    assert res.text == "你好" and res.detected_lang == "en"
    assert seen["body"]["target_lang"] == "ZH-HANT", seen["body"]
    assert "source_lang" not in seen["body"], "auto 時不應送 source_lang"
    assert "api-free.deepl.com" in seen["url"]


def t_deepl_pro_url():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"deepl": {"api_key": "k", "pro": True}}})
    assert "api.deepl.com" in engines.DeepL(cfg)._url()


def t_gemini_parse():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    seen = {}

    def fake_post(url, **k):
        seen["url"], seen["headers"] = url, k["headers"]
        seen["body"] = json.loads(k["data"].decode())
        return FakeResp({"candidates": [{"content": {"parts": [{"text": "```\n你好\n```"}]}}]})

    restore = _patch(engines, "http", lambda: FakeSession(post=fake_post))
    try:
        res = engines.Gemini(cfg).translate("Hello", "auto", "zh-TW")
    finally:
        restore()
    assert res.text == "你好", f"markdown 圍籬沒拆掉：{res.text!r}"
    assert seen["headers"]["x-goog-api-key"] == "k"
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "Hello" in prompt and "Traditional Chinese (Taiwan)" in prompt


def t_gemini_blocked():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeResp({"promptFeedback": {"blockReason": "SAFETY"}})))
    try:
        engines.Gemini(cfg).translate("x", "auto", "zh-TW")
    except engines.EngineError as exc:
        assert "SAFETY" in str(exc)
        return
    finally:
        restore()
    raise AssertionError("被擋下時應丟出可讀的錯誤")


def t_prompt_with_braces():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    engines.Gemini(cfg)._build_prompt("def f(): return {'a': 1}", "zh-TW")


def t_openai_parse():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"openai": {"api_key": "k"}}})
    seen = {}

    def fake_post(url, **k):
        seen["url"], seen["headers"] = url, k["headers"]
        return FakeResp({"choices": [{"message": {"content": "  你好  "}}]})

    restore = _patch(engines, "http", lambda: FakeSession(post=fake_post))
    try:
        res = engines.OpenAICompatible(cfg).translate("Hello", "auto", "zh-TW")
    finally:
        restore()
    assert res.text == "你好"
    assert seen["url"].endswith("/chat/completions")
    assert seen["headers"]["Authorization"] == "Bearer k"


def t_libre_parse():
    # 預設不帶網址（公開實例常掛），測試自己指定一個
    cfg = cfgmod._deep_merge(BASE_CFG, {
        "engines": {"libretranslate": {"url": "https://lt.example.com"}}})
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeResp(
            {"translatedText": "你好", "detectedLanguage": {"language": "en"}})))
    try:
        res = engines.LibreTranslate(cfg).translate("Hello", "auto", "zh-TW")
    finally:
        restore()
    assert res.text == "你好" and res.detected_lang == "en"


def t_listing():
    rows = engines.listing(BASE_CFG)
    reasons = dict((r[0], r[2]) for r in rows)
    assert set(reasons) == {"google_free", "deepl", "libretranslate", "gemini", "openai"}
    assert reasons["google_free"] == "", "Google 免金鑰應永遠可用"
    assert reasons["deepl"] and reasons["gemini"], "沒金鑰的引擎應標示原因"
    assert reasons["openai"] == "", "OpenAI 相容端點有預設網址就算可用"
    assert reasons["libretranslate"], "LibreTranslate 預設無網址，應標示未設定"


def t_build_fallback():
    assert isinstance(engines.build("不存在的引擎", BASE_CFG), engines.GoogleFree)


def t_stream_flags():
    assert engines.Gemini.supports_stream and engines.OpenAICompatible.supports_stream
    assert not engines.GoogleFree.supports_stream
    assert not engines.DeepL.supports_stream


run([("引擎：Google 解析", t_google_parse), ("引擎：Google 429", t_google_429),
     ("引擎：Google 長文多段", t_google_multichunk),
     ("引擎：DeepL 解析", t_deepl_parse), ("引擎：DeepL Pro 端點", t_deepl_pro_url),
     ("引擎：Gemini 解析", t_gemini_parse), ("引擎：Gemini 被擋", t_gemini_blocked),
     ("引擎：提示詞含大括號", t_prompt_with_braces),
     ("引擎：OpenAI 解析", t_openai_parse),
     ("引擎：LibreTranslate 解析", t_libre_parse),
     ("引擎：清單與可用狀態", t_listing), ("引擎：未知 id 回退", t_build_fallback),
     ("引擎：串流能力標記", t_stream_flags)])


# ---------------------------------------------------------------- 7. SSE 串流
def t_gemini_stream():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"你"}]}}]}',
        "",
        'data: {"candidates":[{"content":{"parts":[{"text":"好，"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"世界。"}]}}]}',
    ]
    seen, tokens = {}, []
    stream = FakeStream(lines)

    def fake_post(url, **k):
        seen["url"], seen["stream"] = url, k.get("stream")
        return stream

    restore = _patch(engines, "http", lambda: FakeSession(post=fake_post))
    try:
        res = engines.Gemini(cfg).translate_stream(
            "Hello, world.", "auto", "zh-TW", on_token=tokens.append)
    finally:
        restore()
    assert "alt=sse" in seen["url"], seen["url"]
    assert "streamGenerateContent" in seen["url"]
    assert seen["stream"] is True, "沒有啟用 requests 的串流模式"
    assert tokens == ["你", "好，", "世界。"], tokens
    assert res.text == "你好，世界。", repr(res.text)
    assert stream.closed, "串流結束後沒有關閉連線"


def t_gemini_stream_strips_fence():
    """串流收到 markdown 圍籬時，最終結果要清乾淨。"""
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    lines = ['data: {"candidates":[{"content":{"parts":[{"text":"```\\n你好\\n```"}]}}]}']
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeStream(lines)))
    try:
        res = engines.Gemini(cfg).translate_stream(
            "Hello", "auto", "zh-TW", on_token=lambda t: None)
    finally:
        restore()
    assert res.text == "你好", repr(res.text)


def t_openai_stream():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"openai": {"api_key": "k"}}})
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{}}]}',          # 空 delta 要被忽略
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"不該出現"}}]}',
    ]
    tokens, seen = [], {}

    def fake_post(url, **k):
        seen["body"] = json.loads(k["data"].decode())
        return FakeStream(lines)

    restore = _patch(engines, "http", lambda: FakeSession(post=fake_post))
    try:
        res = engines.OpenAICompatible(cfg).translate_stream(
            "Hello", "auto", "zh-TW", on_token=tokens.append)
    finally:
        restore()
    assert seen["body"]["stream"] is True
    assert tokens == ["你", "好"], tokens
    assert res.text == "你好"


def t_stream_cancellation():
    """舊請求被取消時要立刻停止讀取，不可以繼續吐 token。"""
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    cancel = threading.Event()
    tokens = []

    def on_token(piece):
        tokens.append(piece)
        cancel.set()        # 收到第一段就模擬「使用者選了新的文字」

    lines = [f'data: {{"candidates":[{{"content":{{"parts":[{{"text":"{i}"}}]}}}}]}}'
             for i in range(20)]
    stream = FakeStream(lines)
    restore = _patch(engines, "http", lambda: FakeSession(post=lambda *a, **k: stream))
    try:
        engines.Gemini(cfg).translate_stream(
            "x", "auto", "zh-TW", on_token=on_token, cancel=cancel)
    except engines.Cancelled:
        assert len(tokens) == 1, f"取消後還繼續讀了 {len(tokens)} 段"
        assert stream.closed, "取消時沒有關閉連線（會繼續燒額度）"
        return
    finally:
        restore()
    raise AssertionError("取消時應丟出 Cancelled")


def t_stream_http_error():
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    stream = FakeStream([], status=400, text="bad key")
    restore = _patch(engines, "http", lambda: FakeSession(post=lambda *a, **k: stream))
    try:
        engines.Gemini(cfg).translate_stream("x", "auto", "zh-TW", on_token=lambda t: None)
    except engines.EngineError as exc:
        assert "400" in str(exc)
        assert stream.closed, "錯誤回應沒有關閉連線"
        return
    finally:
        restore()
    raise AssertionError("HTTP 400 應丟出 EngineError")


def t_stream_fallback_for_non_llm():
    """不支援串流的引擎呼叫 translate_stream 要退回一次性翻譯。"""
    restore = _patch(engines, "http", lambda: FakeSession(
        get=lambda *a, **k: FakeResp({"sentences": [{"trans": "你好"}], "src": "en"})))
    try:
        res = engines.GoogleFree(BASE_CFG).translate_stream(
            "Hello", "auto", "zh-TW", on_token=lambda t: None)
    finally:
        restore()
    assert res.text == "你好"


run([("串流：Gemini 逐字", t_gemini_stream),
     ("串流：Gemini 清除圍籬", t_gemini_stream_strips_fence),
     ("串流：OpenAI 逐字與 [DONE]", t_openai_stream),
     ("串流：取消立即停止", t_stream_cancellation),
     ("串流：HTTP 錯誤關閉連線", t_stream_http_error),
     ("串流：非 LLM 引擎退回一次性", t_stream_fallback_for_non_llm)])


# ---------------------------------------------------------------- 8. 熱鍵
def t_describe():
    assert hotkeys.describe({"ctrl": True, "key": "Q"}) == "Ctrl+Q"
    assert hotkeys.describe({"ctrl": True, "shift": True, "key": "q"}) == "Ctrl+Shift+Q"
    assert hotkeys.describe({"key": ""}) == "（停用）"
    assert hotkeys.describe(None) == "（停用）"


def t_vk_mods():
    assert hotkeys._to_vk({"key": "Q"}) == 0x51
    assert hotkeys._to_vk({"key": "q"}) == 0x51
    assert hotkeys._to_vk({"key": "F5"}) == 0x74
    assert hotkeys._to_vk({"key": ""}) is None
    assert hotkeys._to_mods({"ctrl": True, "shift": True}) == 0x0002 | 0x0004
    assert hotkeys._to_mods({}) == 0, "沒有修飾鍵必須回傳 0，才會被跳過"


def t_default_hotkeys_registrable():
    hk = cfgmod.DEFAULTS["hotkeys"]
    for action in ("popup", "main", "speak"):
        b = hk[action]
        assert hotkeys._to_vk(b) is not None, f"{action} 的按鍵無法對應 vk"
        assert hotkeys._to_mods(b) != 0, f"{action} 沒有修飾鍵，會被跳過"


run([("熱鍵：顯示字串", t_describe), ("熱鍵：vk 與修飾鍵", t_vk_mods),
     ("熱鍵：預設值可註冊", t_default_hotkeys_registrable)])


# ---------------------------------------------------------------- 9. 語言
def t_guess_lang():
    for text, want in [("這是一段中文句子，用來測試。", "zh"),
                       ("This is an English sentence.", "en"),
                       ("これは日本語の文です", "ja"),
                       ("이것은 한국어 문장입니다", "ko")]:
        got = appmod.guess_lang(text)
        assert got == want, f"{text!r} -> {got}（預期 {want}）"


def t_guess_lang_empty():
    appmod.guess_lang("")
    appmod.guess_lang("12345 !!! ???")


def t_same_lang():
    assert appmod.same_lang("zh", "zh-TW")
    assert appmod.same_lang("ZH-tw", "zh-CN")
    assert not appmod.same_lang("en", "zh-TW")
    assert not appmod.same_lang("", "zh-TW")


run([("語言：判斷中英日韓", t_guess_lang), ("語言：空字串不炸", t_guess_lang_empty),
     ("語言：同語言比對", t_same_lang)])


# ------------------------------------------------- 10. 送出前決定目標語言
class _FakeApp:
    """只取 QTransApp 的 _resolve_target 方法來測，不建立整個 Qt 應用。"""

    def __init__(self, cfg):
        self.cfg = cfg

    _resolve_target = appmod.QTransApp._resolve_target


def t_resolve_target_llm_swaps():
    """中文原文 + 目標繁中 + LLM 引擎 → 送出前就改成備用語言。"""
    cfg = dict(BASE_CFG, auto_swap_target=True, alt_target_lang="en")
    app = _FakeApp(cfg)
    assert app._resolve_target("這是中文句子。", "gemini", "zh-TW") == "en"


def t_resolve_target_llm_keeps():
    cfg = dict(BASE_CFG, auto_swap_target=True, alt_target_lang="en")
    app = _FakeApp(cfg)
    assert app._resolve_target("This is English.", "gemini", "zh-TW") == "zh-TW"


def t_resolve_target_non_llm_untouched():
    """Google／DeepL 會自己回報來源語言，不要在送出前亂改。"""
    cfg = dict(BASE_CFG, auto_swap_target=True, alt_target_lang="en")
    app = _FakeApp(cfg)
    assert app._resolve_target("這是中文句子。", "google_free", "zh-TW") == "zh-TW"


def t_resolve_target_respects_switch():
    cfg = dict(BASE_CFG, auto_swap_target=False, alt_target_lang="en")
    app = _FakeApp(cfg)
    assert app._resolve_target("這是中文句子。", "gemini", "zh-TW") == "zh-TW"


def t_resolve_target_alt_same_as_target():
    """備用語言和目標語言相同時不可以換（會變成無意義的同語言翻譯）。"""
    cfg = dict(BASE_CFG, auto_swap_target=True, alt_target_lang="zh-CN")
    app = _FakeApp(cfg)
    assert app._resolve_target("這是中文句子。", "gemini", "zh-TW") == "zh-TW"


run([("目標語言：LLM 自動對調", t_resolve_target_llm_swaps),
     ("目標語言：LLM 不需對調", t_resolve_target_llm_keeps),
     ("目標語言：非 LLM 不動", t_resolve_target_non_llm_untouched),
     ("目標語言：尊重關閉開關", t_resolve_target_respects_switch),
     ("目標語言：備用與目標相同時不換", t_resolve_target_alt_same_as_target)])


# ---------------------------------------------------------------- 11. 歷史
def t_history():
    h = History(max_items=5)
    h.clear()
    for i in range(12):
        h.add(f"source {i}", f"結果 {i}", "en", "zh-TW", "Google")
    rows = h.recent(50)
    assert len(rows) == 5, f"筆數上限沒生效，實際 {len(rows)} 筆"
    assert rows[0].source == "source 11"
    assert h.recent(50, "結果 11")[0].result == "結果 11"
    assert h.recent(50, "不存在的關鍵字") == []
    h.add("", "空的", "en", "zh-TW", "G")
    h.add("有原文", "", "en", "zh-TW", "G")
    assert len(h.recent(50)) == 5, "空白內容不該被寫入"
    h.clear()
    assert h.recent(50) == []
    h.close()


run([("歷史：新增/上限/搜尋/清空", t_history)])


# ---------------------------------------------------------------- 12. 朗讀
def t_tts_split():
    text = "這是一段沒有空白的中文長句用來測試朗讀切塊是否正確。" * 30
    parts = tts._split(text)
    assert parts and all(len(p) <= 180 for p in parts)
    assert "".join(parts) == text, "中文長句切塊掉字"


def t_tts_split_words():
    text = " ".join(["word"] * 200)
    parts = tts._split(text)
    assert all(len(p) <= 180 for p in parts)
    assert " ".join(parts).split() == text.split(), "英文切塊掉字"


run([("朗讀：中文切塊", t_tts_split), ("朗讀：英文切塊", t_tts_split_words)])



# ------------------------------------------------- 13. 自動重試
def _no_sleep():
    """把重試的等待換成不會真的睡的版本，測試才跑得快。"""
    return _patch(engines.Engine, "_wait", lambda self, sec: None)


RETRY_CFG = cfgmod._deep_merge(cfgmod.load(), {
    "retry": {"attempts": 3, "base_seconds": 0.01, "max_seconds": 0.02},
    "auto_fallback": False,
    "engines": {"gemini": {"api_key": "k"}},
})


def t_retry_503_then_success():
    """Gemini 回 503（模型過載）時要自動重試，不該直接把錯誤丟給使用者。"""
    seq = [FakeResp({}, status=503), FakeResp({}, status=503),
           FakeResp({"candidates": [{"content": {"parts": [{"text": "你好"}]}}]})]
    calls = []

    def fake_post(url, **k):
        calls.append(url)
        return seq[min(len(calls) - 1, len(seq) - 1)]

    restores = [_no_sleep(), _patch(engines, "http",
                                    lambda: FakeSession(post=fake_post))]
    try:
        res = engines.Gemini(RETRY_CFG).translate("Hello", "auto", "zh-TW")
    finally:
        for r in restores:
            r()
    assert len(calls) == 3, f"應重試到第 3 次才成功，實際打了 {len(calls)} 次"
    assert res.text == "你好"


def t_retry_429_then_success():
    seq = [FakeResp({}, status=429),
           FakeResp({"sentences": [{"trans": "你好"}], "src": "en"})]
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return seq[min(len(calls) - 1, len(seq) - 1)]

    restores = [_no_sleep(), _patch(engines, "http",
                                    lambda: FakeSession(get=fake_get))]
    try:
        res = engines.GoogleFree(RETRY_CFG).translate("Hello", "auto", "zh-TW")
    finally:
        for r in restores:
            r()
    assert len(calls) == 2 and res.text == "你好"


def t_no_retry_on_client_error():
    """401/403 是金鑰問題，重試幾次都一樣，要立刻回報而不是拖時間。"""
    calls = []

    def fake_post(url, **k):
        calls.append(url)
        return FakeResp({}, status=403, text="invalid key")

    restores = [_no_sleep(), _patch(engines, "http",
                                    lambda: FakeSession(post=fake_post))]
    try:
        engines.Gemini(RETRY_CFG).translate("x", "auto", "zh-TW")
    except engines.EngineError as exc:
        assert len(calls) == 1, f"不該重試，卻打了 {len(calls)} 次"
        assert not exc.retryable, "403 不該被標成可重試"
        assert "金鑰" in str(exc)
        return
    finally:
        for r in restores:
            r()
    raise AssertionError("403 應丟出 EngineError")


def t_retry_exhausted_is_retryable():
    """重試用完後丟出的錯誤要標記 retryable，備援機制才知道可以換引擎。"""
    restores = [_no_sleep(), _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeResp({}, status=503)))]
    try:
        engines.Gemini(RETRY_CFG).translate("x", "auto", "zh-TW")
    except engines.EngineError as exc:
        assert exc.retryable and exc.status == 503
        assert "503" in str(exc)
        return
    finally:
        for r in restores:
            r()
    raise AssertionError("應丟出 EngineError")


def t_retry_honours_retry_after():
    """伺服器指定了 Retry-After 就該聽它的，而不是用自己的退避時間。"""
    waits = []
    restores = [
        _patch(engines.Engine, "_wait", lambda self, sec: waits.append(sec)),
        _patch(engines, "http", lambda: FakeSession(
            post=lambda *a, **k: FakeResp({}, status=503, headers={"Retry-After": "5"}))),
    ]
    try:
        engines.Gemini(RETRY_CFG).translate("x", "auto", "zh-TW")
    except engines.EngineError:
        pass
    finally:
        for r in restores:
            r()
    assert waits and all(abs(w - 5.0) < 0.01 for w in waits), waits


def t_retry_backoff_grows():
    """沒有 Retry-After 時，等待時間要一次比一次長（指數退避）。"""
    waits = []
    cfg = cfgmod._deep_merge(RETRY_CFG,
                             {"retry": {"base_seconds": 1.0, "max_seconds": 100.0}})
    restores = [
        _patch(engines.Engine, "_wait", lambda self, sec: waits.append(sec)),
        _patch(engines, "http", lambda: FakeSession(
            post=lambda *a, **k: FakeResp({}, status=503))),
    ]
    try:
        engines.Gemini(cfg).translate("x", "auto", "zh-TW")
    except engines.EngineError:
        pass
    finally:
        for r in restores:
            r()
    assert len(waits) == 3, waits
    # 有隨機抖動，所以比大致範圍而非精確值
    assert waits[0] < waits[1] < waits[2], f"退避沒有變長：{waits}"


def t_retry_stops_on_cancel():
    """重試等待中被取消，要立刻停住而不是等完。"""
    cancel = threading.Event()
    cancel.set()
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeResp({}, status=503)))
    try:
        engines.Gemini(RETRY_CFG, cancel).translate("x", "auto", "zh-TW")
    except engines.Cancelled:
        return
    finally:
        restore()
    raise AssertionError("已取消時應丟出 Cancelled")


run([("重試：503 後成功", t_retry_503_then_success),
     ("重試：429 後成功", t_retry_429_then_success),
     ("重試：403 不重試", t_no_retry_on_client_error),
     ("重試：耗盡後標記可重試", t_retry_exhausted_is_retryable),
     ("重試：遵守 Retry-After", t_retry_honours_retry_after),
     ("重試：退避時間遞增", t_retry_backoff_grows),
     ("重試：取消立即停止", t_retry_stops_on_cancel)])


# ------------------------------------------------- 14. 引擎備援鏈
def t_fallback_chain_filters():
    """備援鏈只收現在就能用的引擎，而且不含主引擎自己。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "auto_fallback": True,
        "engines": {"gemini": {"api_key": "k"}},   # deepl 沒金鑰，應被濾掉
    })
    chain = engines.fallback_chain(cfg, "gemini")
    assert "gemini" not in chain, "備援鏈不該包含主引擎"
    assert "deepl" not in chain, "沒金鑰的 DeepL 不該進備援鏈"
    assert "google_free" in chain, chain


def t_fallback_chain_disabled():
    cfg = cfgmod._deep_merge(cfgmod.load(), {"auto_fallback": False})
    assert engines.fallback_chain(cfg, "gemini") == []


def _run_task(cfg, chain, stream=False, text="Hello"):
    """直接跑 _TranslateTask.run()，收集它發出的訊號。"""
    from qtrans.worker import TranslateSignals, _TranslateTask
    sig = TranslateSignals()
    got = {"done": [], "failed": [], "switched": [], "token": []}
    sig.done.connect(lambda rid, res: got["done"].append(res))
    sig.failed.connect(lambda rid, msg: got["failed"].append(msg))
    sig.switched.connect(lambda rid, msg: got["switched"].append(msg))
    sig.token.connect(lambda rid, t: got["token"].append(t))
    task = _TranslateTask(1, cfg, chain, text, "auto", "zh-TW",
                          sig, stream, threading.Event())
    task.run()
    return got


def t_fallback_switches_engine():
    """主引擎 503 重試後仍失敗 → 自動換下一個引擎，並附上說明。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "retry": {"attempts": 0}, "stream_llm": False,
        "engines": {"gemini": {"api_key": "k"}},
    })

    def fake_post(url, **k):
        return FakeResp({}, status=503)

    def fake_get(url, **k):
        return FakeResp({"sentences": [{"trans": "你好"}], "src": "en"})

    restores = [_no_sleep(), _patch(engines, "http",
                                    lambda: FakeSession(get=fake_get, post=fake_post))]
    try:
        got = _run_task(cfg, ["gemini", "google_free"])
    finally:
        for r in restores:
            r()

    assert not got["failed"], got["failed"]
    assert len(got["done"]) == 1, got
    res = got["done"][0]
    assert res.text == "你好"
    assert res.engine_id == "google_free", res.engine_id
    assert "已自動改用" in res.note, res.note
    assert got["switched"], "應該要先提示正在換引擎"


def t_fallback_continues_after_key_error():
    """主引擎金鑰錯誤時仍要試下一個引擎。

    這裡原本寫成「不可重試就放棄備援」，理由是「金鑰錯了換引擎也沒用」——
    但那個推理是錯的：Gemini 的金鑰過期，不代表 DeepL 或 Groq 不能用。
    結果是主引擎一回 401／400，整串備援就放棄，使用者只看到「翻譯失敗」。
    """
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "retry": {"attempts": 0}, "stream_llm": False,
        "engines": {"gemini": {"api_key": "bad"}},
    })
    get_calls = []

    def fake_get(*a, **k):
        get_calls.append(1)
        return FakeResp({"sentences": [{"trans": "你好"}], "src": "en"})

    restores = [_no_sleep(), _patch(engines, "http", lambda: FakeSession(
        get=fake_get, post=lambda *a, **k: FakeResp({}, status=401)))]
    try:
        got = _run_task(cfg, ["gemini", "google_free"])
    finally:
        for r in restores:
            r()

    assert get_calls, "401 之後仍然應該試下一個引擎"
    assert got["done"], f"備援引擎應該成功：{got['failed']}"
    assert got["done"][0].text == "你好"
    assert "已自動改用" in got["done"][0].note, got["done"][0].note


def t_all_failed_lists_every_reason():
    """全部引擎都失敗時，要列出每一家各自的原因。

    只講第一個的話，使用者會以為換個引擎就好；全部列出來才看得出
    是某一家壞了還是整個網路不通。
    """
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "retry": {"attempts": 0}, "stream_llm": False,
        "engines": {"gemini": {"api_key": "bad"}},
    })
    restores = [_no_sleep(), _patch(engines, "http", lambda: FakeSession(
        get=lambda *a, **k: FakeResp({}, status=429),
        post=lambda *a, **k: FakeResp({}, status=401)))]
    try:
        got = _run_task(cfg, ["gemini", "google_free"])
    finally:
        for r in restores:
            r()

    assert got["failed"], "應回報失敗"
    msg = got["failed"][0]
    assert "Gemini" in msg and "Google" in msg, f"沒有列出每個引擎：{msg}"
    assert "401" in msg or "金鑰" in msg, msg
    assert "429" in msg or "頻繁" in msg, msg


def t_libretranslate_public_needs_key():
    """填了公開實例卻沒金鑰，等於沒設定 —— 不該讓它進備援鏈白跑一趟。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "engines": {"libretranslate": {"url": "https://libretranslate.com"}}})
    reason = engines.LibreTranslate(cfg).unavailable_reason()
    assert reason and "金鑰" in reason, reason
    assert "libretranslate" not in engines.fallback_chain(cfg, "gemini")

    # 自架的實例不該被誤判
    own = cfgmod._deep_merge(cfgmod.load(), {
        "engines": {"libretranslate": {"url": "http://192.168.1.50:5000"}}})
    assert engines.LibreTranslate(own).unavailable_reason() == ""

    # 公開實例有金鑰就可用
    keyed = cfgmod._deep_merge(cfgmod.load(), {
        "engines": {"libretranslate": {"url": "https://libretranslate.com",
                                       "api_key": "k"}}})
    assert engines.LibreTranslate(keyed).unavailable_reason() == ""


def t_fallback_reports_first_error():
    """全部引擎都失敗時，回報的是第一個（主引擎）的原因。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "retry": {"attempts": 0}, "stream_llm": False,
        "engines": {"gemini": {"api_key": "k"}},
    })
    restores = [_no_sleep(), _patch(engines, "http", lambda: FakeSession(
        get=lambda *a, **k: FakeResp({}, status=429),
        post=lambda *a, **k: FakeResp({}, status=503)))]
    try:
        got = _run_task(cfg, ["gemini", "google_free"])
    finally:
        for r in restores:
            r()
    assert got["failed"] and "503" in got["failed"][0], got["failed"]


def t_no_fallback_after_tokens_emitted():
    """串流已經吐字才失敗，不可以再換引擎 —— 否則畫面會有兩段譯文接在一起。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "retry": {"attempts": 0}, "stream_llm": True,
        "engines": {"gemini": {"api_key": "k"}},
    })
    lines = ['data: {"candidates":[{"content":{"parts":[{"text":"你"}]}}]}',
             'data: {"bad json']       # 吐了一段之後就壞掉

    get_calls = []
    restores = [_no_sleep(), _patch(engines, "http", lambda: FakeSession(
        get=lambda *a, **k: get_calls.append(1) or FakeResp({"sentences": []}),
        post=lambda *a, **k: FakeStream(lines)))]
    try:
        got = _run_task(cfg, ["gemini", "google_free"], stream=True)
    finally:
        for r in restores:
            r()
    assert got["token"] == ["你"], got["token"]
    assert not get_calls, "已經輸出過內容就不該再換引擎"


run([("備援：只收可用引擎", t_fallback_chain_filters),
     ("備援：可關閉", t_fallback_chain_disabled),
     ("備援：503 自動換引擎", t_fallback_switches_engine),
     ("備援：金鑰錯仍試下一個", t_fallback_continues_after_key_error),
     ("備援：全失敗列出每個原因", t_all_failed_lists_every_reason),
     ("備援：公開 Libre 需金鑰", t_libretranslate_public_needs_key),
     ("備援：全失敗回報主引擎原因", t_fallback_reports_first_error),
     ("備援：已吐字就不換引擎", t_no_fallback_after_tokens_emitted)])


# ------------------------------------------------- 15. 選取即翻譯的過濾
class _SelApp:
    """只取 _handle_mouse_selection 來測，不建立整個 Qt 應用。"""

    class _Badge:
        def __init__(self, outer):
            self.outer = outer

        def pop_at_cursor(self, lifetime_ms=3000):
            self.outer.badged.append(self.outer._badge_text)

    def __init__(self, cfg):
        self.cfg = cfg
        self.translated = []
        self.badged = []
        self.spoken = []
        self.debug_lines = []
        self._badge_text = ""
        self.badge = _SelApp._Badge(self)

    def _debug_select(self, message):
        self.debug_lines.append(message)

    def _speak(self, text, lang):
        self.spoken.append((text, lang))

    def _translate_selection(self, text):
        self.translated.append(text)

    _handle_mouse_selection = appmod.QTransApp._handle_mouse_selection


def _sel_cfg(**over):
    base = {"enabled": True, "mode": "icon", "min_chars": 2, "max_chars": 2000,
            "skip_if_target_lang": True, "pre_delay_ms": 180, "debug": False}
    base.update(over)
    return cfgmod._deep_merge(cfgmod.load(),
                              {"select_to_translate": base, "target_lang": "zh-TW"})


def t_sel_disabled_does_nothing():
    app = _SelApp(_sel_cfg(enabled=False))
    app._handle_mouse_selection("Hello world")
    assert not app.badged and not app.translated


def t_sel_shows_badge():
    app = _SelApp(_sel_cfg())
    app._handle_mouse_selection("Hello world")
    assert app.badged == ["Hello world"], app.badged
    assert not app.translated, "圖示模式不該直接翻譯"


def t_sel_translate_mode():
    app = _SelApp(_sel_cfg(mode="translate"))
    app._handle_mouse_selection("Hello world")
    assert app.translated == ["Hello world"]


def t_sel_skips_target_language():
    """框選中文時不該跳出來吵（目標語言就是中文）。"""
    app = _SelApp(_sel_cfg())
    app._handle_mouse_selection("這是一段中文")
    assert not app.badged and not app.translated


def t_sel_skips_too_short_and_long():
    app = _SelApp(_sel_cfg())
    app._handle_mouse_selection("a")
    app._handle_mouse_selection("x" * 5000)
    assert not app.badged


def t_sel_skips_non_text():
    """純數字、符號、路徑不該觸發。"""
    app = _SelApp(_sel_cfg())
    for junk in ("12345", "----", "123.45", "   "):
        app._handle_mouse_selection(junk)
    assert not app.badged, app.badged


run([("選取：關閉時無反應", t_sel_disabled_does_nothing),
     ("選取：顯示圖示", t_sel_shows_badge),
     ("選取：直接翻譯模式", t_sel_translate_mode),
     ("選取：略過目標語言", t_sel_skips_target_language),
     ("選取：略過過短過長", t_sel_skips_too_short_and_long),
     ("選取：略過非文字", t_sel_skips_non_text)])



# ------------------------------------------------- 16. 串流中文編碼
def t_stream_chinese_not_mojibake():
    """串流的中文必須完整，不可以變成 Latin-1 亂碼。

    迴歸測試：SSE 的 Content-Type 是 text/event-stream，不帶 charset，
    requests 會把 encoding 退回 ISO-8859-1。若用 iter_lines(decode_unicode=True)
    就會把 UTF-8 的「很」(E5 BE 88) 讀成「å¾ˆ」。
    """
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    want = "很高興認識你"
    lines = [json.dumps({"candidates": [{"content": {"parts": [{"text": ch}]}}]},
                        ensure_ascii=False).join(["data: ", ""])
             for ch in want]
    tokens = []
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeStream(lines)))
    try:
        res = engines.Gemini(cfg).translate_stream(
            "nice to meet you", "auto", "zh-TW", on_token=tokens.append)
    finally:
        restore()

    assert res.text == want, f"譯文被弄壞了：{res.text!r}（應為 {want!r}）"
    assert "".join(tokens) == want, f"逐字輸出被弄壞了：{''.join(tokens)!r}"
    assert "å" not in res.text, "出現 Latin-1 亂碼"


def t_stream_chinese_openai_endpoint():
    """Groq 等 OpenAI 相容端點走同一條解碼路徑，一併守住。"""
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"openai": {"api_key": "k"}}})
    want = "很高興認識你"
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": ch}}]},
                                   ensure_ascii=False) for ch in want]
    lines.append("data: [DONE]")
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeStream(lines)))
    try:
        res = engines.OpenAICompatible(cfg).translate_stream(
            "nice to meet you", "auto", "zh-TW", on_token=lambda t: None)
    finally:
        restore()
    assert res.text == want, f"譯文被弄壞了：{res.text!r}"


def t_stream_emoji_and_mixed():
    """跨三、四位元組的字元（含表情符號）也要完整。"""
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    want = "測試🎓日本語한국어"
    lines = ["data: " + json.dumps(
        {"candidates": [{"content": {"parts": [{"text": want}]}}]},
        ensure_ascii=False)]
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: FakeStream(lines)))
    try:
        res = engines.Gemini(cfg).translate_stream(
            "x", "auto", "zh-TW", on_token=lambda t: None)
    finally:
        restore()
    assert res.text == want, f"{res.text!r} != {want!r}"


def t_stream_sets_utf8_encoding():
    """_open_stream 要主動把 encoding 設成 utf-8（第二道保險）。"""
    cfg = cfgmod._deep_merge(BASE_CFG, {"engines": {"gemini": {"api_key": "k"}}})
    stream = FakeStream(['data: {"candidates":[{"content":{"parts":[{"text":"好"}]}}]}'])
    assert stream.encoding == "ISO-8859-1", "測試前提：預設是會出事的編碼"
    restore = _patch(engines, "http", lambda: FakeSession(
        post=lambda *a, **k: stream))
    try:
        engines.Gemini(cfg).translate_stream("x", "auto", "zh-TW",
                                             on_token=lambda t: None)
    finally:
        restore()
    assert stream.encoding == "utf-8", f"沒有改成 utf-8，仍是 {stream.encoding}"


run([("編碼：Gemini 串流中文不亂碼", t_stream_chinese_not_mojibake),
     ("編碼：OpenAI 相容串流中文不亂碼", t_stream_chinese_openai_endpoint),
     ("編碼：表情符號與多語系完整", t_stream_emoji_and_mixed),
     ("編碼：串流強制 utf-8", t_stream_sets_utf8_encoding)])



# ------------------------------------------------- 17. 引擎可用性與排序
def t_first_available_skips_unconfigured():
    """預設引擎指向沒設定好的引擎時，要自動退到真的能用的那個。"""
    from qtrans import uikit
    cfg = cfgmod.load()      # 乾淨設定：只有 Google 與 OpenAI 端點可用
    assert uikit.first_available_engine(cfg, "gemini") != "gemini", "沒金鑰的 Gemini 不該被選中"
    assert uikit.first_available_engine(cfg, "google_free") == "google_free"


def t_first_available_honours_preferred():
    from qtrans import uikit
    cfg = cfgmod._deep_merge(cfgmod.load(), {"engines": {"gemini": {"api_key": "k"}}})
    assert uikit.first_available_engine(cfg, "gemini") == "gemini"


def t_libretranslate_default_unconfigured():
    """公開實例常掛，預設留空比填了連不上更誠實。"""
    assert cfgmod.DEFAULTS["engines"]["libretranslate"]["url"] == ""
    assert engines.LibreTranslate(cfgmod.load()).unavailable_reason()


def t_fallback_order_respected():
    """備援鏈要照使用者排的順序走。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "auto_fallback": True,
        "fallback_order": ["openai", "google_free", "gemini", "deepl"],
        "engines": {"gemini": {"api_key": "k"}},
    })
    chain = engines.fallback_chain(cfg, "deepl")
    assert chain[0] == "openai", chain
    assert chain.index("openai") < chain.index("google_free") < chain.index("gemini"), chain


run([("可用性：略過未設定引擎", t_first_available_skips_unconfigured),
     ("可用性：尊重指定引擎", t_first_available_honours_preferred),
     ("可用性：LibreTranslate 預設未設定", t_libretranslate_default_unconfigured),
     ("備援：依使用者排序", t_fallback_order_respected)])


# ------------------------------------------------- 18. 朗讀不播到舊音訊
def t_tts_discards_stale_request():
    """先按朗讀譯文再按朗讀原文，兩個下載同時在跑，
    舊的那個即使晚回來也不可以播出去。"""
    from qtrans import tts as ttsmod
    played = []

    class FakeSpeaker:
        _seq = 2                      # 目前最新的請求是 2
        _player = None
        _folder = None

        def _cleanup(self, keep):
            pass

        _play = ttsmod.Speaker._play

    class FakePlayer:
        def stop(self):
            played.append("stop")

        def setSource(self, url):
            played.append(("setSource", str(url)))

        def play(self):
            played.append("play")

    sp = FakeSpeaker()
    sp._player = FakePlayer()

    sp._play(1, "C:/tmp/tts-1.mp3")   # 過期的請求
    assert played == [], f"過期請求不該播放：{played}"

    sp._play(2, "C:/tmp/tts-2.mp3")   # 最新的請求
    assert "play" in played, played


def t_tts_clears_source_before_replay():
    """必須先 setSource(空) 再設新檔，否則 Qt 會重播記憶體裡的舊音訊。"""
    from qtrans import tts as ttsmod
    calls = []

    class FakePlayer:
        def stop(self):
            pass

        def setSource(self, url):
            calls.append(str(url))

        def play(self):
            pass

    class FakeSpeaker:
        _seq = 1
        _player = FakePlayer()

        def _cleanup(self, keep):
            pass

        _play = ttsmod.Speaker._play

    FakeSpeaker()._play(1, "C:/tmp/tts-1.mp3")
    assert len(calls) == 2, f"應該呼叫兩次 setSource，實際 {len(calls)}：{calls}"
    assert "QUrl()" in calls[0] or calls[0] == "", \
        f"第一次要先清空來源，實際是 {calls[0]!r}"
    assert "tts-1.mp3" in calls[1], f"第二次才設新檔，實際是 {calls[1]!r}"


def t_tts_filenames_rotate():
    """每次朗讀要用不同檔名，不能一直覆寫同一個檔。"""
    import inspect
    from qtrans import tts as ttsmod
    src = inspect.getsource(ttsmod._FetchTask.run)
    assert "qtrans_tts.mp3" not in src, "還在用固定檔名"
    assert "self.seq" in src, "檔名應帶序號"


run([("朗讀：丟棄過期請求", t_tts_discards_stale_request),
     ("朗讀：重播前先清空來源", t_tts_clears_source_before_replay),
     ("朗讀：檔名每次輪替", t_tts_filenames_rotate)])



# ------------------------------------------------- 19. 介面一致性修正
def t_listing_follows_fallback_order():
    """設定裡調整備援順序後，下拉選單的排列也要跟著變。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {
        "fallback_order": ["libretranslate", "openai", "google_free", "gemini", "deepl"]})
    ids = [row[0] for row in engines.listing(cfg)]
    assert ids[:3] == ["libretranslate", "openai", "google_free"], ids

    cfg2 = cfgmod._deep_merge(cfgmod.load(), {
        "fallback_order": ["deepl", "gemini"]})
    ids2 = [row[0] for row in engines.listing(cfg2)]
    assert ids2[0] == "deepl" and ids2[1] == "gemini", ids2


def t_listing_includes_unlisted_engines():
    """fallback_order 沒列到的引擎不可以憑空消失。"""
    cfg = cfgmod._deep_merge(cfgmod.load(), {"fallback_order": ["deepl"]})
    ids = {row[0] for row in engines.listing(cfg)}
    assert ids == set(engines.ENGINES), ids


class _FakeCombo:
    """足以驗證 fill_engine_combo 行為的假下拉選單。"""

    def __init__(self):
        self.items = []          # [(文字, eid)]
        self.data = {}           # {(idx, role): 值}
        self.index = -1
        self._model = self

    def blockSignals(self, flag):
        pass

    def clear(self):
        self.items.clear()
        self.data.clear()

    def model(self):
        return self._model

    def item(self, idx):
        return None              # 模擬拿不到 QStandardItem 的情況

    def addItem(self, text, eid):
        self.items.append((text, eid))

    def count(self):
        return len(self.items)

    def setItemData(self, idx, value, role):
        self.data[(idx, role)] = value

    def findData(self, eid):
        for i, (_t, e) in enumerate(self.items):
            if e == eid:
                return i
        return -1

    def setCurrentIndex(self, idx):
        self.index = idx


def t_combo_marks_unavailable():
    """未設定的引擎要有明顯標示，不能只靠 setEnabled —— 深色主題下看不出來。"""
    from qtrans import uikit
    box = _FakeCombo()
    cfg = cfgmod.load()          # Gemini、DeepL、LibreTranslate 都沒設定
    uikit.fill_engine_combo(box, cfg, "google_free")

    by_id = {eid: text for text, eid in box.items}
    assert "🔒" in by_id["gemini"], by_id["gemini"]
    assert "未設定" in by_id["gemini"]
    assert "🔒" not in by_id["google_free"], "可用的引擎不該被標記"

    # 灰色前景（ForegroundRole）要有設定，這是 setEnabled 之外的第二道標示
    gemini_idx = box.findData("gemini")
    roles = [role for (idx, role) in box.data if idx == gemini_idx]
    assert len(roles) >= 2, f"未設定的項目應同時有提示與顏色，實際 {roles}"


def t_combo_selects_available():
    from qtrans import uikit
    box = _FakeCombo()
    uikit.fill_engine_combo(box, cfgmod.load(), "google_free")
    assert box.items[box.index][1] == "google_free"


def t_settings_dialog_is_not_modal():
    """設定必須非 modal，否則開著設定時彈窗會卡住點不動。"""
    import inspect
    src = inspect.getsource(appmod.QTransApp.open_settings)
    assert ".exec()" not in src, "還在用 modal 的 exec()，會鎖住其他視窗"
    assert "setModal(False)" in src, "沒有明確設成非 modal"
    assert "dialog.show()" in src


def t_popup_speak_signal_carries_direction():
    """彈窗要能分別朗讀原文與譯文。"""
    import inspect
    from qtrans import ui_popup
    src = inspect.getsource(ui_popup.PopupWindow._build_ui)
    assert "speak_requested.emit(True)" in src, "缺少朗讀原文"
    assert "speak_requested.emit(False)" in src, "缺少朗讀譯文"


def t_app_speak_handles_both():
    import inspect
    src = inspect.getsource(appmod.QTransApp._on_popup_speak)
    assert "source" in src and "result_text" in src, src


def t_runtime_config_rebuilds_services():
    """朗讀與歷史的開關要能即時生效，不該需要重新啟動程式。"""
    import inspect
    src = inspect.getsource(appmod.QTransApp._apply_runtime_config)
    assert "_create_speaker" in src, "朗讀從關到開不會被重建"
    assert "History(" in src, "歷史從關到開不會被重建"
    assert "apply_style" in src, "字型大小不會即時套用"


run([("介面：下拉依備援順序排列", t_listing_follows_fallback_order),
     ("介面：未列出的引擎不消失", t_listing_includes_unlisted_engines),
     ("介面：未設定引擎明確標示", t_combo_marks_unavailable),
     ("介面：自動選可用引擎", t_combo_selects_available),
     ("介面：設定視窗非 modal", t_settings_dialog_is_not_modal),
     ("介面：彈窗可朗讀原文與譯文", t_popup_speak_signal_carries_direction),
     ("介面：朗讀處理兩個方向", t_app_speak_handles_both),
     ("介面：設定即時生效不需重啟", t_runtime_config_rebuilds_services)])



# ------------------------------------------------- 20. 自動取字的時序與降級
def t_capture_has_configurable_predelay():
    """取字前的等待要能調整 —— 滑鼠剛放開時各家程式反應速度差很多。"""
    import inspect
    from qtrans import capture
    sig = inspect.signature(capture.get_selection)
    assert "pre_delay" in sig.parameters, "get_selection 沒有 pre_delay 參數"
    assert sig.parameters["pre_delay"].default == 0.05, "熱鍵路徑的預設不該變"

    sig2 = inspect.signature(capture.get_selection_or_clipboard)
    assert "pre_delay" in sig2.parameters


def t_auto_select_does_not_fall_back_to_clipboard():
    """自動偵測框選時，取不到就是取不到，不可以拿剪貼簿舊內容頂替。"""
    import inspect
    src = inspect.getsource(appmod._CaptureTask.run)
    assert "allow_clipboard" in src, "沒有區分兩種取字方式"

    src2 = inspect.getsource(appmod.QTransApp._on_hotkey_action)
    assert "allow_clipboard=False" in src2, "自動模式仍會退回剪貼簿"
    assert "pre_delay" in src2, "自動模式沒有套用較長的等待"


def t_default_predelay_is_generous():
    """自動模式的預設等待要夠長，否則瀏覽器與 Office 幾乎都抓不到。"""
    sel = cfgmod.DEFAULTS["select_to_translate"]
    assert sel["pre_delay_ms"] >= 150, sel["pre_delay_ms"]


def t_empty_capture_is_logged():
    """取不到文字時要留下記錄，不能靜默返回讓人無從查起。"""
    app = _SelApp(_sel_cfg(debug=True))
    app._handle_mouse_selection("")
    assert app.debug_lines, "取不到文字卻沒有任何記錄"
    assert "沒有取到文字" in app.debug_lines[0], app.debug_lines


def t_filter_reasons_are_logged():
    """每一道過濾都要說明為什麼略過。"""
    app = _SelApp(_sel_cfg(debug=True))
    app._handle_mouse_selection("這是一段中文")      # 目標語言
    assert any("目標語言" in line for line in app.debug_lines), app.debug_lines

    app2 = _SelApp(_sel_cfg(debug=True))
    app2._handle_mouse_selection("12345")            # 沒有字母
    assert any("字母" in line for line in app2.debug_lines), app2.debug_lines


def t_speak_mode_calls_speak():
    """直接朗讀模式要真的去唸，而不是翻譯。"""
    app = _SelApp(_sel_cfg(mode="speak"))
    app._handle_mouse_selection("nice to meet you")
    assert app.spoken, "朗讀模式沒有呼叫朗讀"
    assert app.spoken[0][0] == "nice to meet you"
    assert not app.translated, "朗讀模式不該翻譯"


run([("取字：等待時間可調", t_capture_has_configurable_predelay),
     ("取字：自動模式不退回剪貼簿", t_auto_select_does_not_fall_back_to_clipboard),
     ("取字：自動模式預設等待夠長", t_default_predelay_is_generous),
     ("取字：取不到會留記錄", t_empty_capture_is_logged),
     ("取字：過濾原因會留記錄", t_filter_reasons_are_logged),
     ("取字：朗讀模式確實朗讀", t_speak_mode_calls_speak)])



# ------------------------------------------------- 21. Win32 型別宣告
def t_win32_handle_types_declared():
    """GetModuleHandleW 一定要宣告 restype。

    迴歸測試：ctypes 沒指定 restype 時預設 c_int（32 位元），而 64 位元
    Windows 的 module handle 是 64 位元指標 —— 回傳值被截斷成無效 handle，
    SetWindowsHookExW 收到後回報 ERROR_MOD_NOT_FOUND（126），
    「選取即翻譯」和「連按兩下 Ctrl」就永遠裝不上掛鉤。
    這個 bug 在 Linux 上測不出來，只能靠靜態檢查守住。
    """
    src = pathlib.Path("qtrans/hotkeys.py").read_text(encoding="utf-8")
    assert "GetModuleHandleW.restype" in src, "GetModuleHandleW 沒有宣告 restype"
    assert "GetModuleHandleW.argtypes" in src, "GetModuleHandleW 沒有宣告 argtypes"
    assert "GetCurrentThreadId.restype" in src, "GetCurrentThreadId 沒有宣告 restype"

    # 回傳型別必須是指標大小，不能是 c_int
    idx = src.index("GetModuleHandleW.restype")
    line = src[idx:src.index("\n", idx)]
    assert "HMODULE" in line or "c_void_p" in line, line


def t_hook_install_has_fallback():
    """掛鉤安裝要有退路：模組 handle 不行就試 NULL。"""
    src = pathlib.Path("qtrans/hotkeys.py").read_text(encoding="utf-8")
    assert "_install_hook" in src, "沒有共用的掛鉤安裝函式"
    block = src[src.index("def _install_hook"):]
    block = block[:block.index("\n\n\n")] if "\n\n\n" in block else block[:800]
    assert block.count("SetWindowsHookExW") >= 2, "沒有第二次嘗試"
    assert "None, 0" in block, "沒有退回 NULL 的那條路"


def t_keyboard_hook_failure_is_reported():
    """鍵盤掛鉤裝不上也要回報，不能默默失效。"""
    src = pathlib.Path("qtrans/hotkeys.py").read_text(encoding="utf-8")
    assert "連按兩下 Ctrl 的鍵盤監聽" in src, "鍵盤掛鉤失敗沒有回報"


run([("Win32：module handle 型別正確", t_win32_handle_types_declared),
     ("Win32：掛鉤安裝有退路", t_hook_install_has_fallback),
     ("Win32：鍵盤掛鉤失敗會回報", t_keyboard_hook_failure_is_reported)])



# ------------------------------------------------- 22. 不干擾使用者輸入
def t_auto_capture_never_releases_modifiers():
    """自動取字絕對不可以強制鬆開修飾鍵。

    迴歸測試：這個 bug 會毀掉使用者的資料。框選後按 Ctrl+C 時，自動取字
    若送出 Ctrl 的 keyup，系統就認為 Ctrl 放開了；使用者接著按的 C 變成
    單獨的字元輸入，在可編輯區域會把選取的文字整段取代掉 —— 看起來就像
    「複製卻把字刪掉了」。
    """
    import inspect
    from qtrans import capture

    src = inspect.getsource(capture.get_selection_passively)
    assert "release_modifiers=False" in src, "自動取字仍會強制鬆開修飾鍵"
    assert "abort_if_typing=True" in src, "自動取字沒有偵測使用者是否在打字"

    # 熱鍵路徑必須維持原本行為
    sig = inspect.signature(capture.get_selection)
    assert sig.parameters["release_modifiers"].default is True, \
        "熱鍵路徑需要鬆開修飾鍵，否則會送成 Ctrl+Shift+C"
    assert sig.parameters["abort_if_typing"].default is False


def t_auto_capture_aborts_when_typing():
    """偵測到修飾鍵按著就放棄，而且不可以留下副作用。"""
    from qtrans import capture

    clipboard = {"value": "使用者原本的剪貼簿內容"}
    sent = []

    restores = [
        _patch(capture, "_any_modifier_down", lambda: True),
        _patch(capture, "_read_clipboard", lambda: clipboard["value"]),
        _patch(capture, "_write_clipboard",
               lambda v: clipboard.__setitem__("value", v)),
        _patch(capture, "_send_copy", lambda: sent.append(1)),
        _patch(capture, "_release_modifiers", lambda: sent.append("release")),
    ]
    try:
        text = capture.get_selection_passively()
    finally:
        for r in restores:
            r()

    assert text == "", "放棄時不該回傳內容"
    assert not sent, f"放棄時不該送出任何按鍵：{sent}"
    assert clipboard["value"] == "使用者原本的剪貼簿內容", "剪貼簿被弄髒了"


def t_auto_capture_aborts_if_typing_starts_during_delay():
    """等待期間才開始按鍵也要放棄，並且還原剪貼簿。

    這是最常見的時序：框選完立刻按 Ctrl+C，使用者的按鍵落在等待視窗裡。
    """
    from qtrans import capture

    clipboard = {"value": "原本的內容"}
    state = {"checks": 0}
    sent = []

    def typing_starts_later():
        state["checks"] += 1
        return state["checks"] > 1      # 第一次沒按，等待後才按下

    restores = [
        _patch(capture, "_any_modifier_down", typing_starts_later),
        _patch(capture, "_read_clipboard", lambda: clipboard["value"]),
        _patch(capture, "_write_clipboard",
               lambda v: clipboard.__setitem__("value", v)),
        _patch(capture, "_send_copy", lambda: sent.append(1)),
    ]
    try:
        text = capture.get_selection_passively(pre_delay=0.0)
    finally:
        for r in restores:
            r()

    assert text == ""
    assert not sent, "等待後才發現使用者在打字，仍然送出了 Ctrl+C"
    assert clipboard["value"] == "原本的內容", "放棄時沒有還原剪貼簿"


def t_hotkey_capture_still_releases():
    """熱鍵路徑的行為不可以被這次修正改掉。"""
    from qtrans import capture

    clipboard = {"value": "舊的"}
    events = []

    restores = [
        _patch(capture, "_read_clipboard",
               lambda: "選取的文字" if events else clipboard["value"]),
        _patch(capture, "_write_clipboard",
               lambda v: clipboard.__setitem__("value", v)),
        _patch(capture, "_send_copy", lambda: events.append("copy")),
        _patch(capture, "_release_modifiers", lambda: events.append("release")),
        _patch(capture, "_any_modifier_down", lambda: True),   # 按著熱鍵
    ]
    try:
        text = capture.get_selection(timeout=0.2, pre_delay=0.0)
    finally:
        for r in restores:
            r()

    assert "release" in events, "熱鍵路徑沒有鬆開修飾鍵"
    assert "copy" in events, "熱鍵路徑沒有送出 Ctrl+C"
    assert text == "選取的文字", text


run([("輸入安全：自動取字不碰修飾鍵", t_auto_capture_never_releases_modifiers),
     ("輸入安全：打字中就放棄", t_auto_capture_aborts_when_typing),
     ("輸入安全：等待中才打字也放棄", t_auto_capture_aborts_if_typing_starts_during_delay),
     ("輸入安全：熱鍵路徑行為不變", t_hotkey_capture_still_releases)])


# ================================================================ 記錄檔隱私
# 除錯記錄檔曾經把框選到的文字前 40 字寫進去。使用者一天框選幾百次，
# 裡面什麼都有，而這個檔案是明文、不輪替、還鼓勵「傳給別人看」。
# 實際的記錄檔裡真的出現過 API 金鑰，所以這幾項是回歸測試：
# 以後若有人想在記錄裡「順便看一下內容比較好查」，會先被擋下來。

def t_debug_log_never_contains_selected_text():
    secret = "sk-ant-usr-NOT-A-REAL-KEY-abcdefghijklmnop"
    app = _SelApp(_sel_cfg(debug=True))
    app._handle_mouse_selection(secret)
    joined = "\n".join(app.debug_lines)
    assert secret not in joined, f"記錄檔寫入了框選到的文字：{joined!r}"
    assert "sk-ant" not in joined, f"記錄檔寫入了文字片段：{joined!r}"
    assert str(len(secret)) in joined, "長度應該要記，否則診斷不了"


def t_debug_log_skip_reasons_carry_no_text():
    app = _SelApp(_sel_cfg(debug=True, min_chars=50))
    app._handle_mouse_selection("MySecretPassword123")
    joined = "\n".join(app.debug_lines)
    assert "MySecretPassword123" not in joined, joined
    assert "略過" in joined, "略過的理由還是要記下來"


def t_debug_log_miss_message_does_not_blame_delay():
    # 實測 525 次框選中有 244 次取不到字，而失敗是成串出現的 ——
    # 典型是使用者在拖檔案或拉視窗，不是等待時間不夠。舊訊息
    # 直接建議「調高等待時間」，會把人帶往錯的方向。
    app = _SelApp(_sel_cfg(debug=True))
    app._handle_mouse_selection("")
    joined = "\n".join(app.debug_lines)
    assert "本來就不是在選文字" in joined, joined
    assert joined.index("本來就不是在選文字") < joined.index("放開滑鼠後等待"), \
        "最可能的原因要排在調參數建議前面"


run([("記錄檔隱私：不寫入框選到的文字", t_debug_log_never_contains_selected_text),
     ("記錄檔隱私：略過理由也不帶文字", t_debug_log_skip_reasons_carry_no_text),
     ("記錄檔正確性：失敗訊息不先怪等待時間", t_debug_log_miss_message_does_not_blame_delay)])


# ================================================================ 品牌符號
# Ro 是作者簽名，放在使用者看得到的介面上。它純屬裝飾，所以這一組
# 測試的重點不是「畫得好不好看」，而是「畫不出來時程式照樣能用」。

from qtrans import brand as brandmod


def t_brand_white_version_flips_only_the_figure():
    black, white = brandmod.MARK_SVG, brandmod.MARK_SVG_WHITE
    assert black.count('class="fig"') == 4, "人形與腿應該是 4 條路徑"
    flipped = [l for l in white.splitlines() if 'class="fig"' in l]
    assert len(flipped) == 4
    for line in flipped:
        assert 'fill="#ffffff"' in line, line
        assert '#231816' not in line, line


def t_brand_white_version_keeps_the_football_readable():
    # 足球的五邊形必須留著深色，否則反白後整顆球變成一塊白斑。
    white = brandmod.MARK_SVG_WHITE
    non_fig_dark = [l for l in white.splitlines()
                    if '#231816' in l and 'class="fig"' not in l]
    assert non_fig_dark, "足球的五邊形被一起轉白了"
    assert 'stroke="#ffffff" stroke-width="3.5"' in white, "反白版少了分離用的白框"


def t_brand_aspect_ratio_is_fixed():
    assert abs(brandmod.ASPECT - 520 / 334) < 1e-9


def t_brand_degrades_without_qtsvg():
    # 測試環境的 Qt 替身沒有 QtSvg，正好等同於使用者裝了精簡版 PySide6。
    # 這種時候要安靜地不顯示，不能讓翻譯程式整個起不來。
    assert brandmod.ro_pixmap(18) is None
    assert brandmod.ro_label(18) is None


def t_brand_never_raises():
    for h in (1, 18, 56, 512):
        for dark in (True, False):
            brandmod.ro_pixmap(h, dark=dark)      # 不得拋例外
    brandmod.ro_label(0)
    brandmod.ro_label(18, opacity=1.0)


def t_brand_ui_guards_against_missing_mark():
    # 三個放置點都必須檢查 None。少一個，QtSvg 不在的機器上就開不了視窗。
    import inspect
    from qtrans import ui_main, ui_settings

    main_src = inspect.getsource(ui_main.MainWindow._build_ui)
    assert "ro_label" in main_src, "主視窗沒有放上 Ro"
    assert "if mark is not None" in main_src, "主視窗沒有檢查 Ro 畫得出來"

    about_src = inspect.getsource(ui_main.MainWindow._about)
    assert "ro_pixmap" in about_src, "關於視窗沒有放上 Ro"
    assert "if pm is not None" in about_src, "關於視窗沒有備援圖示"

    # 設定視窗的版面直接建在 __init__ 裡，沒有獨立的 _build_ui。
    set_src = inspect.getsource(ui_settings.SettingsDialog.__init__)
    assert "ro_label" in set_src, "設定視窗沒有放上 Ro"
    assert "if mark is not None" in set_src, "設定視窗沒有檢查 Ro 畫得出來"


run([("品牌：反白版只翻轉人形", t_brand_white_version_flips_only_the_figure),
     ("品牌：反白版的足球仍看得出來", t_brand_white_version_keeps_the_football_readable),
     ("品牌：長寬比固定不變形", t_brand_aspect_ratio_is_fixed),
     ("品牌：缺少 QtSvg 時安靜降級", t_brand_degrades_without_qtsvg),
     ("品牌：任何尺寸都不拋例外", t_brand_never_raises),
     ("品牌：三個放置點都有防呆", t_brand_ui_guards_against_missing_mark)])


# ---------------------------------------------------------------- 結果
print(f"\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項\n")
for name in PASS:
    print(f"  ✓ {name}")
if FAIL:
    print()
    for name, msg, tb in FAIL:
        print(f"  ✗ {name}\n      {msg}")
    print("\n" + "=" * 62)
    for name, msg, tb in FAIL:
        print(f"\n--- {name} ---\n{tb}")
sys.exit(1 if FAIL else 0)
