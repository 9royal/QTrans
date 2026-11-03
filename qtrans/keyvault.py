"""API 金鑰的本機加密保護（Windows DPAPI）。

用 Windows 的 CryptProtectData / CryptUnprotectData 加密金鑰後再存進設定檔，
金鑰由 Windows 依「目前登入使用者的憑證」自動衍生，程式不必自己保管密碼。

效果：
  * 其他 Windows 使用者帳號無法解密。
  * 把 config.json 複製到別台電腦也解不開。
  * 同帳號下的惡意程式若直接呼叫 CryptUnprotectData，因為缺少下面這組
    自訂 entropy（相當於第二把鑰匙），仍然解不開。

重要限制：DPAPI 綁使用者帳號，所以「換電腦」或「重裝 Windows」之後舊的
密文一定解不開。這不是錯誤而是預期行為，呼叫端必須能優雅降級
（清空欄位並請使用者重新輸入），不可以讓程式當掉。
"""
from __future__ import annotations

import base64
import sys

IS_WINDOWS = sys.platform == "win32"

# 密文前綴：用來分辨「已加密」與「舊版留下的明文」，方便無痛遷移。
PREFIX = "dpapi:v1:"

# 自訂 entropy（第二把鑰匙）。改動這個值會讓所有既有密文失效。
_ENTROPY = b"QTrans/v1/api-key-protection"

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class VaultError(RuntimeError):
    pass


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    _BLOB_P = ctypes.POINTER(_Blob)

    _crypt32.CryptProtectData.argtypes = [
        _BLOB_P,            # pDataIn
        wintypes.LPCWSTR,   # szDataDescr
        _BLOB_P,            # pOptionalEntropy
        ctypes.c_void_p,    # pvReserved
        ctypes.c_void_p,    # pPromptStruct
        wintypes.DWORD,     # dwFlags
        _BLOB_P,            # pDataOut
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        _BLOB_P,                        # pDataIn
        ctypes.POINTER(wintypes.LPWSTR),  # ppszDataDescr
        _BLOB_P,                        # pOptionalEntropy
        ctypes.c_void_p,                # pvReserved
        ctypes.c_void_p,                # pPromptStruct
        wintypes.DWORD,                 # dwFlags
        _BLOB_P,                        # pDataOut
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _to_blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data),
                     ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    def _take_blob(blob: _Blob) -> bytes:
        """把 Windows 配置的緩衝區複製出來，並立刻 LocalFree 避免記憶體洩漏。"""
        if not blob.pbData:
            return b""
        try:
            return bytes(bytearray(
                ctypes.cast(blob.pbData,
                            ctypes.POINTER(ctypes.c_byte * blob.cbData)).contents))
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def _protect(raw: bytes) -> bytes:
        data_in, _keep_in = _to_blob(raw)
        ent_in, _keep_ent = _to_blob(_ENTROPY)
        data_out = _Blob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(data_in), None, ctypes.byref(ent_in),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
        if not ok:
            raise VaultError(f"DPAPI 加密失敗（錯誤碼 {ctypes.get_last_error()}）")
        return _take_blob(data_out)

    def _unprotect(cipher: bytes) -> bytes:
        data_in, _keep_in = _to_blob(cipher)
        ent_in, _keep_ent = _to_blob(_ENTROPY)
        data_out = _Blob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, ctypes.byref(ent_in),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
        if not ok:
            raise VaultError(
                "DPAPI 解密失敗：可能是在其他電腦或其他 Windows 帳號加密的，"
                f"或設定檔已損毀（錯誤碼 {ctypes.get_last_error()}）")
        return _take_blob(data_out)

    AVAILABLE = True

else:  # 非 Windows：沒有等效的免維護金鑰機制，不假裝有加密
    AVAILABLE = False

    def _protect(raw: bytes) -> bytes:  # pragma: no cover
        raise VaultError("此平台不支援 DPAPI")

    def _unprotect(cipher: bytes) -> bytes:  # pragma: no cover
        raise VaultError("此平台不支援 DPAPI")


# --------------------------------------------------------------------------
def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plain: str) -> str:
    """把明文金鑰加密成可放進 JSON 的字串。

    空字串原樣回傳；本平台無 DPAPI 時也原樣回傳（不加前綴，誠實標示未加密）。
    """
    if not plain:
        return ""
    if is_encrypted(plain):
        return plain          # 已經是密文，不要重複加密
    if not AVAILABLE:
        return plain
    try:
        return PREFIX + base64.b64encode(_protect(plain.encode("utf-8"))).decode("ascii")
    except VaultError:
        # 加密失敗時寧可存明文也不要讓使用者的金鑰整個消失
        return plain


def decrypt(stored: str) -> tuple[str, str]:
    """解密設定檔裡的值。

    回傳 (明文, 錯誤訊息)。錯誤訊息為空字串代表成功。
    沒有前綴的值視為舊版明文，直接原樣回傳（供無痛遷移）。
    """
    if not stored:
        return "", ""
    if not is_encrypted(stored):
        return stored, ""     # 舊版明文，下次存檔時就會被加密
    payload = stored[len(PREFIX):]
    if not AVAILABLE:
        return "", "這台電腦不是 Windows，無法解密以 DPAPI 保護的金鑰。"
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        return "", "金鑰欄位損毀（Base64 解碼失敗），請重新輸入。"
    try:
        return _unprotect(raw).decode("utf-8"), ""
    except (VaultError, UnicodeDecodeError) as exc:
        return "", str(exc)


def describe_backend() -> str:
    if AVAILABLE:
        return "Windows DPAPI（綁定目前使用者帳號）"
    return "此平台無加密後端，金鑰以明文儲存"


def _self_test() -> int:
    """自我檢測：python -m qtrans.keyvault

    這段 ctypes 呼叫只能在真正的 Windows 上驗證，所以提供一個指令讓你
    在自己的電腦上五秒確認加解密真的通。
    """
    print(f"平台        ：{sys.platform}")
    print(f"加密後端    ：{describe_backend()}")
    if not AVAILABLE:
        print("\n非 Windows，略過 DPAPI 實測。")
        return 0

    sample = "sk-test-金鑰-ABCDEFG-1234567890"
    try:
        cipher = encrypt(sample)
    except Exception as exc:
        print(f"\n✗ 加密丟出例外：{type(exc).__name__}: {exc}")
        return 1

    if not is_encrypted(cipher):
        print("\n✗ 加密沒有產生密文（回傳的還是明文）")
        return 1
    if sample in cipher:
        print("\n✗ 密文裡看得到明文")
        return 1

    print(f"\n密文前 60 字：{cipher[:60]}…")
    print(f"密文長度    ：{len(cipher)}")

    plain, err = decrypt(cipher)
    if err:
        print(f"\n✗ 解密失敗：{err}")
        return 1
    if plain != sample:
        print(f"\n✗ 解密結果不符：{plain!r}")
        return 1

    print("\n✓ DPAPI 加解密正常，金鑰會被加密儲存。")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
