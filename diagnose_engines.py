"""逐一實測每個翻譯引擎，把結果寫成報告讓 Claude 直接讀。

不需要關閉 QTrans，也不會動到任何設定 —— 只是讀設定、打 API、記錄結果。
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

buf = io.StringIO()


def say(line: str = "") -> None:
    print(line)
    buf.write(line + "\n")


def mask(value: str) -> str:
    """只顯示金鑰的頭尾，中間遮掉 —— 報告會給別人看。"""
    v = (value or "").strip()
    if not v:
        return "（未填）"
    if len(v) <= 10:
        return f"已填（{len(v)} 字）"
    return f"{v[:4]}…{v[-4:]}（{len(v)} 字）"


say("QTrans 引擎診斷報告")
say("=" * 58)
say(f"時間：{time.strftime('%Y-%m-%d %H:%M:%S')}")
say()

# ---------------------------------------------------------------- 設定
try:
    from qtrans import config as cfgmod
    from qtrans import engines as enginesmod
except Exception as exc:
    say(f"無法載入 QTrans 模組：{exc}")
    say(traceback.format_exc())
    (ROOT / "DIAGNOSTIC-ENGINES.txt").write_text(buf.getvalue(), encoding="utf-8")
    input("按 Enter 關閉...")
    sys.exit(1)

cfg = cfgmod.load()

say("[設定]")
say(f"  設定檔　　：{cfgmod.CONFIG_PATH}")
say(f"  存在　　　：{'是' if cfgmod.CONFIG_PATH.exists() else '否'}")
say(f"  預設引擎　：{cfg.get('engine')}")
say(f"  目標語言　：{cfg.get('target_lang')}　備用：{cfg.get('alt_target_lang')}")
say(f"  串流輸出　：{cfg.get('stream_llm')}")
say(f"  自動備援　：{cfg.get('auto_fallback')}")
say(f"  備援順序　：{' → '.join(cfg.get('fallback_order') or [])}")
retry = cfg.get("retry") or {}
say(f"  重試設定　：{retry.get('attempts')} 次，起始 {retry.get('base_seconds')} 秒")
say(f"  連線逾時　：{cfg.get('timeout')} 秒")

if cfgmod.last_decrypt_errors:
    say()
    say("  ⚠ 有金鑰解密失敗：")
    for label, msg in cfgmod.last_decrypt_errors:
        say(f"     {label}：{msg}")
say()

sel = cfg.get("select_to_translate") or {}
say("[選取即翻譯]")
say(f"  啟用　　　：{sel.get('enabled')}")
say(f"  反應方式　：{sel.get('mode')}")
say(f"  字數範圍　：{sel.get('min_chars')} ～ {sel.get('max_chars')}")
say(f"  最小拖曳　：{sel.get('min_drag_px')} px　冷卻：{sel.get('cooldown_seconds')} 秒")
say(f"  略過目標語言：{sel.get('skip_if_target_lang')}")
say()

say("[金鑰狀態]")
eng_cfg = cfg.get("engines") or {}
say(f"  Gemini 金鑰　：{mask(eng_cfg.get('gemini', {}).get('api_key', ''))}")
say(f"  Gemini 模型　：{eng_cfg.get('gemini', {}).get('model')}")
say(f"  DeepL 金鑰　 ：{mask(eng_cfg.get('deepl', {}).get('api_key', ''))}")
say(f"  OpenAI 端點　：{eng_cfg.get('openai', {}).get('base_url')}")
say(f"  OpenAI 金鑰　：{mask(eng_cfg.get('openai', {}).get('api_key', ''))}")
say(f"  OpenAI 模型　：{eng_cfg.get('openai', {}).get('model')}")
say(f"  Libre 網址　 ：{eng_cfg.get('libretranslate', {}).get('url') or '（未填）'}")
say()

say("[引擎可用性判定]")
for eid, label, reason in enginesmod.listing(cfg):
    say(f"  {'✗' if reason else '✓'} {eid:16} {label}")
    if reason:
        say(f"      原因：{reason}")
say()

# ---------------------------------------------------------------- 實測
SAMPLE = "nice to meet you"
TARGET = cfg.get("target_lang", "zh-TW")

say("=" * 58)
say(f"[實際翻譯測試]　原文：{SAMPLE!r}　→　{TARGET}")
say("=" * 58)

results = []
for eid, label, reason in enginesmod.listing(cfg):
    say()
    say(f"── {label}（{eid}）")
    if reason:
        say(f"   略過：{reason}")
        results.append((eid, "略過", reason))
        continue

    engine = enginesmod.build(eid, cfg)
    started = time.time()
    try:
        res = engine.translate(SAMPLE, "auto", TARGET)
        took = time.time() - started
        say(f"   ✓ 成功（{took:.1f} 秒）")
        say(f"     譯文：{res.text}")
        say(f"     偵測語言：{res.detected_lang or '—'}")
        # 檢查有沒有亂碼跡象
        if any(ch in res.text for ch in "åÃ¤¾«"):
            say("     ⚠ 譯文疑似編碼異常")
        results.append((eid, "成功", res.text[:40]))
    except enginesmod.EngineError as exc:
        took = time.time() - started
        say(f"   ✗ 失敗（{took:.1f} 秒）")
        say(f"     {exc}")
        say(f"     可重試：{exc.retryable}　HTTP：{exc.status or '—'}")
        results.append((eid, "失敗", str(exc)[:120]))
    except Exception as exc:
        took = time.time() - started
        say(f"   ✗ 例外（{took:.1f} 秒）：{type(exc).__name__}: {exc}")
        say("     " + traceback.format_exc().replace("\n", "\n     ")[:600])
        results.append((eid, "例外", f"{type(exc).__name__}: {exc}"))

# ---------------------------------------------------------------- 串流實測
say()
say("=" * 58)
say("[串流測試]（只測支援串流的引擎）")
say("=" * 58)
for eid, label, reason in enginesmod.listing(cfg):
    cls = enginesmod.ENGINES.get(eid)
    if reason or not (cls and cls.supports_stream):
        continue
    say()
    say(f"── {label}（{eid}）")
    engine = enginesmod.build(eid, cfg)
    tokens = []
    try:
        res = engine.translate_stream(SAMPLE, "auto", TARGET, on_token=tokens.append)
        say(f"   ✓ 成功，共 {len(tokens)} 段")
        say(f"     譯文：{res.text}")
        if any(ch in res.text for ch in "åÃ¤¾«"):
            say("     ⚠ 譯文疑似編碼異常（串流解碼問題）")
    except Exception as exc:
        say(f"   ✗ {type(exc).__name__}: {exc}")

# ---------------------------------------------------------------- 總結
say()
say("=" * 58)
say("[總結]")
say("=" * 58)
ok = [r for r in results if r[1] == "成功"]
bad = [r for r in results if r[1] in ("失敗", "例外")]
for eid, state, detail in results:
    say(f"  {state:4}  {eid:16} {detail}")
say()
if ok:
    say(f"  可以正常翻譯的引擎：{', '.join(r[0] for r in ok)}")
    say(f"  建議把預設引擎設成：{ok[0][0]}")
else:
    say("  ⚠ 目前沒有任何引擎可以成功翻譯。")
    say("    最可能的原因是網路被擋，或所有金鑰都有問題。")
if bad:
    say()
    say(f"  失敗的引擎：{', '.join(r[0] for r in bad)}")

report = ROOT / "DIAGNOSTIC-ENGINES.txt"
try:
    report.write_text(buf.getvalue(), encoding="utf-8")
    print()
    print(f"報告已寫入：{report}")
    print("請回對話告訴 Claude「跑好了」，他會直接讀這個檔案。")
except Exception as exc:
    print(f"報告寫入失敗：{exc}")

print()
input("按 Enter 關閉...")
