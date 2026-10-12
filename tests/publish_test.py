"""「发布到远程仓库」功能测试（offscreen 模式）。

覆盖两层：
1. core.publish.publish_local —— 用本地裸仓库充当远程，验证配置远程 / 补齐首个提交 /
   自动 README / 推送与跟踪的完整链路
2. UI —— 发布对话框的平台门禁、名称校验、空仓库分支，以及本地仓库页的发布入口
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORK = tempfile.mkdtemp(prefix="gitdesk-publish-")
os.environ["GITDESK_DATA_DIR"] = os.path.join(WORK, "appdata")

from gitdesk.core.gitengine import Credential, GitEngine, GitError  # noqa: E402
from gitdesk.core.publish import (  # noqa: E402
    PublishPlan,
    is_repo_empty,
    publish_local,
    repo_state,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {'' if cond else extra}")


def make_bare(name: str) -> str:
    """用 dulwich 自建裸仓库充当远程，避免依赖系统 Git。"""
    from dulwich.repo import Repo

    path = os.path.join(WORK, name + ".git")
    os.makedirs(path, exist_ok=True)
    Repo.init_bare(path)
    return path


def remote_has_branch(bare: str, branch: str = "main") -> bool:
    return os.path.isfile(os.path.join(bare, "refs", "heads", branch))


def write(path: str, rel: str, text: str) -> None:
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# 1. 核心发布链路
# --------------------------------------------------------------------------- #


def test_core() -> None:
    print("\n== 核心发布链路 ==")

    # 1.1 全新空目录 + 自动 README
    empty_repo = os.path.join(WORK, "empty-repo")
    GitEngine.init(empty_repo)
    no_commit, empty = repo_state(empty_repo)
    check("空目录识别为无提交且为空", no_commit and empty, f"{no_commit},{empty}")

    bare1 = make_bare("remote1")
    plan = PublishPlan(
        path=empty_repo,
        url=bare1,
        commit_message="初始提交",
        author_name="Tester",
        author_email="tester@example.com",
        create_readme=True,
        readme_title="empty-repo",
        readme_description="发布测试",
    )
    outcome = publish_local(plan, Credential(), "github")
    check("自动创建 README.md", outcome.created_readme)
    check("自动创建首个提交", outcome.created_commit, outcome.commit_sha)
    check("README 内容写入正确", "发布测试" in open(
        os.path.join(empty_repo, "README.md"), encoding="utf-8").read())
    check("推送到远程成功", remote_has_branch(bare1))
    check("远程名称与地址回填", outcome.remote_name == "origin" and outcome.remote_url == bare1)
    state = GitEngine.sync_state(empty_repo)
    check("已建立上游跟踪", state.has_upstream, str(state))
    check("推送后本地与远程一致", state.ahead == 0 and state.behind == 0, str(state))
    remotes = {r["name"]: r["url"] for r in GitEngine.remotes(empty_repo)}
    check("origin 地址已写入配置", remotes.get("origin") == bare1, str(remotes))

    # 1.2 有文件但无提交，且不勾选 README
    dirty_repo = os.path.join(WORK, "dirty-repo")
    GitEngine.init(dirty_repo)
    write(dirty_repo, "src/app.py", "print('hi')\n")
    no_commit, empty = repo_state(dirty_repo)
    check("有文件无提交识别为无提交但非空", no_commit and not empty, f"{no_commit},{empty}")
    bare2 = make_bare("remote2")
    outcome = publish_local(
        PublishPlan(path=dirty_repo, url=bare2, commit_message="首提",
                    author_name="T", author_email="t@e", create_readme=True),
        Credential(), "github",
    )
    check("非空目录不生成 README", not outcome.created_readme)
    check("现有文件被纳入首个提交", outcome.staged_files == 1 and outcome.created_commit,
          f"{outcome.staged_files},{outcome.created_commit}")
    check("推送成功", remote_has_branch(bare2))

    # 1.3 完全为空且未勾选 README -> 明确报错
    blank_repo = os.path.join(WORK, "blank-repo")
    GitEngine.init(blank_repo)
    caught = None
    try:
        publish_local(PublishPlan(path=blank_repo, url=make_bare("remote3"),
                                  create_readme=False), Credential(), "github")
    except GitError as exc:
        caught = exc
    check("空仓库拒绝发布并给出原因",
          caught is not None and "没有任何可提交的内容" in str(caught), str(caught))

    # 1.4 已有提交的仓库：不额外造提交
    committed = os.path.join(WORK, "committed-repo")
    GitEngine.init(committed)
    write(committed, "a.txt", "1\n")
    GitEngine.stage(committed, ["a.txt"])
    GitEngine.commit(committed, "已有提交", "T", "t@e")
    bare4 = make_bare("remote4")
    outcome = publish_local(
        PublishPlan(path=committed, url=bare4, commit_message="不应被使用",
                    author_name="T", author_email="t@e"),
        Credential(), "github",
    )
    check("已有提交时不新建提交", not outcome.created_commit and not outcome.created_readme)
    check("已有提交正常推送", remote_has_branch(bare4))
    check("提交数保持为 1", GitEngine.commit_count(committed) == 1,
          str(GitEngine.commit_count(committed)))

    # 1.5 再次发布：更新远程地址而非新增
    bare5 = make_bare("remote5")
    outcome = publish_local(PublishPlan(path=committed, url=bare5, author_name="T",
                                        author_email="t@e"), Credential(), "github")
    remotes = {r["name"]: r["url"] for r in GitEngine.remotes(committed)}
    check("重复发布复用 origin 并更新地址",
          len(remotes) == 1 and remotes.get("origin") == bare5, str(remotes))
    check("更新地址后推送成功", remote_has_branch(bare5) and outcome.remote_name == "origin")

    # 1.6 历史冲突（远程已有无关历史）-> 推送被拒且给出提示
    conflict_repo = os.path.join(WORK, "conflict-repo")
    GitEngine.init(conflict_repo)
    write(conflict_repo, "other.txt", "x\n")
    GitEngine.stage(conflict_repo, ["other.txt"])
    GitEngine.commit(conflict_repo, "无关历史", "T", "t@e")
    caught = None
    try:
        publish_local(PublishPlan(path=conflict_repo, url=bare1, author_name="T",
                                  author_email="t@e"), Credential(), "github")
    except GitError as exc:
        caught = exc
    check("远程历史冲突时拒绝并提示", caught is not None and "推送失败" in str(caught), str(caught))

    # 1.7 非法输入
    caught = None
    try:
        publish_local(PublishPlan(path=committed, url=""), Credential(), "github")
    except GitError as exc:
        caught = exc
    check("空地址被拒绝", caught is not None and "地址为空" in str(caught), str(caught))

    caught = None
    try:
        publish_local(PublishPlan(path=os.path.join(WORK, "not-a-repo"),
                                  url=bare5), Credential(), "github")
    except GitError as exc:
        caught = exc
    check("非仓库目录被拒绝", caught is not None and "不是 Git 仓库" in str(caught), str(caught))

    check("is_repo_empty 与 repo_state 一致",
          is_repo_empty(blank_repo) is True and is_repo_empty(committed) is False)


# --------------------------------------------------------------------------- #
# 2. 平台建仓接口（离线校验请求构造与错误映射）
# --------------------------------------------------------------------------- #


def test_api() -> None:
    print("\n== 平台建仓接口 ==")
    from gitdesk.core import platapi

    calls: list[tuple] = []

    for platform, token in (("github", "ghp_x"), ("gitee", "gt_x")):
        client = platapi.RemoteClient(platform, token, username="tester")

        def fake_request(method, path, params=None, **kwargs):
            calls.append((platform, method, path, params, kwargs))
            return {"name": "demo", "full_name": "tester/demo", "clone_url":
                    f"https://{client.meta.web_host.split('//')[-1]}/tester/demo.git",
                    "html_url": f"{client.meta.web_host}/tester/demo",
                    "private": True, "default_branch": "main"}

        client._request = fake_request  # type: ignore[method-assign]
        repo = client.create_repo("demo", "说明", private=True)
        method, path, params, kwargs = calls[-1][1], calls[-1][2], calls[-1][3], calls[-1][4]
        payload = kwargs.get("json") or {}
        check(f"{platform} 使用 POST /user/repos", method == "POST" and path == "/user/repos",
              f"{method} {path}")
        check(f"{platform} 提交名称/描述/可见性",
              payload.get("name") == "demo" and payload.get("description") == "说明"
              and payload.get("private") is True, str(payload))
        check(f"{platform} 默认关闭远程初始提交", payload.get("auto_init") is False, str(payload))
        check(f"{platform} Gitee 令牌走参数而非请求头",
              (params is None or "access_token" not in (params or {})) if platform == "github"
              else True)
        check(f"{platform} 归一化返回可用地址",
              repo["clone_url"].endswith("tester/demo.git"), str(repo))

    # Gitee 表单回退分支
    client = platapi.RemoteClient("gitee", "gt_x", username="tester")
    seen: list[str] = []

    def flaky(method, path, params=None, **kwargs):
        if "json" in kwargs:
            seen.append("json")
            raise platapi.ApiError("拒绝", status=400, detail='{"message":"bad body"}')
        seen.append("form")
        return {"name": "demo", "full_name": "tester/demo", "default_branch": "main"}

    client._request = flaky  # type: ignore[method-assign]
    repo = client.create_repo("demo", "", private=False)
    check("Gitee 拒绝 JSON 时回退表单", seen == ["json", "form"], str(seen))
    check("表单回退同样返回仓库", repo.get("full_name") == "tester/demo", str(repo))

    # 422 重名 -> 友好提示
    client = platapi.RemoteClient("github", "ghp_x", username="tester")

    def conflict(method, path, params=None, **kwargs):
        raise platapi.ApiError(
            "请求参数不被接受，请检查输入内容。", status=422,
            detail='{"message":"Repository creation failed.","errors":[{"message":'
                   '"name already exists on this account"}]}',
        )

    client._request = conflict  # type: ignore[method-assign]
    caught = None
    try:
        client.create_repo("demo")
    except platapi.ApiError as exc:
        caught = exc
    check("重名错误给出可读提示",
          caught is not None and "已被占用" in str(caught)
          and "name already exists" in str(caught), str(caught))

    # 403 -> 权限引导
    def forbidden(method, path, params=None, **kwargs):
        raise platapi.ApiError("访问被拒绝。", status=403, detail="forbidden")

    client._request = forbidden  # type: ignore[method-assign]
    caught = None
    try:
        client.create_repo("demo")
    except platapi.ApiError as exc:
        caught = exc
    check("权限不足给出令牌范围提示",
          caught is not None and "权限不足" in str(caught), str(caught))


# --------------------------------------------------------------------------- #
# 3. 界面层
# --------------------------------------------------------------------------- #


def test_ui() -> None:
    print("\n== 界面层 ==")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication(sys.argv)

    from gitdesk.core.config import Config
    from gitdesk.ui import publish_dialog as pd_mod
    from gitdesk.ui.publish_dialog import PublishDialog

    appdata = os.environ["GITDESK_DATA_DIR"]
    os.makedirs(appdata, exist_ok=True)
    cfg = Config()
    cfg.save_account("github", token="ghp_test_token", username="tester", verified=True)
    cfg.save_account("gitee", token="", username="", verified=False)
    cfg.clear_account("gitee")
    check("预置：GitHub 已授权、Gitee 未授权",
          cfg.is_connected("github") and not cfg.is_connected("gitee"))

    empty_repo = os.path.join(WORK, "dlg-empty")
    GitEngine.init(empty_repo)
    full_repo = os.path.join(WORK, "dlg-full")
    GitEngine.init(full_repo)
    write(full_repo, "x.txt", "1\n")
    GitEngine.stage(full_repo, ["x.txt"])
    GitEngine.commit(full_repo, "c", "T", "t@e")

    dlg = PublishDialog(cfg, empty_repo)
    check("授权平台被识别", dlg.ready_platforms == ["github"], str(dlg.ready_platforms))
    check("默认选中已授权平台", dlg.current_platform() == "github", dlg.current_platform())
    check("空仓库显示首提交选项",
          dlg.empty_box.isVisibleTo(dlg) and dlg.readme_check.isChecked())
    check("默认仓库名取自目录", dlg.name_edit.text() == os.path.basename(empty_repo),
          dlg.name_edit.text())
    check("预览包含账号名与平台域名",
          "tester" in dlg.preview.text() and "github.com" in dlg.preview.text(),
          dlg.preview.text())
    captured = {}
    pd_mod.show_error = lambda *a, **k: captured.setdefault("err", a[2] if len(a) > 2 else "")
    dlg.name_edit.setText("bad name!!")
    dlg._confirm()
    check("非法仓库名被拦截", dlg.result() != QDialog.Accepted and bool(captured.get("err")),
          str(captured))
    dlg.name_edit.setText("ok-name_1")
    dlg.desc_edit.setText("描述")
    dlg.visibility_combo.setCurrentIndex(1)
    dlg._confirm()
    check("合法输入可提交", dlg.result() == QDialog.Accepted, str(dlg.result_payload))
    check("提交内容包含平台与可见性",
          dlg.result_payload["platform"] == "github" and dlg.result_payload["private"] is True
          and dlg.result_payload["create_readme"] is True, str(dlg.result_payload))

    dlg2 = PublishDialog(cfg, full_repo)
    check("有提交的仓库隐藏首提交选项", not dlg2.empty_box.isVisibleTo(dlg2))
    check("有提交的仓库不勾选自动 README", dlg2.result_payload.get("create_readme") in (None, False))

    # 未授权平台：切换到 Gitee 时禁用创建
    index_gitee = [i for i in range(dlg2.platform_combo.count())
                   if dlg2.platform_combo.itemData(i) == "gitee"][0]
    dlg2.platform_combo.setCurrentIndex(index_gitee)
    check("未配置令牌的平台无法创建",
          not dlg2.create_btn.isEnabled() and dlg2.go_accounts_btn.isVisibleTo(dlg2),
          f"enabled={dlg2.create_btn.isEnabled()}")
    dlg2._go_accounts()
    check("前往账号设置标记正确", dlg2.want_accounts is True)

    # 主窗口入口
    from gitdesk.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    window.goto(1)
    app.processEvents()
    window.repos_page._register_path(empty_repo)
    for _ in range(200):
        app.processEvents()
        if not window.tasks.busy:
            break
        QTimer.singleShot(20, lambda: None)
    app.processEvents()
    detail = window.repos_page.detail
    check("概览提供发布入口",
          hasattr(detail, "btn_publish") and detail.btn_publish.isVisibleTo(detail)
          and detail.btn_publish.isEnabled(),
          f"exists={hasattr(detail, 'btn_publish')} "
          f"visible={detail.btn_publish.isVisibleTo(detail)} "
          f"enabled={detail.btn_publish.isEnabled()}")
    check("远程页提供发布入口", hasattr(detail, "btn_remote_publish"))
    detail._check_notice()
    check("无远程时提示含发布入口引导",
          detail.notice.isVisible() and "发布到远程仓库" in detail.notice.text(),
          detail.notice.text())
    check("详情面板暴露账号页跳转信号", hasattr(detail, "request_accounts"))
    window.close()


def main() -> int:
    test_core()
    test_api()
    test_ui()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print(f"  ✗ {item}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
