"""「发布到远程仓库」编排逻辑。

把本地仓库一键推送到新建的远程仓库，包含三步：
1. 配置（或更新）远程地址
2. 保证仓库至少有一个提交（可自动生成 README.md）
3. 推送当前分支并建立上游跟踪关系

与界面解耦，便于单独测试：远程地址可以是任意 Git 地址（含本地裸仓库）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from .gitengine import (
    CancelToken,
    CancelledError,
    Credential,
    GitEngine,
    GitError,
    ProgressCb,
)
from .logger import get_logger

log = get_logger("publish")

README_TEMPLATE = "# {title}\n\n{description}\n"


@dataclass
class PublishPlan:
    """一次发布所需的全部输入。"""

    path: str
    url: str
    remote_name: str = "origin"
    branch: str = ""
    commit_message: str = "Initial commit"
    author_name: str = "GitDesk"
    author_email: str = "gitdesk@localhost"
    create_readme: bool = False
    readme_title: str = ""
    readme_description: str = ""


@dataclass
class PublishResult:
    """发布结果，供界面展示与日志记录。"""

    branch: str
    remote_name: str
    remote_url: str
    created_commit: bool = False
    created_readme: bool = False
    staged_files: int = 0
    commit_sha: str = ""


def _report(progress: ProgressCb | None, message: str) -> None:
    log.info(message)
    if progress:
        try:
            progress(message)
        except Exception:  # 回调异常不应中断发布流程
            log.debug("进度回调异常", exc_info=True)


def _write_readme(path: str, title: str, description: str) -> None:
    body = README_TEMPLATE.format(title=title or os.path.basename(path),
                                  description=description or "").rstrip() + "\n"
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def repo_state(path: str) -> tuple[bool, bool]:
    """返回 (是否无任何提交, 是否完全为空)。"""
    if GitEngine.commit_count(path) > 0:
        return False, False
    status = GitEngine.status(path, untracked=True)
    staged = status.get("staged") or {}
    empty = not staged and not status.get("unstaged") and not status.get("untracked")
    return True, empty


def is_repo_empty(path: str) -> bool:
    """仓库是否既无提交也无任何待提交内容。"""
    return repo_state(path)[1]


def publish_local(
    plan: PublishPlan,
    credential: Credential | None = None,
    platform: str = "github",
    progress: ProgressCb | None = None,
    detail: Callable[[dict], None] | None = None,
    cancel: CancelToken | None = None,
) -> PublishResult:
    """把本地仓库发布到 plan.url 指向的远程仓库。

    传入 ``cancel`` 后可在推送阶段（含自动重试等待期）中止；中止时抛出
    ``CancelledError``，并在 detail 里说明远程仓库是否已经建好。
    """
    credential = credential or Credential()
    path = os.path.abspath(plan.path)
    url = (plan.url or "").strip()
    if not url:
        raise GitError("远程仓库地址为空，无法发布。")
    if not GitEngine.is_git_repo(path):
        raise GitError(f"目录不是 Git 仓库：{path}")

    branch = (plan.branch or "").strip()
    if not branch:
        branch = str(GitEngine.status(path).get("branch") or "")
    if not branch:
        raise GitError("当前仓库处于游离 HEAD 状态，无法确定要发布的分支。")

    remote_name = (plan.remote_name or "origin").strip() or "origin"

    # 1) 配置远程
    existing = {r["name"]: r["url"] for r in GitEngine.remotes(path)}
    if remote_name in existing:
        if existing[remote_name] != url:
            _report(progress, f"更新远程 {remote_name} 地址…")
            GitEngine.set_remote_url(path, remote_name, url)
        else:
            _report(progress, f"远程 {remote_name} 已指向目标地址")
    else:
        _report(progress, f"添加远程 {remote_name}…")
        GitEngine.add_remote(path, remote_name, url)

    result = PublishResult(branch=branch, remote_name=remote_name, remote_url=url)

    # 2) 确保有可推送的提交
    if GitEngine.commit_count(path) == 0:
        status = GitEngine.status(path, untracked=True)
        paths = list(status.get("unstaged") or []) + list(status.get("untracked") or [])
        if not paths and not (status.get("staged") or {}) and plan.create_readme:
            _report(progress, "仓库为空，自动创建 README.md…")
            _write_readme(path, plan.readme_title, plan.readme_description)
            result.created_readme = True
            status = GitEngine.status(path, untracked=True)
            paths = list(status.get("unstaged") or []) + list(status.get("untracked") or [])
        if paths:
            _report(progress, f"暂存 {len(paths)} 个文件…")
            GitEngine.stage(path, paths)
            result.staged_files = len(paths)
        if not GitEngine.status(path, untracked=True).get("staged"):
            raise GitError(
                "仓库中没有任何可提交的内容，无法创建首个提交。\n"
                "请先向仓库目录放入文件，或勾选「自动创建 README.md」。"
            )
        _report(progress, "创建首个提交…")
        result.commit_sha = GitEngine.commit(
            path, plan.commit_message or "Initial commit", plan.author_name, plan.author_email
        )
        result.created_commit = True

    # 3) 推送并建立跟踪
    if cancel is not None and cancel.cancelled:
        raise CancelledError("已停止发布。", f"远程仓库已就绪：{url}\n可在「远程」页用「推送本地提交」重试。")
    _report(progress, f"推送分支 {branch} 到 {remote_name}…")
    try:
        GitEngine.push(
            path,
            remote_name,
            branch,
            credential=credential,
            platform=platform,
            progress=progress,
            detail=detail,
            set_upstream=True,
            cancel=cancel,
        )
    except CancelledError as exc:
        raise CancelledError(
            "已停止发布。",
            f"远程仓库 {url} 已就绪，可在「远程」页用「推送本地提交」重试推送。",
        ) from exc
    return result
