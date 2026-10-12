"""GitHub / Gitee 远程平台 API 客户端。

统一输出仓库数据结构，供界面层直接消费。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests

from .logger import get_logger

log = get_logger("api")

TIMEOUT = (10, 30)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def _friendly_status(status: int, body: str) -> str:
    if status == 401:
        return "身份验证失败：令牌无效或已过期，请重新生成访问令牌。"
    if status == 403:
        if "rate limit" in body.lower():
            return "接口调用频率超限，请稍后再试（未认证请求限额较低）。"
        return "访问被拒绝：令牌权限不足，请确认已勾选所需权限范围。"
    if status == 404:
        return "资源不存在或无权访问。"
    if status == 422:
        return "请求参数不被接受，请检查输入内容。"
    if status >= 500:
        return "远程服务暂时不可用，请稍后重试。"
    return f"请求失败（HTTP {status}）。"


@dataclass
class PlatformMeta:
    key: str
    label: str
    api_base: str
    web_host: str
    token_url: str
    token_label: str
    token_hint: str
    scopes: str = ""
    accent: str = "#2f6f4f"


PLATFORMS: dict[str, PlatformMeta] = {
    "github": PlatformMeta(
        key="github",
        label="GitHub",
        api_base="https://api.github.com",
        web_host="https://github.com",
        token_url="https://github.com/settings/tokens/new?scopes=repo,read:user,delete_repo&description=GitDesk",
        token_label="Personal Access Token",
        token_hint=(
            "建议创建 classic Token 并勾选 repo、read:user 权限；"
            "若需在 GitDesk 内删除远程仓库，还需勾选 delete_repo。"
        ),
        scopes="repo, read:user, delete_repo",
        accent="#24292f",
    ),
    "gitee": PlatformMeta(
        key="gitee",
        label="Gitee 码云",
        api_base="https://gitee.com/api/v5",
        web_host="https://gitee.com",
        token_url="https://gitee.com/personal_access_tokens",
        token_label="私人令牌 Private Token",
        token_hint="在「设置 → 私人令牌」中生成，勾选 projects、pull_requests 权限即可。",
        scopes="projects, pull_requests",
        accent="#c71d23",
    ),
}


class RemoteClient:
    """单个平台的 API 客户端。"""

    def __init__(self, platform: str, token: str, username: str = "", proxy: str = "") -> None:
        if platform not in PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}")
        self.platform = platform
        self.meta = PLATFORMS[platform]
        self.token = (token or "").strip()
        self.username = (username or "").strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "GitDesk/1.0 (+https://example.local)",
                "Accept": "application/json",
            }
        )
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        if platform == "github":
            if self.token:
                self.session.headers["Authorization"] = f"Bearer {self.token}"
            self.session.headers["X-GitHub-Api-Version"] = "2022-11-28"
        self.last_rate: dict = {}

    # ---------------- 底层请求 ---------------- #

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else "/" + path
        return f"{self.meta.api_base}{path}"

    def _params(self, params: dict | None) -> dict:
        params = dict(params or {})
        if self.platform == "gitee" and self.token:
            params["access_token"] = self.token
        return params

    def _request(self, method: str, path: str, params: dict | None = None, **kwargs) -> Any:
        url = self._url(path)
        try:
            resp = self.session.request(
                method, url, params=self._params(params), timeout=TIMEOUT, **kwargs
            )
        except requests.exceptions.SSLError as exc:
            raise ApiError("TLS 连接失败，请检查网络代理设置。", detail=str(exc)) from exc
        except requests.exceptions.Timeout as exc:
            raise ApiError("请求超时，请检查网络连接后重试。", detail=str(exc)) from exc
        except requests.exceptions.ConnectionError as exc:
            raise ApiError("无法连接到服务器，请检查网络或代理设置。", detail=str(exc)) from exc
        except requests.RequestException as exc:
            raise ApiError(f"请求异常：{exc}", detail=str(exc)) from exc

        self._capture_rate(resp)
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:
                pass
            message = _friendly_status(resp.status_code, body)
            if resp.status_code == 422 and body:
                message += f"\n服务端返回：{body}"
            raise ApiError(message, status=resp.status_code, detail=body)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise ApiError("服务端返回了无法解析的数据。", detail=str(exc)) from exc

    def _capture_rate(self, resp: requests.Response) -> None:
        headers = resp.headers
        limit = headers.get("X-RateLimit-Limit")
        if limit:
            self.last_rate = {
                "limit": limit,
                "remaining": headers.get("X-RateLimit-Remaining", ""),
                "reset": headers.get("X-RateLimit-Reset", ""),
            }

    # ---------------- 业务接口 ---------------- #

    def verify(self) -> dict:
        """校验令牌并返回用户信息。"""
        data = self._request("GET", "/user")
        if not isinstance(data, dict):
            raise ApiError("无法获取用户信息。")
        login = data.get("login") or data.get("name") or ""
        return {
            "login": login,
            "name": data.get("name") or login,
            "avatar": data.get("avatar_url") or "",
            "email": data.get("email") or "",
            "repos": data.get("public_repos") or 0,
            "followers": data.get("followers") or 0,
            "html_url": data.get("html_url") or "",
            "platform": self.platform,
        }

    def list_repos(
        self,
        page: int = 1,
        per_page: int = 30,
        sort: str = "updated",
        visibility: str = "all",
    ) -> tuple[list[dict], bool]:
        """列出当前令牌可访问的仓库。返回 (列表, 是否还有下一页)。"""
        params = {"page": page, "per_page": per_page}
        if self.platform == "github":
            params.update(
                {
                    "sort": sort,
                    "direction": "desc",
                    "affiliation": "owner,collaborator,organization_member",
                    "visibility": visibility,
                }
            )
        else:
            params.update({"sort": sort, "direction": "desc"})
        data = self._request("GET", "/user/repos", params=params)
        items = data if isinstance(data, list) else []
        repos = [self.normalize(r) for r in items if isinstance(r, dict)]
        return repos, len(items) >= per_page

    def search_repos(
        self, query: str, page: int = 1, per_page: int = 30, scope: str = "all"
    ) -> tuple[list[dict], int]:
        """搜索仓库。返回 (列表, 总数)。"""
        query = (query or "").strip()
        if not query:
            return [], 0
        if self.platform == "github":
            q = query
            if scope == "mine" and self.username:
                q = f"{query} user:{self.username}"
            elif scope == "org":
                q = f"{query} org:{self.username}"
            data = self._request(
                "GET",
                "/search/repositories",
                params={"q": q, "page": page, "per_page": per_page, "sort": "stars"},
            )
            items = (data or {}).get("items") or []
            total = int((data or {}).get("total_count") or 0)
            return [self.normalize(r) for r in items], total

        data = self._request(
            "GET",
            "/search/repositories",
            params={"q": query, "page": page, "per_page": per_page, "order": "desc"},
        )
        items = data if isinstance(data, list) else []
        return [self.normalize(r) for r in items if isinstance(r, dict)], len(items)

    def repo_detail(self, full_name: str) -> dict:
        data = self._request("GET", f"/repos/{full_name}")
        return self.normalize(data or {})

    def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = False,
        auto_init: bool = False,
    ) -> dict:
        """在当前账号下新建远程仓库，返回归一化后的仓库信息。

        auto_init 默认关闭：远程仓库若带有初始提交，本地首次推送会因历史无关被拒。
        """
        name = (name or "").strip()
        if not name:
            raise ApiError("仓库名称不能为空。")
        payload: dict[str, Any] = {
            "name": name,
            "description": (description or "").strip(),
            "private": bool(private),
            "auto_init": bool(auto_init),
            "has_issues": True,
            "has_wiki": False,
        }
        meta = self.meta
        try:
            data = self._request("POST", "/user/repos", json=payload)
        except ApiError as exc:
            # 少数 Gitee 网关不接受 JSON 请求体，退化为表单提交再试一次
            if exc.status in (400, 415, 422) and self.platform == "gitee":
                form = {k: ("true" if v is True else "false" if v is False else v)
                        for k, v in payload.items()}
                try:
                    data = self._request("POST", "/user/repos", data=form)
                except ApiError as retry_exc:
                    raise self._create_error(retry_exc, name) from retry_exc
            else:
                raise self._create_error(exc, name) from exc
        repo = self.normalize(data or {})
        if not repo.get("full_name"):
            repo["full_name"] = f"{self.username}/{name}" if self.username else name
        if not repo.get("clone_url"):
            repo["clone_url"] = f"{meta.web_host}/{repo['full_name']}.git"
        return repo

    def delete_repo(self, full_name: str) -> str:
        """删除远程仓库（不可恢复）。返回被删除的仓库全名。"""
        full_name = (full_name or "").strip().strip("/")
        if not full_name or "/" not in full_name:
            raise ApiError("仓库标识无效，无法删除。")
        try:
            self._request("DELETE", f"/repos/{full_name}")
        except ApiError as exc:
            raise self._delete_error(exc, full_name) from exc
        return full_name

    def _delete_error(self, exc: ApiError, full_name: str) -> ApiError:
        """把删除失败的原始报文转成可读提示。"""
        if exc.status in (403, 401):
            scope = (
                "Gitee 私人令牌需勾选 projects 权限。"
                if self.platform == "gitee"
                else "GitHub 令牌需要勾选 delete_repo 权限（classic Token）。"
            )
            return ApiError(
                f"删除仓库「{full_name}」失败：令牌权限不足。\n{scope}",
                status=exc.status,
                detail=exc.detail,
            )
        if exc.status == 404:
            return ApiError(
                f"删除仓库「{full_name}」失败：仓库不存在，或当前账号不是该仓库的所有者。",
                status=exc.status,
                detail=exc.detail,
            )
        return ApiError(f"删除仓库「{full_name}」失败：{exc.message}", exc.status, exc.detail)

    def _create_error(self, exc: ApiError, name: str) -> ApiError:
        """把建仓失败的原始报文转成可读提示。"""
        detail = ""
        try:
            parsed = json.loads(exc.detail or "")
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            parts = []
            if parsed.get("message"):
                parts.append(str(parsed["message"]))
            for item in parsed.get("errors") or []:
                if isinstance(item, dict) and item.get("message"):
                    parts.append(str(item["message"]))
            detail = "；".join(parts)
        if exc.status == 422 or (detail and "exist" in detail.lower()):
            return ApiError(
                f"创建仓库「{name}」失败：该名称已被占用，或参数不符合平台要求。\n"
                + (f"服务端提示：{detail}" if detail else str(exc.message)),
                status=exc.status,
                detail=exc.detail,
            )
        if exc.status == 403:
            return ApiError(
                f"创建仓库「{name}」失败：令牌权限不足。\n"
                + (
                    "Gitee 私人令牌需勾选 projects 权限。"
                    if self.platform == "gitee"
                    else "GitHub 令牌需具备 repo（或 Contents 读写）权限。"
                ),
                status=exc.status,
                detail=exc.detail,
            )
        return ApiError(f"创建仓库「{name}」失败：{exc.message}", exc.status, exc.detail)

    # ---------------- 归一化 ---------------- #

    def normalize(self, raw: dict) -> dict:
        owner = raw.get("owner") or {}
        if isinstance(owner, dict):
            owner_login = owner.get("login") or owner.get("name") or ""
        else:
            owner_login = ""
        full_name = raw.get("full_name") or raw.get("path") or raw.get("name") or ""
        if not owner_login and "/" in full_name:
            owner_login = full_name.split("/")[0]
        host = self.meta.web_host
        git_url = raw.get("clone_url")
        if not git_url:
            git_url = f"{host}/{full_name}.git"
        return {
            "platform": self.platform,
            "platform_label": self.meta.label,
            "id": raw.get("id"),
            "name": raw.get("name") or full_name.split("/")[-1],
            "full_name": full_name,
            "owner": owner_login,
            "description": (raw.get("description") or "").strip(),
            "private": bool(raw.get("private")),
            "fork": bool(raw.get("fork")),
            "clone_url": git_url,
            "ssh_url": raw.get("ssh_url") or "",
            "html_url": raw.get("html_url") or f"{host}/{full_name}",
            "default_branch": raw.get("default_branch") or "main",
            "stars": raw.get("stargazers_count") or raw.get("stars_count") or 0,
            "forks": raw.get("forks_count") or 0,
            "watchers": raw.get("watchers_count") or raw.get("watchers") or 0,
            "language": raw.get("language") or "",
            "updated_at": (raw.get("updated_at") or raw.get("pushed_at") or "")[:19].replace("T", " "),
            "size_kb": raw.get("size") or 0,
        }


def make_client(cfg, platform: str) -> RemoteClient:
    """依据配置构造客户端。"""
    token = cfg.token(platform)
    if not token:
        raise ApiError(f"尚未配置 {PLATFORMS[platform].label} 访问令牌，请先在「账号设置」中完成配置。")
    return RemoteClient(
        platform,
        token,
        username=cfg.username(platform),
        proxy=cfg.get_setting("proxy", "") or "",
    )
