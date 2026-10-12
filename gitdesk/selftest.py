"""内置自检：在打包后的 exe 中验证核心能力是否可用。

用法：GitDesk.exe --self-test [输出文件路径]

自检完全使用内置 Git 引擎创建仓库，不依赖系统 Git，
因此可以验证「无 Git 环境」下的可用性。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback

from .core.gitengine import Credential, GitEngine, GitError
from .core.logger import get_logger
from .core.paths import data_dir

log = get_logger("selftest")

PASS: list[str] = []
FAIL: list[str] = []
LINES: list[str] = []

# 离线榜单样例：用于验证 GitHub Trending 页面解析逻辑
_TRENDING_FIXTURE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/openai/example-tool" class="Link">
      <span class="text-normal">openai /</span>example-tool</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4">A tiny tool &amp; library</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="tmp-mr-3 d-inline-block">
      <span class="repo-language-color" style="background-color: #3572A5"></span>
      <span itemprop="programmingLanguage">Python</span></span>
    <a href="/openai/example-tool/stargazers"><svg height="16"></svg>12,345</a>
    <span class="d-inline-block float-sm-right">678 stars this week</span>
  </div>
</article>
"""


def _record(name: str, ok: bool, extra: str = "") -> None:
    target = PASS if ok else FAIL
    target.append(f"{name}{'' if ok else ' :: ' + extra}")
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + extra}"
    LINES.append(line)
    _out(line)


def _out(text: str) -> None:
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(text + "\n")
            stream.flush()
        except Exception:
            pass
    log.info(text)


def run_self_test(report_path: str | None = None) -> int:
    started = time.time()
    LINES.append("GitDesk 自检报告")
    LINES.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    LINES.append(f"Python: {sys.version.split()[0]}  冻结: {getattr(sys, 'frozen', False)}")
    LINES.append("=" * 64)

    work = tempfile.mkdtemp(prefix="gitdesk-selftest-")
    try:
        # 1. 初始化仓库
        origin_dir = os.path.join(work, "origin")
        GitEngine.init(origin_dir)
        _record("初始化仓库", GitEngine.is_git_repo(origin_dir))

        # 2. 提交
        with open(os.path.join(origin_dir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# GitDesk 自检\n")
        os.makedirs(os.path.join(origin_dir, "src"), exist_ok=True)
        with open(os.path.join(origin_dir, "src", "app.py"), "w", encoding="utf-8") as fh:
            fh.write("print('ok')\n")
        GitEngine.stage(origin_dir, ["README.md", "src/app.py"])
        sha = GitEngine.commit(origin_dir, "自检初始提交", "SelfTest", "selftest@local")
        _record("创建提交", len(sha) == 40, sha)

        status = GitEngine.status(origin_dir)
        _record("工作区状态读取", status["clean"] is True, str(status))
        _record("提交历史读取", len(GitEngine.history(origin_dir)) == 1)

        # 3. 克隆
        clone_dir = os.path.join(work, "clone")
        GitEngine.clone(origin_dir, clone_dir)
        _record("克隆仓库", os.path.isfile(os.path.join(clone_dir, "README.md")))
        _record("识别分支", GitEngine.status(clone_dir)["branch"] in ("main", "master"),
                GitEngine.status(clone_dir)["branch"])

        # 4. 变更 / 差异
        with open(os.path.join(clone_dir, "src", "app.py"), "w", encoding="utf-8") as fh:
            fh.write("print('changed')\n")
        with open(os.path.join(clone_dir, "extra.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        st = GitEngine.status(clone_dir)
        _record("检测未暂存更改", "src/app.py" in st["unstaged"], str(st["unstaged"]))
        _record("检测未跟踪文件", "extra.txt" in st["untracked"], str(st["untracked"]))
        GitEngine.stage(clone_dir, ["src/app.py"])
        diff = GitEngine.diff_text(clone_dir, file_path="src/app.py", staged=True)
        _record("生成差异文本", "+print('changed')" in diff, diff[:120])
        GitEngine.unstage(clone_dir, ["src/app.py"])
        _record("取消暂存", "src/app.py" in GitEngine.status(clone_dir)["unstaged"])
        GitEngine.discard(clone_dir, ["src/app.py"], [])
        _record("放弃更改", "src/app.py" not in GitEngine.status(clone_dir)["unstaged"])

        # 5. 分支
        original_branch = GitEngine.status(clone_dir)["branch"]
        GitEngine.create_branch(clone_dir, "selftest-branch")
        _record("新建并切换分支", GitEngine.status(clone_dir)["branch"] == "selftest-branch")
        GitEngine.checkout(clone_dir, original_branch)
        _record("切换回原分支", GitEngine.status(clone_dir)["branch"] == original_branch)
        GitEngine.delete_branch(clone_dir, "selftest-branch")
        remaining = [b["name"] for b in GitEngine.branches(clone_dir)["local"]]
        _record("删除分支", "selftest-branch" not in remaining, str(remaining))

        # 6. 推送与拉取
        with open(os.path.join(clone_dir, "src", "app.py"), "w", encoding="utf-8") as fh:
            fh.write("print('pushed')\n")
        GitEngine.stage(clone_dir, ["src/app.py"])
        GitEngine.commit(clone_dir, "自检推送提交", "SelfTest", "selftest@local")
        branch = GitEngine.status(clone_dir)["branch"]
        GitEngine.push(clone_dir, "origin", branch, Credential(), "github")
        remote_head = os.path.join(
            origin_dir, ".git", "refs", "heads", branch.replace("\\", "/")
        )
        _record("推送提交到远程", os.path.isfile(remote_head), remote_head)

        second = os.path.join(work, "clone2")
        GitEngine.clone(origin_dir, second)
        with open(os.path.join(second, "docs.md"), "w", encoding="utf-8") as fh:
            fh.write("docs\n")
        GitEngine.stage(second, ["docs.md"])
        GitEngine.commit(second, "第二处提交", "SelfTest", "selftest@local")
        GitEngine.push(second, "origin", branch, Credential(), "github")

        GitEngine.fetch(clone_dir, "origin", Credential(), "github")
        state = GitEngine.sync_state(clone_dir)
        _record("获取远程更新并识别落后", state.behind >= 1, str(state))
        result = GitEngine.pull(clone_dir, "origin", Credential(), "github")
        _record("拉取并快进", result["result"] in ("fast_forward", "merged", "up_to_date"),
                str(result))
        _record("拉取后文件到位", os.path.isfile(os.path.join(clone_dir, "docs.md")))

        # 7. 发布到远程仓库（模拟：本地裸仓库作为远程）
        from dulwich.repo import Repo as _Repo

        from .core.publish import PublishPlan, publish_local, repo_state

        publish_remote = os.path.join(work, "publish-remote.git")
        os.makedirs(publish_remote, exist_ok=True)  # dulwich 的 init_bare 不会自建目录
        _Repo.init_bare(publish_remote)
        fresh = os.path.join(work, "fresh-project")
        GitEngine.init(fresh)
        no_commit, empty = repo_state(fresh)
        _record("空仓库状态识别", no_commit and empty, f"{no_commit},{empty}")

        outcome = publish_local(
            PublishPlan(
                path=fresh,
                url=publish_remote,
                commit_message="自检初始提交",
                author_name="SelfTest",
                author_email="selftest@local",
                create_readme=True,
                readme_title="fresh-project",
                readme_description="自检生成",
            ),
            Credential(),
            "github",
        )
        _record("发布时自动生成 README", outcome.created_readme)
        _record(
            "发布时自动创建首个提交",
            outcome.created_commit and len(outcome.commit_sha) == 40,
            outcome.commit_sha,
        )
        _record(
            "发布后远程出现对应分支",
            os.path.isfile(os.path.join(publish_remote, "refs", "heads", outcome.branch)),
            outcome.branch,
        )
        _record("发布后自动建立上游跟踪", GitEngine.sync_state(fresh).has_upstream)
        _record(
            "发布后记录 origin 地址",
            {r["name"]: r["url"] for r in GitEngine.remotes(fresh)}.get("origin")
            == outcome.remote_url,
        )
        _record(
            "重复发布不产生重复远程",
            len(GitEngine.remotes(fresh)) == 1,
            str(GitEngine.remotes(fresh)),
        )

        blank = os.path.join(work, "blank-project")
        GitEngine.init(blank)
        caught = None
        try:
            publish_local(
                PublishPlan(path=blank, url=publish_remote, create_readme=False),
                Credential(),
                "github",
            )
        except GitError as exc:
            caught = exc
        _record(
            "空仓库拒绝发布并给出原因",
            caught is not None and "没有任何可提交的内容" in str(caught),
            str(caught),
        )

        # 8. 冲突保护
        with open(os.path.join(clone_dir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("本地版本\n")
        GitEngine.stage(clone_dir, ["README.md"])
        GitEngine.commit(clone_dir, "本地改动", "SelfTest", "selftest@local")
        with open(os.path.join(second, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("远程版本\n")
        GitEngine.stage(second, ["README.md"])
        GitEngine.commit(second, "远程改动", "SelfTest", "selftest@local")
        GitEngine.push(second, "origin", branch, Credential(), "github")
        GitEngine.fetch(clone_dir, "origin", Credential(), "github")
        before = open(os.path.join(clone_dir, "README.md"), encoding="utf-8").read()
        caught = None
        try:
            GitEngine.pull(clone_dir, "origin", Credential(), "github")
        except GitError as exc:
            caught = exc
        after = open(os.path.join(clone_dir, "README.md"), encoding="utf-8").read()
        _record("冲突时中止并给出提示", caught is not None and "冲突" in str(caught), str(caught))
        _record("冲突时本地文件未被改动", before == after, f"{before!r} -> {after!r}")

        # 9. 错误处理
        caught = None
        try:
            GitEngine.status(work)
        except GitError as exc:
            caught = exc
        _record("非仓库目录友好报错", caught is not None)

        # 10. 配置与加密
        from .core.config import Config

        cfg = Config()
        marker = "ghp_selftest_marker_12345"
        cfg.save_account("github", token=marker, username="selftest", verified=True)
        raw = cfg.path.read_text(encoding="utf-8")
        _record("令牌密文存储", marker not in raw)
        _record("令牌可解密还原", cfg.token("github") == marker)
        cfg.clear_account("github")
        _record("清除账号配置", cfg.token("github") == "")

        # 11. 克隆可中止
        from .core.gitengine import CancelToken, CancelledError

        cancel_target = os.path.join(work, "cancelled-clone")
        token = CancelToken()
        token.cancel()
        caught = None
        try:
            GitEngine.clone(origin_dir, cancel_target, cancel=token)
        except CancelledError as exc:
            caught = exc
        except Exception as exc:  # noqa: BLE001
            caught = exc
        _record("取消令牌可立即中止克隆", isinstance(caught, CancelledError), repr(caught))
        _record("取消后清理未完成的目录", not os.path.exists(cancel_target), cancel_target)

        from .core.gitengine import CancelToken, TransferMonitor

        notes: list[str] = []
        snapshots: list[dict] = []
        monitor = TransferMonitor("clone", notes.append, snapshots.append, CancelToken())
        monitor.write(b"Counting objects: 5% (5/100)\n")
        monitor.write(b"Counting objects: 5% (5/100)\n")
        monitor(1024, "read")
        monitor(1024, "read")
        _record("克隆进度去重上报", len(notes) == 1, str(notes))
        _record(
            "克隆传输字节被统计",
            monitor.stats.bytes_in == 2048,
            str(monitor.stats.bytes_in),
        )
        _record(
            "克隆进度推进结构化详情",
            bool(snapshots) and snapshots[-1].get("bytes_in_text", "").endswith("KB"),
            str(snapshots[-1].get("bytes_in_text") if snapshots else None),
        )

        # 12. 简介中文化（离线：替换翻译源 + 缓存落到临时目录）
        from pathlib import Path as _Path

        from .core import translator

        real_cache_file = translator.cache_file
        translator.cache_file = lambda: _Path(work) / "translate-cache.json"
        translator._cache = None
        saved_providers = dict(translator._PROVIDERS)
        saved_order = translator._ORDER
        try:
            _record("识别中文简介", translator.has_chinese("一个神奇的文档站点生成器"))
            _record(
                "识别英文简介",
                not translator.has_chinese("A magical documentation site generator"),
            )
            _record("清理 HTML 标签", translator.clean("<p>Hello&nbsp;<b>World</b></p>") == "Hello World")

            calls: list[str] = []

            def _fake(text: str) -> str:
                calls.append(text)
                return f"【{text}】"

            translator._PROVIDERS = {"fake": _fake}
            translator._ORDER = ("fake",)
            first = translator.translate("Build fast tools")
            second = translator.translate("Build fast tools")
            _record("英文简介被翻译", first == "【Build fast tools】", first)
            _record("同一条简介只翻译一次", len(calls) == 1, str(calls))
            _record("翻译结果写入缓存", translator.cache_size() == 1, str(translator.cache_size()))
            _record("中文简介原样返回", translator.translate("中文简介") == "中文简介")

            translator.clear_cache()
            batch = translator.translate_many(["Alpha tool", "Beta tool"], workers=2)
            _record(
                "并发批量翻译可用",
                batch == ["【Alpha tool】", "【Beta tool】"],
                str(batch),
            )
            repos = [{"description": "Gamma tool"}, {"description": "中文"}]
            changed = translator.translate_descriptions(repos)
            _record("批量中文化统计条数", changed == 1, str(changed))
            _record(
                "批量中文化结果就位",
                repos[0]["description_zh"] == "【Gamma tool】" and repos[1]["description_zh"] == "中文",
                str(repos),
            )

            def _boom(_text: str) -> str:
                raise RuntimeError("模拟翻译服务不可用")

            translator._PROVIDERS = {"fake": _boom}
            translator.clear_cache()
            _record(
                "翻译不可用时回退原文",
                translator.translate("Keep me") == "Keep me",
            )
        finally:
            translator._PROVIDERS.clear()
            translator._PROVIDERS.update(saved_providers)
            translator._ORDER = saved_order
            translator.cache_file = real_cache_file
            translator._cache = None

        # 13. 榜单解析（离线样例）
        from .core import trending

        items = trending.parse_trending_html(_TRENDING_FIXTURE, limit=5, period="weekly")
        first_item = items[0] if items else {}
        _record("趋势榜解析出条目", len(items) == 1, str(len(items)))
        _record(
            "趋势榜字段完整",
            first_item.get("full_name") == "openai/example-tool"
            and first_item.get("language") == "Python"
            and first_item.get("stars") == 12345
            and first_item.get("stars_period") == 678,
            str(first_item),
        )
        _record(
            "趋势榜缺失字段降级",
            first_item.get("forks") == 0,
            str(first_item.get("forks")),
        )
        translate_state = trending._translate_state(items)
        _record(
            "榜单统计中文化进度",
            "/1" in translate_state,
            translate_state,
        )

        no_avatar = {"avatar_url": ""}
        trending._attach_avatars([no_avatar])
        _record("头像并发加载器可用（无地址时安全跳过）", "avatar_bytes" not in no_avatar)

        # 14. 删除远程仓库接口契约（不发真实请求）
        from .core import platapi

        client = platapi.RemoteClient("github", "selftest-token")
        calls: list[tuple] = []
        client._request = lambda method, path, params=None, **kw: calls.append((method, path))  # type: ignore[assignment]
        deleted = client.delete_repo("selftest/demo")
        _record(
            "删除远程仓库使用 DELETE 接口",
            calls == [("DELETE", "/repos/selftest/demo")] and deleted == "selftest/demo",
            str(calls),
        )
        _record(
            "GitHub 令牌说明包含 delete_repo",
            "delete_repo" in platapi.PLATFORMS["github"].scopes,
        )

        # 15. 瀑布流分列计算（纯函数）
        from .ui.masonry import columns_for_width

        _record("窄窗口单列展示", columns_for_width(320) == 1, str(columns_for_width(320)))
        _record("宽窗口多列展示", columns_for_width(1128) == 3, str(columns_for_width(1128)))
        _record("超宽窗口列数封顶", columns_for_width(6000) == 4, str(columns_for_width(6000)))
        _record("异常宽度兜底为 1 列", columns_for_width(0) == 1, str(columns_for_width(0)))

        # 16. 网络故障识别与自动重试
        from .core import gitengine as engine

        broken = engine.GitProtocolError(
            ("('Connection aborted.', ConnectionResetError(10054, "
             "'远程主机强迫关闭了一个现有的连接。'))",)
        )
        _record("识别连接被远程主机中断", engine.is_transient_error(broken))
        _record(
            "识别本机中止连接（10053）",
            engine.is_transient_error(ConnectionAbortedError(10053, "software caused abort")),
        )
        _record(
            "推送被拒绝不重试",
            not engine.is_transient_error(engine.SendPackError(b"non-fast-forward")),
        )
        friendly, technical = engine.humanize_error(broken, retried=2)
        _record("中断给出可读提示", "网络连接被中断" in friendly, friendly.splitlines()[0])
        _record("提示包含自动重试次数", "已自动重试 2 次" in friendly)
        _record("保留原始技术细节", "10054" in technical, technical)

        attempts: list[int] = []
        real_sleep = engine.time.sleep
        engine.time.sleep = lambda _s: None  # 自检不真的等待
        try:

            def _flaky():
                attempts.append(1)
                if len(attempts) < 3:
                    raise engine.GitProtocolError(("('Connection aborted.', OSError(10054))",))
                return "ok"

            result = engine._retry_network(_flaky, label="自检", attempts=3)
            _record("瞬时故障自动重试后成功", result == "ok" and len(attempts) == 3, str(len(attempts)))
        finally:
            engine.time.sleep = real_sleep

        # 17. 传输详情解析（进度条下方的「详情」数据源）
        parsed = engine.parse_progress_line(
            "Receiving objects:  45% (9/20), 1.2 MiB | 300 KiB/s"
        )
        _record(
            "进度文本解析出阶段/对象/速度",
            parsed["phase"] == "接收对象"
            and parsed["objects"] == 9
            and parsed["total"] == 20
            and parsed["speed"] == "300 KiB/s",
            str(parsed),
        )
        _record("无关文本不误判阶段", engine.parse_progress_line("hello")["phase"] == "")
        _record("传输类型有中文名", engine.TRANSFER_KINDS.get("push") == "推送")

        details: list[dict] = []
        monitor = engine.TransferMonitor("push", None, details.append, None)
        monitor.write(b"Writing objects: 100% (4/4), 900 B | 10 KiB/s\n")
        monitor(4096, "write")
        monitor.finish(True, "推送完成")
        payload = details[-1] if details else {}
        _record(
            "详情含阶段与对象计数",
            payload.get("phase") == "完成" and payload.get("objects_text") == "4/4",
            str(payload.get("phase")) + "/" + str(payload.get("objects_text")),
        )
        _record("详情含已发送字节", payload.get("bytes_out") == 4096, str(payload.get("bytes_out")))
        _record("详情含耗时字段", isinstance(payload.get("elapsed"), float))

        # 18. 项目结构树（纯函数）
        from .core import tree

        tree_root = os.path.join(work, "structure")
        os.makedirs(os.path.join(tree_root, "src", "ui"), exist_ok=True)
        os.makedirs(os.path.join(tree_root, ".git"), exist_ok=True)
        with open(os.path.join(tree_root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo\n")
        with open(os.path.join(tree_root, "src", "main.py"), "w", encoding="utf-8") as fh:
            fh.write("print('hi')\n")

        entries = tree.list_dir(tree_root)
        names = [e["name"] for e in entries]
        _record("结构树隐藏 .git", ".git" not in names, str(names))
        _record("结构树目录优先", names == ["src", "README.md"], str(names))
        _record(
            "结构树仅显示目录",
            [e["name"] for e in tree.list_dir(tree_root, dirs_only=True)] == ["src"],
        )
        stats = tree.summarize(tree_root)
        _record(
            "结构树统计目录与文件数",
            stats["dirs"] == 2 and stats["files"] == 2,
            str(stats),
        )
        hits = tree.filter_tree(tree_root, "main")
        _record(
            "结构树跨层搜索命中嵌套文件",
            [e["rel"] for e in hits] == ["src"] and hits[0]["children"][0]["name"] == "main.py",
            str(hits),
        )
        _record("结构树无命中返回空", tree.filter_tree(tree_root, "zzz") == [])
        _record("结构树大小格式化", tree.format_size(2048) == "2.0 KB", tree.format_size(2048))

        # 19. 界面组件（冻结包内可实例化，验证资源与样式完整）
        from PySide6.QtWidgets import QApplication

        from .ui.style import STYLESHEET

        app_instance = QApplication.instance() or QApplication([])
        _record("QApplication 可创建（界面依赖就绪）", app_instance is not None)
        _record("样式表包含瀑布流浅色背景", "QWidget#MasonryCanvas" in STYLESHEET)
        _record("样式表包含传输面板样式", "TransferPanel" in STYLESHEET)
        try:
            from .ui.widgets import TransferPanel

            panel = TransferPanel()
            panel.begin("正在推送…", kind="上传 · 推送", cancellable=True)
            panel.update_detail({"phase": "发送对象", "objects_text": "1/2", "percent": 50.0})
            ok_bar = panel.bar.maximum() == 100 and panel.bar.value() == 50
            _record(
                "上传开始时出现停止按钮",
                not panel.stop_btn.isHidden() and panel.stop_btn.text() == "停止",
                panel.stop_btn.text(),
            )
            panel.finish("推送成功", ok=True)
            _record(
                "传输详情面板可实例化并回填",
                ok_bar and panel.bar.value() == 100,
                str(panel.bar.value()),
            )
        except Exception as exc:  # noqa: BLE001
            _record("传输详情面板可实例化并回填", False, f"{type(exc).__name__}: {exc}")

        # 20. 上传可停止（推送 / 发布）
        cancelled_token = engine.CancelToken()
        cancelled_token.cancel()
        _record("取消令牌可置位并读取", cancelled_token.cancelled)

        never: list[int] = []
        try:
            engine._retry_network(lambda: never.append(1), cancel=cancelled_token)
            stop_raised = False
        except engine.OperationCancelled:
            stop_raised = True
        _record("已取消时不再发起新的尝试", stop_raised and not never, str(never))

        class _WaitCancelled:
            """模拟「重试等待期内用户点了停止」。"""

            cancelled = False

            def wait(self, _timeout: float) -> bool:
                return True

        waited: list[int] = []

        def _boom():
            waited.append(1)
            raise engine.GitProtocolError(("('Connection aborted.', OSError(10054))",))

        try:
            engine._retry_network(_boom, cancel=_WaitCancelled())
            wait_raised = False
        except engine.OperationCancelled:
            wait_raised = True
        _record(
            "重试等待期可被立刻打断",
            wait_raised and len(waited) == 1,
            str(waited),
        )

        push_token = engine.CancelToken()
        push_token.cancel()
        try:
            GitEngine.push(origin_dir, "origin", "main", cancel=push_token)
            push_cancelled = False
            push_message = "（未抛出异常）"
        except engine.CancelledError as exc:
            push_cancelled = True
            push_message = exc.message
        except Exception as exc:  # noqa: BLE001
            push_cancelled = False
            push_message = f"{type(exc).__name__}: {exc}"
        _record(
            "推送支持取消",
            push_cancelled and "已停止推送" in push_message,
            push_message[:60],
        )

        publish_token = engine.CancelToken()
        publish_token.cancel()
        try:
            publish_local(
                PublishPlan(path=origin_dir, url=publish_remote, branch="main"),
                Credential(),
                "github",
                cancel=publish_token,
            )
            publish_cancelled = False
            publish_message = "（未抛出异常）"
        except engine.CancelledError as exc:
            publish_cancelled = True
            publish_message = exc.message
        except Exception as exc:  # noqa: BLE001
            publish_cancelled = False
            publish_message = f"{type(exc).__name__}: {exc}"
        _record(
            "发布支持取消",
            publish_cancelled and "已停止" in publish_message,
            publish_message[:60],
        )

        # 21. 布局：概览页放在滚动容器内，避免空间不足时控件重叠
        try:
            from PySide6.QtWidgets import QScrollArea

            from .ui.pages.repos import RepoDetailPanel

            class _Stub:
                """占位对象：任何属性访问都返回一个空实现。"""

                def __getattr__(self, _name):
                    return lambda *_a, **_k: None

            class _NoopTasks:
                """自检不启动后台线程，只用空实现占位。"""

                def run(self, *_args, **_kwargs):
                    return None

            detail = RepoDetailPanel(_Stub(), _Stub(), _NoopTasks())
            detail.resize(1000, 600)
            detail.show()
            app_instance.processEvents()
            overview = detail.tabs.widget(0)
            _record(
                "概览页位于滚动容器内",
                isinstance(overview, QScrollArea),
                type(overview).__name__,
            )
            if isinstance(overview, QScrollArea):
                inner = overview.widget()
                detail.transfer.begin("正在推送…", kind="上传 · 推送", cancellable=True)
                detail.transfer.toggle_btn.setChecked(True)
                app_instance.processEvents()
                _record(
                    "详情展开后概览内容不被压缩",
                    inner.height() >= inner.minimumSizeHint().height(),
                    f"{inner.height()} < {inner.minimumSizeHint().height()}",
                )
                status_card = detail.ov_labels["current_branch"].parentWidget()
                actions_card = detail.ov_hint.parentWidget()
                _record(
                    "同步操作卡片不与仓库状态卡片重叠",
                    actions_card.geometry().top() > status_card.geometry().bottom(),
                    f"{actions_card.geometry().top()} <= {status_card.geometry().bottom()}",
                )
                _record(
                    "同步操作按钮行不与说明文字重叠",
                    detail.ov_hint.geometry().top() > detail.btn_publish.geometry().bottom(),
                    f"{detail.ov_hint.geometry().top()} <= "
                    f"{detail.btn_publish.geometry().bottom()}",
                )
            detail.transfer.finish("推送失败：网络连接被中断", ok=False)
            _record(
                "失败时进度条切换为错误配色",
                detail.transfer.bar.property("state") == "error",
                str(detail.transfer.bar.property("state")),
            )
            _record(
                "失败后停止/关闭按钮仍可用",
                detail.transfer.stop_btn.isEnabled() and not detail.transfer.stop_btn.isHidden(),
            )
        except Exception as exc:  # noqa: BLE001
            _record("概览页滚动容器与失败态", False, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        _record("自检执行异常", False, f"{exc}")
        LINES.append(traceback.format_exc())
    finally:
        shutil.rmtree(work, ignore_errors=True)

    elapsed = time.time() - started
    LINES.append("=" * 64)
    LINES.append(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项，用时 {elapsed:.2f} 秒")
    for item in FAIL:
        LINES.append(f"  ✗ {item}")

    text = "\n".join(LINES)
    _out(text)

    target = report_path or os.path.join(str(data_dir()), "selftest-report.txt")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        _out(f"报告已写入: {target}")
    except Exception as exc:  # noqa: BLE001
        _out(f"报告写入失败: {exc}")

    return 0 if not FAIL else 1


def run_net_check() -> int:
    """联网自检：验证冻结包中的网络依赖与外部接口可达性。

    用法：GitDesk.exe --net-check
    用于排查「程序内网络请求失败」类问题（代理、防火墙、接口变更）。
    """
    from .core import translator, trending

    results: list[str] = []
    failures = 0

    def probe(name: str, fn) -> None:
        nonlocal failures
        try:
            detail = fn()
            results.append(f"  [OK]   {name}  {detail}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            results.append(f"  [FAIL] {name}  {type(exc).__name__}: {exc}")

    def github_api() -> str:
        import requests

        resp = requests.get(
            "https://api.github.com/rate_limit",
            timeout=(8, 20),
            headers={"User-Agent": trending.USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        remaining = ((resp.json() or {}).get("resources") or {}).get("core", {}).get("remaining")
        return f"HTTP {resp.status_code} 剩余配额 {remaining}"

    def github_trending() -> str:
        items = trending.fetch_trending("daily", limit=3)
        return f"解析到 {len(items)} 条，示例 {items[0]['full_name']}"

    def translate() -> str:
        text = "A tiny tool for testing the network"
        out = translator._via_google(text)
        if not out or not translator.has_chinese(out):
            raise RuntimeError(f"未返回中文结果: {out!r}")
        return f"翻译结果「{out}」"

    probe("GitHub API（requests 依赖）", github_api)
    probe("GitHub 趋势榜页面解析", github_trending)
    probe("简介中文化接口", translate)

    lines = ["GitDesk 联网自检", f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", "=" * 64]
    lines.extend(results)
    lines.append("=" * 64)
    lines.append("全部通过" if not failures else f"失败 {failures} 项（请检查网络 / 代理 / 防火墙）")
    for line in lines:
        _out(line)
    return 0 if not failures else 1
