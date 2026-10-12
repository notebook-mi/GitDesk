"""仓库简介中文化。

GitHub 上的项目简介以英文为主，本模块把任意语言的简介统一翻译为简体中文：

* 多来源回退：Google 免密翻译接口 → MyMemory → 保留原文；
* 磁盘缓存：同一段文本只翻译一次，缓存落在数据目录的 cache/translate-zh.json；
* 纯离线可用：翻译服务不可达时不会阻塞界面，直接回退为原文。

所有函数均为纯函数式，不依赖 Qt，便于独立测试。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .logger import get_logger
from .paths import data_dir

log = get_logger("translate")

TARGET = "zh-CN"
TIMEOUT = (6, 14)
MAX_CHARS = 480  # MyMemory 免费额度对单次请求有长度限制

_GOOGLE_URL = "https://clients5.google.com/translate_a/t"
_GOOGLE_FALLBACK_URL = "https://translate.googleapis.com/translate_a/single"
_MYMEMORY_URL = "https://api.mymemory.translated.net/get"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_lock = threading.RLock()
_cache: dict[str, str] | None = None
_session: requests.Session | None = None
_preferred = "google"
_stats = {"hit": 0, "miss": 0, "failed": 0}

USER_AGENT = "GitDesk/1.0 (repo description translator)"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #


def clean(text: str) -> str:
    """清理 HTML 标签与多余空白。"""
    if not text:
        return ""
    text = html.unescape(str(text))
    text = _TAG_RE.sub(" ", text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def has_chinese(text: str, threshold: float = 0.12) -> bool:
    """判断文本是否已经是中文（按汉字占比）。"""
    text = clean(text)
    if not text:
        return False
    letters = [ch for ch in text if not ch.isspace()]
    if not letters:
        return False
    cjk = sum(1 for ch in letters if _CJK_RE.match(ch))
    return cjk / len(letters) >= threshold


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def cache_file() -> Path:
    return data_dir() / "cache" / "translate-zh.json"


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return _session


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    data: dict[str, str] = {}
    path = cache_file()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}
    except Exception:  # noqa: BLE001 - 缓存损坏时忽略即可
        log.debug("翻译缓存读取失败，已忽略", exc_info=True)
    _cache = data
    return data


def _save_cache() -> None:
    if _cache is None:
        return
    path = cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_cache, ensure_ascii=False, indent=0), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        log.debug("翻译缓存写入失败", exc_info=True)


def clear_cache() -> int:
    """清空翻译缓存，返回被清理的条目数。"""
    with _lock:
        data = _load_cache()
        count = len(data)
        data.clear()
        _save_cache()
    log.info("已清空翻译缓存，共 %d 条", count)
    return count


def cache_size() -> int:
    with _lock:
        return len(_load_cache())


def stats() -> dict:
    data = {"cache": cache_size(), "file": str(cache_file()), "preferred": _preferred}
    data.update(_stats)
    return data


# --------------------------------------------------------------------------- #
# 翻译提供方
# --------------------------------------------------------------------------- #


def _flatten(obj) -> str:
    """把各家返回的嵌套结构压平成字符串。"""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        if obj and isinstance(obj[0], str):
            return "".join(item for item in obj if isinstance(item, str))
        parts = []
        for item in obj:
            if isinstance(item, list) and item and isinstance(item[0], str):
                parts.append(item[0])
            else:
                parts.append(_flatten(item))
        return "".join(part for part in parts if part)
    if isinstance(obj, dict):
        for key in ("translatedText", "text", "translation"):
            if key in obj:
                return _flatten(obj[key])
    return ""


def _via_google(text: str) -> str:
    resp = _sess().get(
        _GOOGLE_URL,
        params={"client": "dict-chrome-ex", "sl": "auto", "tl": TARGET, "q": text},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return _flatten(resp.json())


def _via_google_single(text: str) -> str:
    resp = _sess().get(
        _GOOGLE_FALLBACK_URL,
        params={"client": "gtx", "sl": "auto", "tl": TARGET, "dt": "t", "q": text},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return _flatten(resp.json())


def _via_mymemory(text: str) -> str:
    resp = _sess().get(
        _MYMEMORY_URL,
        params={"q": text[:MAX_CHARS], "langpair": f"en|{TARGET}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("responseStatus")) not in ("200", "None"):
        raise ValueError(f"MyMemory 返回状态异常: {data.get('responseStatus')}")
    out = _flatten(data.get("responseData") or {})
    upper = out.upper()
    if "MYMEMORY WARNING" in upper or "QUERY LENGTH LIMIT" in upper or "INVALID" in upper:
        raise ValueError("MyMemory 配额或参数提示，忽略该结果")
    return out


_PROVIDERS = {
    "google": _via_google,
    "google_single": _via_google_single,
    "mymemory": _via_mymemory,
}

_ORDER = ("google", "mymemory", "google_single")


def _translate_online(text: str) -> str:
    """按优先级尝试各翻译源，全部失败则抛出最后一个异常。"""
    global _preferred
    order = list(_ORDER)
    if _preferred in order:
        order.remove(_preferred)
        order.insert(0, _preferred)
    last_error: Exception | None = None
    for name in order:
        try:
            out = clean(_PROVIDERS[name](text))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.debug("翻译源 %s 失败: %s", name, exc)
            continue
        if out and out.strip():
            if name != _preferred:
                log.info("翻译源切换为 %s", name)
                _preferred = name
            return out.strip()
    _stats["failed"] += 1
    raise last_error or RuntimeError("所有翻译源均不可用")


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #


def translate(text: str, use_cache: bool = True) -> str:
    """把文本翻译为简体中文；已含中文或翻译失败时原样返回。"""
    original = clean(text)
    if not original:
        return ""
    if has_chinese(original):
        return original

    key = _key(original)
    if use_cache:
        with _lock:
            cached = _load_cache().get(key)
        if cached:
            _stats["hit"] += 1
            return cached

    _stats["miss"] += 1
    try:
        result = _translate_online(original)
    except Exception as exc:  # noqa: BLE001
        log.debug("翻译失败，保留原文: %s", exc)
        return original

    if not result or result == original:
        return original

    with _lock:
        _load_cache()[key] = result
        _save_cache()
    return result


def translate_many(
    texts: list[str],
    workers: int = 5,
    progress=None,
) -> list[str]:
    """并发翻译一组文本，返回与输入等长的结果列表。"""
    results: list[str] = [""] * len(texts)
    if not texts:
        return results
    total = len(texts)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {pool.submit(translate, item): idx for idx, item in enumerate(texts)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:  # noqa: BLE001
                results[idx] = clean(texts[idx])
            done += 1
            if progress is not None:
                try:
                    progress(f"正在翻译简介 {done}/{total} …")
                except Exception:  # noqa: BLE001
                    pass
    return results


def translate_descriptions(repos: list[dict], fold: str = "description", out: str = "description_zh",
                           progress=None) -> int:
    """就地为一组仓库补上中文简介，返回翻译成功的条数。"""
    texts = [clean(repo.get(fold, "")) for repo in repos]
    results = translate_many(texts, progress=progress)
    changed = 0
    for repo, original, translated in zip(repos, texts, results):
        repo[out] = translated or original
        if translated and translated != original:
            changed += 1
    return changed


def reset_stats() -> None:
    _stats.update({"hit": 0, "miss": 0, "failed": 0})
