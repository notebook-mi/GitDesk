"""GitHub 热门榜单抓取。

两种榜单来源：

1. **趋势榜**：解析 https://github.com/trending（今日 / 本周 / 本月），
   除了总星标外还能拿到「本周期新增星标」，最能反映当下热度；
2. **星标总榜**：调用 GitHub 搜索 API（`stars:>1000` 按星标倒序），
   即历史累计 Top N，不受时间窗口影响。

抓取结果统一归一化为同一结构，并把简介翻译为简体中文，供界面层直接渲染。
不依赖 dulwich，也不需要用户授权（有令牌时自动使用以提升配额）。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from .logger import get_logger
from .translator import clean, has_chinese, translate_descriptions

log = get_logger("trending")

TRENDING_URL = "https://github.com/trending"
SEARCH_URL = "https://api.github.com/search/repositories"

PERIODS: tuple[tuple[str, str], ...] = (
    ("daily", "今日"),
    ("weekly", "本周"),
    ("monthly", "本月"),
)
PERIOD_LABEL = dict(PERIODS)

BOARDS: tuple[tuple[str, str], ...] = (
    ("trending", "趋势榜（当期最热）"),
    ("stars", "星标总榜（历史累计）"),
)
BOARD_LABEL = dict(BOARDS)

TIMEOUT = (8, 25)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
AVATAR_TIMEOUT = (5, 10)

_ARTICLE_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
_NAME_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*?href="/([^"]+)"')
_DESC_RE = re.compile(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', re.S)
_LANG_RE = re.compile(r'itemprop="programmingLanguage"[^>]*>([^<]+)<')
_LANG_COLOR_RE = re.compile(r'repo-language-color"\s+style="background-color:\s*([^";]+)')
_STARS_RE = re.compile(r'href="[^"]*/stargazers"[^>]*>(.*?)</a>', re.S)
_FORKS_RE = re.compile(r'href="[^"]*/forks"[^>]*>(.*?)</a>', re.S)
_PERIOD_STARS_RE = re.compile(r'([\d,]+)\s*stars?\s+(?:today|this week|this month)')
_NUM_RE = re.compile(r"[\d,]+")
_TAG_RE = re.compile(r"<[^>]+>")


class TrendingError(Exception):
    """榜单抓取失败。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.message}\n{self.detail}" if self.detail else self.message


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return sess


def _num(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    match = _NUM_RE.search(str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return 0


def _text(raw: str) -> str:
    return clean(_TAG_RE.sub(" ", raw or ""))


def _wrap_error(exc: Exception, action: str) -> TrendingError:
    if isinstance(exc, requests.exceptions.Timeout):
        return TrendingError(f"{action}超时，请检查网络或代理设置后重试。", str(exc))
    if isinstance(exc, requests.exceptions.ConnectionError):
        return TrendingError(f"{action}失败：无法连接到 GitHub，请检查网络。", str(exc))
    if isinstance(exc, requests.exceptions.RequestException):
        return TrendingError(f"{action}失败：{exc}", str(exc))
    return TrendingError(f"{action}失败：{exc}", str(exc))


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def parse_trending_html(page: str, limit: int = 10, period: str = "daily") -> list[dict]:
    """从 GitHub Trending 页面 HTML 中解析榜单条目。"""
    items: list[dict] = []
    for block in _ARTICLE_RE.findall(page or ""):
        name_match = _NAME_RE.search(block)
        if not name_match:
            continue
        full_name = name_match.group(1).strip().strip("/")
        if not full_name or "/" not in full_name:
            continue
        desc_match = _DESC_RE.search(block)
        description = _text(desc_match.group(1)) if desc_match else ""
        lang_match = _LANG_RE.search(block)
        color_match = _LANG_COLOR_RE.search(block)
        stars_match = _STARS_RE.search(block)
        forks_match = _FORKS_RE.search(block)
        period_match = _PERIOD_STARS_RE.search(block)
        owner, _, name = full_name.partition("/")
        items.append(
            {
                "full_name": full_name,
                "owner": owner,
                "name": name,
                "description": description,
                "description_zh": description,
                "language": (lang_match.group(1).strip() if lang_match else ""),
                "language_color": (color_match.group(1).strip() if color_match else ""),
                "stars": _num(_text(stars_match.group(1))) if stars_match else 0,
                "forks": _num(_text(forks_match.group(1))) if forks_match else 0,
                "stars_period": _num(period_match.group(1)) if period_match else 0,
                "period_label": PERIOD_LABEL.get(period, ""),
                "html_url": f"https://github.com/{full_name}",
                "clone_url": f"https://github.com/{full_name}.git",
                "avatar_url": _avatar_url(owner),
                "topics": [],
                "source": "trending",
                "platform": "github",
                "platform_label": "GitHub",
            }
        )
        if len(items) >= limit:
            break
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


def _avatar_url(owner: str) -> str:
    # github.com/<owner>.png 会 302 到该用户头像，无需调用 API
    return f"https://github.com/{owner}.png?size=96" if owner else ""


def _normalize_search_item(raw: dict, rank: int) -> dict:
    owner = (raw.get("owner") or {}).get("login") or ""
    full_name = raw.get("full_name") or ""
    if not owner and "/" in full_name:
        owner = full_name.split("/")[0]
    description = clean(raw.get("description") or "")
    topics = [str(t) for t in (raw.get("topics") or [])][:4]
    return {
        "full_name": full_name,
        "owner": owner,
        "name": raw.get("name") or full_name.split("/")[-1],
        "description": description,
        "description_zh": description,
        "language": raw.get("language") or "",
        "language_color": "",
        "stars": _num(raw.get("stargazers_count")),
        "forks": _num(raw.get("forks_count")),
        "stars_period": 0,
        "period_label": "累计",
        "html_url": raw.get("html_url") or f"https://github.com/{full_name}",
        "clone_url": raw.get("clone_url") or f"https://github.com/{full_name}.git",
        "avatar_url": (raw.get("owner") or {}).get("avatar_url") or _avatar_url(owner),
        "topics": topics,
        "source": "stars",
        "platform": "github",
        "platform_label": "GitHub",
        "rank": rank,
    }


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def fetch_trending(period: str = "daily", limit: int = 10, token: str = "") -> list[dict]:
    """抓取 GitHub 趋势榜。"""
    if period not in PERIOD_LABEL:
        period = "daily"
    headers = {"Accept": "text/html,application/xhtml+xml"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = _session().get(
            TRENDING_URL, params={"since": period}, headers=headers, timeout=TIMEOUT
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise _wrap_error(exc, "获取 GitHub 趋势榜") from exc
    items = parse_trending_html(resp.text, limit=limit, period=period)
    if not items:
        raise TrendingError(
            "未能从 GitHub 趋势榜页面解析出项目，可能是页面结构发生变化或网络被拦截。"
        )
    return items


def fetch_top_stars(limit: int = 10, token: str = "") -> list[dict]:
    """抓取 GitHub 星标总榜（历史累计 Top N）。"""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "q": "stars:>1000",
        "sort": "stars",
        "order": "desc",
        "per_page": max(1, min(limit, 50)),
    }
    try:
        resp = _session().get(SEARCH_URL, params=params, headers=headers, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_error(exc, "获取 GitHub 星标总榜") from exc
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        raise TrendingError(
            "GitHub 接口调用频率超限。\n未授权请求每小时仅 60 次，可在「账号设置」中配置 GitHub 令牌提升配额。"
        )
    if resp.status_code >= 400:
        raise TrendingError(f"获取星标总榜失败（HTTP {resp.status_code}）。", resp.text[:400])
    data = resp.json() if resp.content else {}
    items = data.get("items") or []
    return [_normalize_search_item(raw, rank) for rank, raw in enumerate(items, start=1)][:limit]


def fetch_board(
    kind: str = "trending",
    period: str = "daily",
    limit: int = 10,
    token: str = "",
    translate: bool = True,
    with_avatars: bool = True,
    progress=None,
) -> dict:
    """抓取榜单 + 中文化简介，返回可直接渲染的数据包。"""
    def report(message: str) -> None:
        if progress is not None:
            try:
                progress(message)
            except Exception:  # noqa: BLE001
                pass

    kind = kind if kind in BOARD_LABEL else "trending"
    period = period if period in PERIOD_LABEL else "daily"
    limit = max(1, min(int(limit or 10), 30))

    if kind == "stars":
        report("正在获取 GitHub 星标总榜…")
        items = fetch_top_stars(limit=limit, token=token)
        source_label = BOARD_LABEL["stars"]
    else:
        report(f"正在获取 GitHub 趋势榜（{PERIOD_LABEL[period]}）…")
        items = fetch_trending(period=period, limit=limit, token=token)
        source_label = f"{BOARD_LABEL['trending']} · {PERIOD_LABEL[period]}"

    translated = 0
    if translate and items:
        pending = [it for it in items if it.get("description") and not has_chinese(it["description"])]
        if pending:
            report(f"正在翻译 {len(pending)} 条项目简介…")
            translated = translate_descriptions(items, progress=report)

    if with_avatars and items:
        report("正在加载项目头像…")
        _attach_avatars(items)

    return {
        "items": items,
        "kind": kind,
        "period": period,
        "source_label": source_label,
        "limit": limit,
        "translated": translated,
        "total": len(items),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "translated_state": _translate_state(items),
    }


def _translate_state(items: list[dict]) -> str:
    """统计简介中文化的完成情况。"""
    if not items:
        return "无数据"
    described = [it for it in items if it.get("description")]
    if not described:
        return "这些项目未填写简介"
    done = 0
    for item in described:
        original = item.get("description", "")
        current = item.get("description_zh") or ""
        if has_chinese(original) or (current and current != original):
            done += 1
    if done >= len(described):
        return "简介已全部中文化"
    return f"已中文化 {done}/{len(described)} 条简介"


def _attach_avatars(items: list[dict], workers: int = 6) -> None:
    """并发下载头像二进制，失败时留空（界面自行降级为首字母徽标）。"""

    def load(item: dict) -> None:
        url = item.get("avatar_url") or ""
        if not url:
            return
        try:
            resp = _session().get(url, timeout=AVATAR_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                item["avatar_bytes"] = resp.content
        except Exception:  # noqa: BLE001 - 头像失败不影响主体展示
            log.debug("头像下载失败: %s", url)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        list(pool.map(load, items))
