"""設定檔讀寫。

設定存放位置：
  Windows : %APPDATA%\\QTrans\\config.json
  其他系統 : ~/.config/QTrans/config.json
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import keyvault

APP_NAME = "QTrans"


def _config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".config" / APP_NAME


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.sqlite3"


# 常用語言。code 為內部統一代碼，各引擎自行對應。
LANGUAGES: list[tuple[str, str]] = [
    ("auto", "自動偵測"),
    ("zh-TW", "繁體中文"),
    ("zh-CN", "简体中文"),
    ("en", "English 英文"),
    ("ja", "日本語 日文"),
    ("ko", "한국어 韓文"),
    ("es", "Español 西班牙文"),
    ("fr", "Français 法文"),
    ("de", "Deutsch 德文"),
    ("it", "Italiano 義大利文"),
    ("pt", "Português 葡萄牙文"),
    ("ru", "Русский 俄文"),
    ("vi", "Tiếng Việt 越南文"),
    ("th", "ไทย 泰文"),
    ("id", "Bahasa Indonesia 印尼文"),
    ("ms", "Bahasa Melayu 馬來文"),
    ("hi", "हिन्दी 印地文"),
    ("ar", "العربية 阿拉伯文"),
    ("nl", "Nederlands 荷蘭文"),
    ("pl", "Polski 波蘭文"),
    ("tr", "Türkçe 土耳其文"),
]

LANG_NAMES = dict(LANGUAGES)

# 給 LLM 引擎看的英文語言名稱
LLM_LANG_NAMES = {
    "zh-TW": "Traditional Chinese (Taiwan)",
    "zh-CN": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "ms": "Malay",
    "hi": "Hindi",
    "ar": "Arabic",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
}

DEFAULT_LLM_PROMPT = (
    "You are a professional translator. Translate the text inside <src> into {target_name}.\n"
    "Rules:\n"
    "- Output ONLY the translation. No preface, no explanation, no quotes, no markdown fences.\n"
    "- Preserve line breaks, list numbering, inline code and placeholders exactly.\n"
    "- Keep technical and academic terminology accurate and consistent.\n"
    "- For Traditional Chinese use Taiwan wording and punctuation（臺灣用語）.\n"
    "- If the text is already in the target language, polish it rather than restating it.\n"
    "<src>\n{text}\n</src>"
)


DEFAULTS: dict = {
    "engine": "google_free",
    "source_lang": "auto",
    "target_lang": "zh-TW",
    # 當偵測到的來源語言 == target_lang 時，自動改用這個目標語言（中英互譯很實用）
    "alt_target_lang": "en",
    "auto_swap_target": True,

    "hotkeys": {
        # 只支援英數字鍵 + 修飾鍵；留空 key 代表停用
        "popup": {"ctrl": True, "alt": False, "shift": False, "key": "Q"},
        "main": {"ctrl": True, "alt": False, "shift": True, "key": "Q"},
        "speak": {"ctrl": True, "alt": False, "shift": False, "key": "E"},
        "double_ctrl": True,          # 連按兩下 Ctrl 開啟主視窗
        "double_ctrl_interval": 0.45,  # 秒
    },

    "popup": {
        "width": 440,
        "max_height": 420,
        "font_size": 13,
        "show_source": True,
        "close_on_focus_lost": True,
        "copy_result_automatically": False,
    },

    "engines": {
        "deepl": {"api_key": "", "pro": False},
        "gemini": {
            "api_key": "",
            "model": "gemini-3.8-flash",
            "prompt": DEFAULT_LLM_PROMPT,
        },
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "prompt": DEFAULT_LLM_PROMPT,
        },
        # 預設留空：公開的 libretranslate.com 實例經常限流或離線，
        # 填了網址卻連不上，比老實標示「未設定」更讓人困惑。
        # 自架或確定可用的公開實例再自己填。
        "libretranslate": {"url": "", "api_key": ""},
    },

    # auto_speak_source：翻譯完成後自動朗讀「原文」。
    # 對照著譯文聽原文發音，是練聽力與跟讀時很實用的組合。
    "tts": {"enabled": True, "volume": 0.9, "auto_speak_source": False},
    "history": {"enabled": True, "max_items": 1000},
    "web_search_url": "https://www.google.com/search?q={q}",
    "start_with_windows": False,
    "timeout": 15,
    # LLM 引擎（Gemini／OpenAI 相容）逐字串流輸出，長文的等待感差很多
    "stream_llm": True,

    # 暫時性錯誤（429 太頻繁、503 過載）的自動重試。
    # 依 Gemini API 官方建議：指數退避加隨機抖動，並限制最大次數。
    "retry": {"attempts": 3, "base_seconds": 1.0, "max_seconds": 8.0},

    # 重試完還是失敗時，自動改用下一個可用的引擎，並在彈窗說明換過了。
    "auto_fallback": True,
    "fallback_order": ["gemini", "deepl", "google_free", "openai"],

    # 選取即翻譯：用滑鼠框選文字後自動反應，不必按熱鍵。
    #   mode = "icon"      選好後在旁邊顯示小圖示，點了才翻譯（較不干擾）
    #   mode = "translate" 直接跳出譯文
    #   mode = "speak"     直接朗讀選取的原文，不翻譯（練發音用）
    # 預設關閉：它會在你每次用滑鼠選字時都去碰剪貼簿，
    # 寫程式或編輯文件時可能造成困擾，請自行決定要不要開。
    "select_to_translate": {
        "enabled": False,
        "mode": "icon",
        "min_chars": 2,
        "max_chars": 2000,
        "min_drag_px": 12,
        "cooldown_seconds": 0.8,
        "skip_if_target_lang": True,   # 選到的已經是目標語言就不要吵
        # 放開滑鼠後、送出 Ctrl+C 之前要等多久。瀏覽器、Office、PDF 閱讀器
        # 在放開左鍵的瞬間還沒更新完選取狀態，太快送會什麼都抓不到。
        "pre_delay_ms": 180,
        # 開啟後把每次偵測的過程寫到程式資料夾的 select-debug.log，
        # 用來查「為什麼框選了卻沒反應」。平常請保持關閉。
        "debug": False,
    },
}


# 設定檔中需要加密保護的欄位路徑
SECRET_PATHS: list[tuple[str, ...]] = [
    ("engines", "deepl", "api_key"),
    ("engines", "gemini", "api_key"),
    ("engines", "openai", "api_key"),
    ("engines", "libretranslate", "api_key"),
]

# 最近一次 load() 的金鑰解密錯誤，形如 [(欄位說明, 錯誤訊息), ...]。
# 換電腦或重裝 Windows 後舊密文必然解不開，由介面層讀這個清單提示使用者。
last_decrypt_errors: list[tuple[str, str]] = []

SECRET_LABELS = {
    ("engines", "deepl", "api_key"): "DeepL API 金鑰",
    ("engines", "gemini", "api_key"): "Gemini API 金鑰",
    ("engines", "openai", "api_key"): "OpenAI 相容端點金鑰",
    ("engines", "libretranslate", "api_key"): "LibreTranslate API 金鑰",
}


def _dig(cfg: dict, path: tuple[str, ...]):
    """沿著路徑取值，回傳 (父層 dict, 最後一段鍵名) 或 None。"""
    node = cfg
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, dict):
        return None
    return node, path[-1]


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    """讀取設定，缺少的鍵一律補上預設值，並解密金鑰欄位。

    回傳的 dict 中金鑰是明文（程式要用它發請求），磁碟上存的是密文。
    解密失敗的欄位會被清空，錯誤記在 last_decrypt_errors 供介面提示。
    """
    global last_decrypt_errors
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    cfg = _deep_merge(DEFAULTS, raw if isinstance(raw, dict) else {})

    errors: list[tuple[str, str]] = []
    for path in SECRET_PATHS:
        found = _dig(cfg, path)
        if not found:
            continue
        parent, key = found
        stored = parent.get(key)
        if not isinstance(stored, str) or not stored:
            continue
        plain, err = keyvault.decrypt(stored)
        parent[key] = plain
        if err:
            errors.append((SECRET_LABELS.get(path, ".".join(path)), err))
    last_decrypt_errors = errors
    return cfg


def save(cfg: dict) -> None:
    """寫入設定；金鑰欄位先加密，明文不落地。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 不要改動呼叫端手上那份 dict（它仍需要明文金鑰來發請求）
    out = copy.deepcopy(cfg)
    for path in SECRET_PATHS:
        found = _dig(out, path)
        if not found:
            continue
        parent, key = found
        value = parent.get(key)
        if isinstance(value, str) and value:
            parent[key] = keyvault.encrypt(value)

    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def lang_label(code: str) -> str:
    return LANG_NAMES.get(code, code)


def llm_lang_name(code: str) -> str:
    return LLM_LANG_NAMES.get(code, LANG_NAMES.get(code, code))
