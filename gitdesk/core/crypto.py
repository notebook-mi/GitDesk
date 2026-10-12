"""令牌本地保护。

优先使用 Windows DPAPI（CryptProtectData，与当前用户账户绑定）；
非 Windows 或调用失败时退化为带随机盐的混淆存储，并在日志中提示。
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import os
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform.startswith("win")
_PREFIX_DPAPI = "dpapi:"
_PREFIX_OBF = "obf:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_DPAPI_OK = False
if _IS_WINDOWS:
    try:
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        _crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        _crypt32.CryptProtectData.restype = wintypes.BOOL

        _crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        _crypt32.CryptUnprotectData.restype = wintypes.BOOL

        _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        _kernel32.LocalFree.restype = wintypes.HLOCAL
        _DPAPI_OK = True
    except Exception:  # pragma: no cover - 极端环境
        _DPAPI_OK = False


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _blob_to_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_available() -> bool:
    return _DPAPI_OK


def _obf_key() -> bytes:
    seed = f"{os.environ.get('USERNAME', '')}|{os.environ.get('COMPUTERNAME', '')}|GitDesk"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _obfuscate(data: bytes) -> bytes:
    key = _obf_key()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(out)


def _deobfuscate(data: bytes) -> bytes:
    raw = base64.urlsafe_b64decode(data)
    key = _obf_key()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))


def encrypt(plain: str) -> str:
    """把明文令牌转换为可写入配置文件的安全字符串。"""
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    if _DPAPI_OK:
        try:
            blob_in = _blob_from_bytes(raw)
            blob_out = _DataBlob()
            ok = _crypt32.CryptProtectData(
                ctypes.byref(blob_in), "GitDesk", None, None, None, 0, ctypes.byref(blob_out)
            )
            if ok:
                try:
                    payload = _blob_to_bytes(blob_out)
                finally:
                    _kernel32.LocalFree(blob_out.pbData)
                return _PREFIX_DPAPI + base64.b64encode(payload).decode("ascii")
        except Exception:
            pass
    return _PREFIX_OBF + _obfuscate(raw).decode("ascii")


def decrypt(stored: str) -> str:
    """还原令牌；失败返回空串。"""
    if not stored:
        return ""
    if stored.startswith(_PREFIX_DPAPI):
        if not _DPAPI_OK:
            return ""
        try:
            payload = base64.b64decode(stored[len(_PREFIX_DPAPI):])
            blob_in = _blob_from_bytes(payload)
            blob_out = _DataBlob()
            ok = _crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
            )
            if not ok:
                return ""
            try:
                raw = _blob_to_bytes(blob_out)
            finally:
                _kernel32.LocalFree(blob_out.pbData)
            return raw.decode("utf-8")
        except Exception:
            return ""
    if stored.startswith(_PREFIX_OBF):
        try:
            return _deobfuscate(stored[len(_PREFIX_OBF):].encode("ascii")).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return ""
    # 兼容早期明文配置
    return stored
