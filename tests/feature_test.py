"""新增功能回归测试。

覆盖三项本次迭代交付的能力：

1. 克隆可中止（停止克隆按钮 + 取消令牌 + 残留目录清理）；
2. 远程仓库页交互（左键仅选中、右键查看详情、删除远程仓库）；
3. GitHub 热门榜瀑布流（榜单解析 + 简介中文化 + 最短列排布）；
4. 分支页签的提交演化图（泳道布局算法 + 引擎数据源 + 自绘控件）。

除个别联网用例用环境变量开关外，全部离线运行。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORK = tempfile.mkdtemp(prefix="gitdesk-feature-")
os.environ.setdefault("GITDESK_DATA_DIR", os.path.join(WORK, "appdata"))

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QTableWidgetItem  # noqa: E402

from gitdesk.core import platapi, translator, trending  # noqa: E402
from gitdesk.core.gitengine import CancelToken, CancelledError, GitEngine, GitError  # noqa: E402
from gitdesk.ui.main_window import MainWindow  # noqa: E402
from gitdesk.ui.style import STYLESHEET  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {'' if cond else extra}")


def git(*args, cwd=None, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def make_bare_remote(name: str) -> str:
    path = os.path.join(WORK, name + ".git")
    git("init", "--bare", "--initial-branch=main", path)
    return path


def make_seeded_remote(name: str) -> str:
    """带一个初始提交的裸仓库，可被正常克隆出分支。"""
    bare = make_bare_remote(name)
    seed = os.path.join(WORK, name + "-seed")
    git("clone", bare, seed)
    git("config", "user.name", "Tester", cwd=seed)
    git("config", "user.email", "tester@example.com", cwd=seed)
    with open(os.path.join(seed, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# seed\n")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "init", cwd=seed)
    git("push", "origin", "main", cwd=seed)
    return bare


# --------------------------------------------------------------------------- #
# 1. 克隆可中止
# --------------------------------------------------------------------------- #


def test_clone_cancel() -> None:
    print("\n== 克隆中止（引擎层） ==")
    remote = make_bare_remote("cancel-remote")

    token = CancelToken()
    token.cancel()
    target = os.path.join(WORK, "cancel-early", "repo")
    caught = None
    try:
        GitEngine.clone(remote, target, cancel=token)
    except CancelledError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001
        caught = exc
    check("已取消的令牌会立即中止克隆", isinstance(caught, CancelledError), repr(caught))
    check("取消后不残留目标目录", not os.path.exists(target), target)
    check("取消异常带 cancelled 标记", bool(getattr(caught, "cancelled", False)))
    check("取消异常是 GitError 的子类（界面可统一处理）", isinstance(caught, GitError))

    # 未取消时正常完成，且进度回调可用
    seeded = make_seeded_remote("cancel-seeded")
    messages: list[str] = []
    ok_target = os.path.join(WORK, "cancel-late", "repo")
    path = GitEngine.clone(seeded, ok_target, progress=messages.append, cancel=CancelToken())
    check("正常克隆不受取消机制影响", os.path.isdir(os.path.join(path, ".git")), path)
    check("克隆后可正常读取状态", GitEngine.status(path)["branch"] == "main",
          GitEngine.status(path)["branch"])

    # 传输监视器：进度去重、字节累计、结构化详情
    from gitdesk.core.gitengine import TransferMonitor

    seen: list[str] = []
    details: list[dict] = []
    monitor = TransferMonitor("clone", seen.append, details.append, CancelToken())
    monitor.write(b"Counting objects: 1% (1/100)\n")
    monitor.write(b"Counting objects: 1% (1/100)\n")  # 重复内容应被吞掉
    check("进度监视器去重", len(seen) == 1, str(seen))
    for _ in range(5):
        monitor(70000, "read")
    check("传输层字节计数被累计", monitor.stats.bytes_in == 350000, str(monitor.stats.bytes_in))

    time.sleep(0.15)
    monitor.write(b"Receiving objects:  45% (9/20), 1.2 MiB | 300 KiB/s\n")
    payload = details[-1]
    check("阶段被解析为接收对象", payload["phase"] == "接收对象", str(payload["phase"]))
    check("对象计数被解析", payload["objects_text"] == "9/20", payload["objects_text"])
    check("传输速度被解析", payload["speed"] == "300 KiB/s", payload["speed"])
    check("百分比可驱动进度条", payload["percent"] == 45.0, str(payload["percent"]))
    check("详情含已接收字节文本",
          payload["bytes_in_text"].endswith(("KB", "MB")), payload["bytes_in_text"])
    check("详情含耗时字段", isinstance(payload.get("elapsed"), float), str(payload.get("elapsed")))

    monitor.finish(True, "克隆完成")
    check("收尾会更新最终阶段", monitor.stats.phase == "完成" and monitor.stats.ok is True,
          str(monitor.stats.phase))

    bad = TransferMonitor("fetch", seen.append, None, None)
    before = len(seen)
    bad.write("不是字节流也能处理")
    check("进度监视器兼容文本输入", len(seen) == before + 1, str(seen))


# --------------------------------------------------------------------------- #
# 2. 翻译服务
# --------------------------------------------------------------------------- #


def test_translator() -> None:
    print("\n== 简介中文化 ==")
    check("识别中文文本", translator.has_chinese("一个神奇的文档站点生成器"))
    check("识别英文文本", not translator.has_chinese("A magical documentation site generator"))
    check("清理 HTML 标签", translator.clean("<p>Hello&nbsp;<b>World</b></p>") == "Hello World")

    calls: list[str] = []

    def fake_google(text: str) -> str:
        calls.append(text)
        return f"【{text}】"

    original = dict(translator._PROVIDERS)
    translator._PROVIDERS["fake"] = fake_google
    translator._ORDER = ("fake",)
    translator.clear_cache()
    try:
        first = translator.translate("Build fast things")
        check("翻译结果被采用", first == "【Build fast things】", first)
        check("翻译结果写入缓存", translator.cache_size() == 1, str(translator.cache_size()))
        second = translator.translate("Build fast things")
        check("第二次命中缓存（不再请求）", second == first and len(calls) == 1, str(calls))
        check("中文文本原样返回", translator.translate("中文简介") == "中文简介")

        def boom(_text: str) -> str:
            raise RuntimeError("网络不可用")

        translator._PROVIDERS["fake"] = boom
        translator.clear_cache()
        fallback = translator.translate("Keep me as is")
        check("翻译源全部失败时回退原文", fallback == "Keep me as is", fallback)

        items = [
            {"description": "Alpha tool"},
            {"description": "中文项目"},
            {"description": ""},
        ]
        translator._PROVIDERS["fake"] = fake_google
        translator.clear_cache()
        changed = translator.translate_descriptions(items)
        check("批量中文化并统计实际翻译条数", changed == 1, str(changed))
        check("英文简介已翻译", items[0]["description_zh"] == "【Alpha tool】",
              items[0]["description_zh"])
        check("中文简介保持原样", items[1]["description_zh"] == "中文项目", items[1]["description_zh"])
        check("空简介不报错", items[2]["description_zh"] == "", repr(items[2]["description_zh"]))
        check("清理缓存返回条数", translator.clear_cache() >= 1)
    finally:
        translator._PROVIDERS.clear()
        translator._PROVIDERS.update(original)
        translator._ORDER = ("google", "mymemory", "google_single")


# --------------------------------------------------------------------------- #
# 3. 榜单抓取
# --------------------------------------------------------------------------- #

TRENDING_FIXTURE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/openai/example-tool" data-view-component="true" class="Link">
      <span class="text-normal">openai /</span>example-tool</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4">
    A tiny tool &amp; library for agents
  </p>
  <div class="f6 color-fg-muted mt-2">
    <span class="tmp-mr-3 d-inline-block"><span class="repo-language-color"
      style="background-color: #3572A5"></span>
      <span itemprop="programmingLanguage">Python</span></span>
    <a href="/openai/example-tool/stargazers" class="Link Link--muted">
      <svg height="16" viewBox="0 0 16 16"></svg>
      12,345</a>
    <a href="/openai/example-tool/forks" class="Link Link--muted">
      <svg height="16" viewBox="0 0 16 16"></svg>
      678</a>
    <span class="d-inline-block float-sm-right">1,234 stars today</span>
  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/solo/nolanguage">solo/nolanguage</a></h2>
  <div class="f6 color-fg-muted mt-2">
    <a href="/solo/nolanguage/stargazers"><svg height="16"></svg>9</a>
  </div>
</article>
<article class="Box-row"><h2 class="h3">没有链接的条目</h2></article>
"""


def test_trending_parse() -> None:
    print("\n== 榜单解析 ==")
    items = trending.parse_trending_html(TRENDING_FIXTURE, limit=10, period="daily")
    check("跳过无仓库链接的条目", len(items) == 2, str(len(items)))
    first = items[0]
    check("解析仓库全名", first["full_name"] == "openai/example-tool", first["full_name"])
    check("解析拆分的 owner/name", (first["owner"], first["name"]) == ("openai", "example-tool"))
    check("解析简介并反转义", first["description"] == "A tiny tool & library for agents",
          first["description"])
    check("解析语言", first["language"] == "Python", first["language"])
    check("解析语言配色", first["language_color"] == "#3572A5", first["language_color"])
    check("星标数不含 svg 尺寸干扰", first["stars"] == 12345, str(first["stars"]))
    check("复刻数解析正确", first["forks"] == 678, str(first["forks"]))
    check("解析当期新增星标", first["stars_period"] == 1234, str(first["stars_period"]))
    check("排名连续且从 1 开始", [i["rank"] for i in items] == [1, 2])
    check("生成克隆地址", first["clone_url"].endswith("openai/example-tool.git"), first["clone_url"])
    check("生成头像地址", first["avatar_url"].startswith("https://github.com/openai.png"),
          first["avatar_url"])
    check("缺失字段降级为 0/空", items[1]["language"] == "" and items[1]["forks"] == 0)
    check("数量上限生效", len(trending.parse_trending_html(TRENDING_FIXTURE, limit=1)) == 1)

    raw = {
        "full_name": "org/repo",
        "name": "repo",
        "description": "Hello",
        "stargazers_count": 100,
        "forks_count": 5,
        "language": "Go",
        "html_url": "https://github.com/org/repo",
        "topics": ["a", "b", "c", "d", "e"],
        "owner": {"login": "org", "avatar_url": "https://example/avatar.png"},
    }
    star_item = trending._normalize_search_item(raw, 3)
    check("总榜条目归一化", star_item["rank"] == 3 and star_item["stars"] == 100)
    check("主题标签最多保留 4 个", len(star_item["topics"]) == 4, str(star_item["topics"]))
    check("总榜标注累计口径", star_item["period_label"] == "累计", star_item["period_label"])

    mixed = trending._translate_state(
        [
            {"description": "a", "description_zh": "甲"},
            {"description": "b", "description_zh": "b"},
        ]
    )
    check("中文化状态统计未完成比例", "1/2" in mixed, mixed)
    done_all = trending._translate_state(
        [
            {"description": "a", "description_zh": "甲"},
            {"description": "中文", "description_zh": "中文"},
        ]
    )
    check("全部中文化时给出完成提示", "全部" in done_all, done_all)
    check("空榜单状态", trending._translate_state([]) == "无数据")


# --------------------------------------------------------------------------- #
# 4. 删除远程仓库（接口层）
# --------------------------------------------------------------------------- #


def test_delete_repo_api() -> None:
    print("\n== 删除远程仓库接口 ==")
    client = platapi.RemoteClient("github", "token")
    calls: list[tuple] = []

    def fake_request(method, path, params=None, **kwargs):
        calls.append((method, path))
        return None

    client._request = fake_request  # type: ignore[assignment]
    name = client.delete_repo("me/demo")
    check("调用 DELETE /repos/<full_name>", calls == [("DELETE", "/repos/me/demo")], str(calls))
    check("返回被删除的仓库全名", name == "me/demo", name)

    bad = None
    try:
        client.delete_repo("demo")
    except platapi.ApiError as exc:
        bad = exc
    check("非法仓库标识被拦截", bad is not None and "无效" in bad.message, repr(bad))

    def forbidden(method, path, params=None, **kwargs):
        raise platapi.ApiError("访问被拒绝", status=403, detail="{}")

    client._request = forbidden  # type: ignore[assignment]
    err = None
    try:
        client.delete_repo("me/demo")
    except platapi.ApiError as exc:
        err = exc
    check("403 提示 delete_repo 权限", err is not None and "delete_repo" in err.message,
          repr(err))

    gitee = platapi.RemoteClient("gitee", "token")
    gitee._request = forbidden  # type: ignore[assignment]
    gerr = None
    try:
        gitee.delete_repo("me/demo")
    except platapi.ApiError as exc:
        gerr = exc
    check("Gitee 403 提示 projects 权限", gerr is not None and "projects" in gerr.message,
          repr(gerr))
    check("GitHub 令牌说明包含 delete_repo",
          "delete_repo" in platapi.PLATFORMS["github"].scopes)


# --------------------------------------------------------------------------- #
# 5. 界面：远程页改动 + 瀑布流
# --------------------------------------------------------------------------- #


def build_repo_rows(count: int = 12) -> list[dict]:
    rows = []
    for i in range(1, count + 1):
        rows.append(
            {
                "rank": i,
                "full_name": f"org{i}/repo-{i}",
                "owner": f"org{i}",
                "name": f"repo-{i}",
                "description": "English description " * (i % 4 + 1),
                "description_zh": "这是第 %d 个项目的简介，" % i + "说明内容" * (i % 5 + 1),
                "language": "Python" if i % 2 else "TypeScript",
                "language_color": "#3572A5",
                "stars": 1200 * i,
                "forks": 30 * i,
                "stars_period": 11 * i,
                "period_label": "本周",
                "html_url": f"https://github.com/org{i}/repo-{i}",
                "clone_url": f"https://github.com/org{i}/repo-{i}.git",
                "topics": ["llm", "agent"] if i % 3 == 0 else [],
            }
        )
    return rows


def test_ui(app: QApplication) -> None:
    print("\n== 界面：远程仓库页与热门榜 ==")
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    # ---- 导航 ----
    labels = [b.text().strip() for b in window.sidebar.buttons]
    check("侧边栏新增 GitHub 热门入口", any("GitHub 热门" in t for t in labels), str(labels))
    trending_index = next(i for i, t in enumerate(labels) if "GitHub 热门" in t)
    check("入口位于侧边栏末位（设置之后）", trending_index == len(labels) - 1, str(labels))
    window.goto_key("trending")
    app.processEvents()
    check("可跳转到热门页", window.stack.currentWidget() is window.trending_page)
    check("热门页状态栏标题正确", "GitHub 热门" in window.status_label.text(),
          window.status_label.text())

    # ---- 远程页交互 ----
    window.goto_key("explore")
    app.processEvents()
    explore = window.explore_page
    check("新增停止克隆按钮", hasattr(explore, "stop_btn") and explore.stop_btn.text() == "停止克隆")
    check("停止按钮初始不可用", not explore.stop_btn.isEnabled())
    check("新增选中项操作区",
          explore.detail_btn.text() == "查看详情" and explore.delete_btn.text() == "删除远程仓库")
    check("未选中时操作按钮禁用",
          not explore.detail_btn.isEnabled() and not explore.delete_btn.isEnabled())

    explore._rows = [
        {
            "full_name": "me/demo",
            "name": "demo",
            "private": False,
            "stars": 12,
            "html_url": "https://github.com/me/demo",
            "clone_url": "https://github.com/me/demo.git",
            "platform": "github",
        }
    ]
    explore.table.setRowCount(1)
    explore.table.setItem(0, 0, QTableWidgetItem("me/demo"))
    explore.table.selectRow(0)
    app.processEvents()
    check("左键选中即只做选中（未开始下载）",
          explore._clone_task is None and not explore._busy)
    check("选中后详情/删除可用",
          explore.detail_btn.isEnabled() and explore.delete_btn.isEnabled())
    check("选中信息回显", "me/demo" in explore.selection_label.text(),
          explore.selection_label.text())

    # 双击 = 查看详情，而不是克隆
    explore._opened: list[str] = []
    opened = explore._opened
    original_open_url = explore.__class__.__module__
    import gitdesk.ui.pages.explore as explore_module

    saved = explore_module.open_url
    explore_module.open_url = lambda url: opened.append(url)
    try:
        explore.open_detail()
        check("查看详情打开仓库主页（而非克隆）",
              opened == ["https://github.com/me/demo"], str(opened))
        check("查看详情不产生克隆任务", explore._clone_task is None and not explore._busy)
        check("查看详情给出提示", "在浏览器中打开" in explore.status_label.text(),
              explore.status_label.text())
    finally:
        explore_module.open_url = saved

    menu = explore._build_context_menu(explore._rows[0])
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    check("右键菜单含查看详情", any("查看详情" in t for t in texts), str(texts))
    check("右键菜单含克隆与删除",
          any("克隆" in t for t in texts) and any("删除" in t for t in texts), str(texts))
    check("右键菜单首项为查看详情", "查看详情" in texts[0], str(texts))

    # 搜索模式下不允许删除第三方仓库
    search_index = [
        i for i in range(explore.mode_combo.count())
        if explore.mode_combo.itemData(i) == "search"
    ][0]
    explore.mode_combo.setCurrentIndex(search_index)
    app.processEvents()
    check("搜索模式下删除按钮禁用", not explore.delete_btn.isEnabled())
    search_menu = [a.text() for a in explore._build_context_menu(explore._rows[0]).actions()
                   if not a.isSeparator()]
    check("搜索模式右键菜单无删除项", not any("删除" in t for t in search_menu), str(search_menu))
    mine_index = [
        i for i in range(explore.mode_combo.count())
        if explore.mode_combo.itemData(i) == "mine"
    ][0]
    explore.mode_combo.setCurrentIndex(mine_index)
    app.processEvents()

    # ---- 停止克隆（本地裸仓库，不依赖网络） ----
    remote = make_bare_remote("ui-remote")
    cancel_dir = os.path.join(WORK, "ui-cancel")
    explore.dest_picker.set_path(cancel_dir)
    explore._rows[0].update({"clone_url": remote, "name": "cancelled"})
    explore.table.selectRow(0)
    explore.clone_selected()
    check("克隆启动后停止按钮可用", explore.stop_btn.isEnabled(), str(explore._busy))
    explore._clone_task.cancel()
    deadline = time.time() + 10
    while explore._busy and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    check("取消后结束忙碌状态", not explore._busy, explore.status_label.text())
    check("取消提示可见", "已取消" in explore.status_label.text(), explore.status_label.text())
    check("取消后清理目标目录", not os.path.exists(os.path.join(cancel_dir, "cancelled")),
          os.path.join(cancel_dir, "cancelled"))
    check("取消后恢复按钮状态",
          explore.clone_btn.isEnabled() and not explore.stop_btn.isEnabled())
    check("取消的克隆不写入仓库清单", len(window.store.all()) == 0, str(window.store.all()))

    # ---- 删除远程仓库：二次确认不匹配时中止 ----
    explore_messages: list[str] = []
    import gitdesk.ui.pages.explore as em

    original_input = em.QInputDialog.getText
    original_confirm = em.confirm
    original_error = explore_module.show_error
    em.confirm = lambda *a, **k: True
    em.QInputDialog.getText = staticmethod(lambda *a, **k: ("错误的仓库名", True))
    em.show_error = lambda *a, **k: explore_messages.append(a[2] if len(a) > 2 else "")
    try:
        api_calls: list[str] = []
        original_run = explore.tasks.run
        explore.tasks.run = lambda fn, *a, **k: api_calls.append(fn.__name__)  # type: ignore
        explore.delete_remote()
        check("二次确认不匹配时拒绝删除", not api_calls, str(api_calls))
        check("给出确认失败提示", any("不匹配" in m for m in explore_messages),
              str(explore_messages))
    finally:
        em.QInputDialog.getText = original_input
        em.confirm = original_confirm
        em.show_error = original_error
        explore.tasks.run = original_run  # 恢复共享 TaskCenter，避免污染后续用例

    # ---- 瀑布流 ----
    trending_page = window.trending_page
    window.goto_key("trending")
    app.processEvents()
    rows = build_repo_rows(12)
    trending_page._render(
        {
            "items": rows,
            "source_label": "趋势榜 · 本周",
            "fetched_at": "2026-10-12 17:00:00",
            "translated_state": "已中文化 12/12 条简介",
        }
    )
    app.processEvents()
    trending_page.masonry.refresh_layout()
    app.processEvents()

    cards = trending_page.masonry.cards
    check("瀑布流渲染全部卡片", len(cards) == len(rows), str(len(cards)))
    check("卡片展示中文简介",
          all(c.desc.text() == r["description_zh"] for c, r in zip(cards, rows)))
    check("已翻译时把英文原文挂到提示中",
          cards[0].desc.toolTip().startswith("原文：") and rows[0]["description"] in cards[0].desc.toolTip(),
          cards[0].desc.toolTip()[:40])
    check("卡片首行含排名徽标", [c.rank_label.text() for c in cards[:3]] == ["1", "2", "3"])
    check("卡片展示星标", "★" in cards[0].subtitle.text(), cards[0].subtitle.text())
    check("卡片展示当期新增星标", "+11" in cards[0].subtitle.text(), cards[0].subtitle.text())
    check("主题标签渲染为 chip", len(cards[2].topic_chips) == 2, str(cards[2].topic_chips))
    check("无主题时不渲染标签区", len(cards[0].topic_chips) == 0)

    width = trending_page.masonry.viewport().width()
    columns = trending_page.masonry.canvas.column_count(width)
    check("按宽度自动分列", columns >= 2, f"列数={columns} 宽度={width}")
    xs = sorted({c.x() for c in cards})
    check("卡片按列对齐", len(xs) == columns, f"{xs} vs 列数{columns}")
    check("列内卡片宽度一致", len({c.width() for c in cards}) == 1, str({c.width() for c in cards}))

    rects = [(c.x(), c.y(), c.width(), c.height()) for c in cards]
    overlaps = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            ax, ay, aw, ah = rects[i]
            bx, by, bw, bh = rects[j]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                overlaps += 1
    check("瀑布流卡片互不重叠", overlaps == 0, f"重叠 {overlaps} 对")

    heights = {}
    for card in cards:
        heights.setdefault(card.x(), 0)
    # 高度错落：至少存在两列高度不同（说明是瀑布流而非等高网格）
    column_bottoms = {}
    for card in cards:
        column_bottoms[card.x()] = max(column_bottoms.get(card.x(), 0), card.y() + card.height())
    check("各列高度自然错落", len(set(column_bottoms.values())) > 1, str(column_bottoms))
    check("画布高度容纳全部卡片",
          trending_page.masonry.canvas.height() >= max(column_bottoms.values()),
          f"{trending_page.masonry.canvas.height()} vs {max(column_bottoms.values())}")

    card = cards[0]
    opened.clear()
    card.open_requested.connect(opened.append)
    card.detail_btn.click()
    check("卡片「查看详情」发出打开请求", opened == [rows[0]["html_url"]], str(opened))

    import gitdesk.ui.pages.trending as trending_module

    saved_trending_open = trending_module.open_url
    trending_module.open_url = opened.append
    opened.clear()
    try:
        trending_page._on_open("https://github.com/example/detail")
        check("热门页把详情请求交给浏览器打开",
              opened == ["https://github.com/example/detail"], str(opened))
    finally:
        trending_module.open_url = saved_trending_open

    cloned: list[dict] = []
    trending_page.clone_repo = cloned.append  # type: ignore[assignment]
    card.clone_btn.click()
    check("卡片「克隆到本地」发出克隆请求",
          cloned and cloned[0]["full_name"] == rows[0]["full_name"], str(cloned))

    # 已加载后重复进入页面不会重复抓取
    trending_page.auto_load()
    check("已加载数据时不重复请求", trending_page._task is None, str(trending_page._task))

    # 榜单切换：总榜禁用时间范围
    stars_index = [
        i for i in range(trending_page.board_combo.count())
        if trending_page.board_combo.itemData(i) == "stars"
    ][0]
    trending_page.board_combo.setCurrentIndex(stars_index)
    app.processEvents()
    check("星标总榜禁用时间范围选择", not trending_page.period_combo.isEnabled())
    trending_page.board_combo.setCurrentIndex(0)
    app.processEvents()
    check("趋势榜恢复时间范围选择", trending_page.period_combo.isEnabled())

    # ---- 瀑布流卡片背景（深色系统主题下不应出现黑底） ----
    canvas = trending_page.masonry.canvas
    check("瀑布流画布有独立对象名", canvas.objectName() == "MasonryCanvas", canvas.objectName())
    check("瀑布流视口有独立对象名",
          trending_page.masonry.viewport().objectName() == "MasonryViewport",
          trending_page.masonry.viewport().objectName())
    check("画布显式声明浅色背景",
          "QWidget#MasonryCanvas" in STYLESHEET and "#f4f6f9" in STYLESHEET)
    check("应用调色板锁定为浅色主题",
          QApplication.palette().color(QPalette.Window).name() == "#f4f6f9",
          QApplication.palette().color(QPalette.Window).name())
    image = canvas.grab().toImage()
    corner = image.pixelColor(1, 1).name().lower() if not image.isNull() else ""
    check("画布角落为浅色（非黑底）", corner == "#f4f6f9", corner)

    # ---- 传输详情面板（上传 / 下载共用） ----
    for owner, name in (
        (window.explore_page, "远程仓库页"),
        (window.trending_page, "热门页"),
        (window.repos_page.detail, "本地仓库页"),
    ):
        check(f"{name}具备传输详情面板",
              hasattr(owner, "transfer") and owner.transfer.objectName() == "TransferPanel")

    panel = window.repos_page.detail.transfer
    check("详情面板初始隐藏", panel.isHidden())
    panel.begin("正在推送分支 main…", kind="上传 · 推送")
    check("开始后详情面板可见", not panel.isHidden() and panel.active)
    check("开始时进度条为不确定态", panel.bar.maximum() == 0)
    panel.update_detail(
        {
            "label": "推送",
            "kind": "push",
            "phase": "发送对象",
            "message": "Writing objects: 33% (3/9)",
            "objects_text": "3/9",
            "bytes_in_text": "—",
            "bytes_out_text": "1.2 MB",
            "speed": "300 KiB/s",
            "speed_avg": "500 KB/s",
            "percent": 33.0,
            "elapsed": 4.2,
            "retries": 1,
            "summary": "已发送 1.2 MB · 对象 3/9 · 重试 1 次",
        }
    )
    check("详情回填阶段", panel.fields["phase"].text() == "发送对象", panel.fields["phase"].text())
    check("详情回填对象计数", panel.fields["objects"].text() == "3/9", panel.fields["objects"].text())
    check("详情回填已发送字节", panel.fields["bytes_out"].text() == "1.2 MB",
          panel.fields["bytes_out"].text())
    check("详情回填平均速度", panel.fields["speed_avg"].text() == "500 KB/s",
          panel.fields["speed_avg"].text())
    check("详情回填重试次数", panel.fields["retries"].text() == "1 次", panel.fields["retries"].text())
    check("进度条按百分比推进", panel.bar.maximum() == 100 and panel.bar.value() == 33,
          str(panel.bar.value()))
    check("摘要行展示整体进度", "1.2 MB" in panel.summary_label.text(), panel.summary_label.text())
    panel.set_message("正在发送对象 33%")
    check("进度文本写入详情日志", "正在发送对象" in panel.log.toPlainText(),
          panel.log.toPlainText()[:60])
    panel.toggle_btn.setChecked(True)
    check("展开后显示明细区", not panel.detail_box.isHidden())
    panel.finish("推送成功", ok=True)
    check("完成后进度条满格", panel.bar.value() == 100 and not panel.active)
    panel.reset()
    check("重置后详情面板隐藏", panel.isHidden())

    # ---- 停止按钮（上传） ----
    from gitdesk.ui.widgets import TransferPanel

    bare = TransferPanel()
    bare.resize(900, 200)
    bare.show()
    app.processEvents()
    bare.begin("正在推送分支 main…", kind="上传 · 推送", cancellable=True)
    check("上传时出现停止按钮",
          not bare.stop_btn.isHidden() and bare.stop_btn.text() == "停止",
          f"{bare.stop_btn.text()} hidden={bare.stop_btn.isHidden()}")
    fired: list[int] = []
    bare.stop_requested.connect(lambda: fired.append(1))
    bare.stop_btn.click()
    check("点击停止发出停止请求", len(fired) == 1, str(fired))
    check("点击停止后按钮进入等待态",
          not bare.stop_btn.isEnabled() and bare.stop_btn.text() == "正在停止…",
          f"{bare.stop_btn.text()} enabled={bare.stop_btn.isEnabled()}")
    check("停止请求写入过程日志", "已请求停止" in bare.log.toPlainText())
    bare.finish("已停止推送。", ok=False, cancelled=True)
    check("已停止时进度条换色", bare.bar.property("state") == "cancel",
          str(bare.bar.property("state")))
    check("结束后按钮退化为关闭", bare.stop_btn.text() == "关闭" and bare.stop_btn.isEnabled(),
          bare.stop_btn.text())
    bare.stop_btn.click()
    check("点击关闭后面板收起", bare.isHidden())

    bare.begin("正在推送分支 main…", kind="上传 · 推送", cancellable=True)
    bare.finish("推送失败：网络连接被中断", ok=False)
    check("失败时进度条为错误配色", bare.bar.property("state") == "error",
          str(bare.bar.property("state")))
    check("失败后停止/关闭按钮仍可用",
          bare.stop_btn.isEnabled() and not bare.stop_btn.isHidden())
    bare.reset()
    check("重置后停止按钮隐藏并复位",
          bare.stop_btn.isHidden() and bare.stop_btn.text() == "停止")

    bare.begin("正在克隆示例仓库…", kind="下载 · 克隆")
    check("下载类操作不显示停止按钮", bare.stop_btn.isHidden())
    bare.reset()

    # ---- 页面接线：停止按钮 → 取消上传任务 ----
    repos_detail = window.repos_page.detail

    class _FakeTask:
        """替代 QThread：只记录是否收到取消请求。"""

        def __init__(self) -> None:
            self.cancelled = False

        def isRunning(self) -> bool:
            return True

        def cancel(self) -> None:
            self.cancelled = True

    fake = _FakeTask()
    repos_detail._upload_task = fake
    repos_detail.transfer.begin("正在推送分支 main…", kind="上传 · 推送", cancellable=True)
    repos_detail.transfer.stop_btn.click()
    check("运行中点击停止会取消上传任务", fake.cancelled, str(fake.cancelled))
    repos_detail._upload_task = None
    repos_detail.transfer.finish("已停止推送。", ok=False, cancelled=True)
    repos_detail.transfer.stop_btn.click()
    check("任务结束后点击按钮收起面板", repos_detail.transfer.isHidden())

    # ---- 项目结构树 ----
    local = os.path.join(WORK, "struct-repo")
    git("init", "--initial-branch=main", local)
    git("config", "user.name", "Tester", cwd=local)
    git("config", "user.email", "tester@example.com", cwd=local)
    os.makedirs(os.path.join(local, "src", "ui"), exist_ok=True)
    with open(os.path.join(local, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# struct\n")
    with open(os.path.join(local, "src", "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")
    git("add", "-A", cwd=local)
    git("commit", "-m", "init", cwd=local)
    window.goto_key("repos")
    window.repos_page._register_path(local)
    for _ in range(60):
        app.processEvents()
        time.sleep(0.02)

    detail = window.repos_page.detail
    check("新增项目结构标签页",
          detail.tabs.tabText(detail.structure_index) == "项目结构",
          detail.tabs.tabText(detail.structure_index))
    check("概览提供项目结构入口", detail.btn_structure.text() == "查看项目结构")

    detail.tabs.setCurrentIndex(detail.structure_index)
    deadline = time.time() + 10
    while detail._struct_loading and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    roots = [
        detail.struct_tree.topLevelItem(i).text(0)
        for i in range(detail.struct_tree.topLevelItemCount())
    ]
    check("结构树列出根层内容", roots == ["src", "README.md"], str(roots))
    check("结构树汇总信息可读",
          "2 个目录" in detail.struct_summary.text() and "2 个文件" in detail.struct_summary.text(),
          detail.struct_summary.text())

    src_item = detail.struct_tree.topLevelItem(0)
    src_item.setExpanded(True)
    deadline = time.time() + 10
    while src_item.childCount() <= 1 and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    children = [src_item.child(i).text(0) for i in range(src_item.childCount())]
    check("展开目录按需加载子项", children == ["ui", "main.py"], str(children))

    # 关键字搜索：跨层命中并自动展开父目录
    detail.struct_filter.setText("main")
    detail.refresh_structure(force=True)
    deadline = time.time() + 10
    while detail._struct_loading and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    roots = [
        detail.struct_tree.topLevelItem(i).text(0)
        for i in range(detail.struct_tree.topLevelItemCount())
    ]
    check("结构树按名称跨层搜索", roots == ["src"], str(roots))
    matched = roots and [
        detail.struct_tree.topLevelItem(0).child(i).text(0)
        for i in range(detail.struct_tree.topLevelItem(0).childCount())
    ]
    check("搜索命中项被直接展示", matched == ["main.py"], str(matched))
    check("搜索给出结果提示", "匹配" in detail.struct_summary.text(), detail.struct_summary.text())
    detail.struct_filter.clear()

    window.repos_page.shutdown()
    window.close()
    app.processEvents()


def test_network_and_details() -> None:
    """网络中断识别 / 自动重试 / 传输详情解析。"""
    print("\n== 网络故障识别与自动重试 ==")
    from gitdesk.core import gitengine as engine

    reset = engine.GitProtocolError(
        ("('Connection aborted.', ConnectionResetError(10054, "
         "'远程主机强迫关闭了一个现有的连接。'))",)
    )
    check("识别连接被重置为可重试故障", engine.is_transient_error(reset))
    check("识别 10053（本机中止连接）",
          engine.is_transient_error(ConnectionAbortedError(10053, "已被软件中止")))
    check("识别连接超时", engine.is_transient_error(TimeoutError("timed out")))
    rejected = engine.SendPackError(b"non-fast-forward")
    check("推送被拒绝不属于可重试故障", not engine.is_transient_error(rejected))
    check("普通业务异常不属于可重试故障",
          not engine.is_transient_error(engine.GitError("该目录不是 Git 仓库")))

    message, detail = engine.humanize_error(reset, retried=2)
    check("中断提示为可读文案", "网络连接被中断" in message, message)
    check("提示中包含已重试次数", "已自动重试 2 次" in message, message)
    check("技术细节保留原始异常", "10054" in detail, detail)
    check("非快进推送仍给出拉取提示",
          "先拉取远程更新" in engine.humanize_error(rejected)[0])

    calls: list[int] = []
    sleeps: list[float] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise engine.GitProtocolError(("('Connection aborted.', OSError(10054, 'x'))",))
        return "ok"

    original_sleep = engine.time.sleep
    engine.time.sleep = sleeps.append  # type: ignore[assignment]
    try:
        result = engine._retry_network(flaky, label="测试", attempts=3)
    finally:
        engine.time.sleep = original_sleep  # type: ignore[assignment]
    check("瞬时故障会被自动重试直到成功", result == "ok" and len(calls) == 3, str(calls))
    check("重试之间按退避等待", len(sleeps) == 2 and sleeps[1] > sleeps[0], str(sleeps))

    attempts: list[int] = []

    def always_fail():
        attempts.append(1)
        raise ConnectionResetError(10054, "断了")

    engine.time.sleep = sleeps.append  # type: ignore[assignment]
    try:
        caught = None
        try:
            engine._retry_network(always_fail, label="测试", attempts=2)
        except Exception as exc:  # noqa: BLE001
            caught = exc
    finally:
        engine.time.sleep = original_sleep  # type: ignore[assignment]
    check("重试次数用尽后抛出原异常",
          isinstance(caught, ConnectionResetError) and len(attempts) == 2, str(len(attempts)))

    check("进度文本解析出阶段", engine.parse_progress_line("Writing objects: 100% (5/5)")["phase"] == "发送对象")
    check("无关文本不误判阶段", engine.parse_progress_line("hello world")["phase"] == "")
    check("传输类型有中文名", engine.TRANSFER_KINDS["push"] == "推送")


def test_structure_tree() -> None:
    """项目结构树的数据层（纯函数）。"""
    print("\n== 项目结构树 ==")
    from gitdesk.core import tree

    root = os.path.join(WORK, "structure")
    os.makedirs(os.path.join(root, "src", "ui"), exist_ok=True)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo\n")
    with open(os.path.join(root, "src", "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")
    with open(os.path.join(root, "src", "ui", "widget.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    with open(os.path.join(root, ".git", "HEAD"), "w", encoding="utf-8") as fh:
        fh.write("ref\n")

    entries = tree.list_dir(root)
    names = [e["name"] for e in entries]
    check("根层不展示 .git", ".git" not in names, str(names))
    check("目录排在文件之前", names[0] == "src" and names[-1] == "README.md", str(names))
    check("目录带子项计数", next(e for e in entries if e["name"] == "src")["child_count"] == 2,
          str(entries))
    check("文件带大小", next(e for e in entries if e["name"] == "README.md")["size"] > 0)

    only_dirs = tree.list_dir(root, dirs_only=True)
    check("仅显示目录时过滤文件", [e["name"] for e in only_dirs] == ["src"], str(only_dirs))

    filtered = tree.list_dir(root, "src", keyword="main")
    check("关键字过滤生效", [e["name"] for e in filtered] == ["main.py"], str(filtered))

    nested = tree.list_dir(root, "src")
    check("可按相对路径列出子层", {e["rel"] for e in nested} == {"src/main.py", "src/ui"}, str(nested))

    stats = tree.summarize(root)
    check("统计文件数正确", stats["files"] == 3, str(stats))
    check("统计目录数正确", stats["dirs"] == 2, str(stats))
    check("统计不含 .git 内容", stats["size"] > 0 and not stats["truncated"], str(stats))
    check("空目录返回空列表", tree.list_dir(root, "不存在") == [])
    hits = tree.filter_tree(root, "main")
    check("递归搜索可命中嵌套文件",
          [e["rel"] for e in hits] == ["src"]
          and hits[0]["children"][0]["name"] == "main.py",
          str(hits))
    check("递归搜索保留祖先目录标记",
          hits[0].get("force_expand") is True, str(hits[0]))
    check("递归搜索无命中时返回空", tree.filter_tree(root, "zzz") == [])
    check("空关键字不触发搜索", tree.filter_tree(root, "   ") == [])
    check("仅显示目录时文件命中被忽略", tree.filter_tree(root, "main", dirs_only=True) == [])
    check("仅显示目录时仍能找到目录",
          [e["rel"] for e in tree.filter_tree(root, "src", dirs_only=True)] == ["src"])
    check("大小格式化可读", tree.format_size(2048) == "2.0 KB", tree.format_size(2048))
    check("零字节显示占位", tree.format_size(0) == "—", tree.format_size(0))


def test_commit_graph() -> None:
    """分支页签的提交演化图：泳道算法 + 引擎数据源 + 自绘控件。"""
    print("\n== 提交演化图 ==")
    from gitdesk.core.graph import layout_graph
    from gitdesk.ui.graphview import CommitGraphView

    def commit(sha: str, parents=(), refs=(), subject: str = "改动", ts: int = 0) -> dict:
        return {
            "sha": sha,
            "short": sha[:8],
            "parents": list(parents),
            "refs": list(refs),
            "subject": subject,
            "author": "Dev",
            "date": "2024-01-01 10:00:00",
            "timestamp": ts,
        }

    # 1) 线性历史：全部落在 0 号泳道
    linear = [
        commit(f"{i:040x}", [f"{i - 1:040x}"] if i > 1 else [], subject=f"第 {i} 个提交")
        for i in range(5, 0, -1)
    ]
    rows = layout_graph(linear)
    check("线性历史行数与输入一致", len(rows) == 5, str(len(rows)))
    check("线性历史全部落在 0 号泳道", all(r.lane == 0 for r in rows), str([r.lane for r in rows]))
    check("线性历史没有合并提交", not any(r.is_merge for r in rows))
    check("线性历史末行没有连线", rows[-1].connectors == [], str(rows[-1].connectors))
    check("线性历史中间行有同泳道直线",
          rows[0].connectors == [{"from": 0, "to": 0, "kind": "straight"}],
          str(rows[0].connectors))

    # 2) main 两个提交 + feature 从第 2 个提交分叉 + 合并回 main
    sha_a, sha_b, sha_d, sha_f, sha_m = "a" * 40, "b" * 40, "d" * 40, "f" * 40, "m" * 40
    fork = [
        commit(sha_m, [sha_d, sha_f], refs=["main", "HEAD"], subject="合并 feature", ts=5),
        commit(sha_d, [sha_b], subject="main 上的提交", ts=4),
        commit(sha_f, [sha_b], refs=["feature"], subject="feature 上的提交", ts=3),
        commit(sha_b, [sha_a], subject="main 第二个提交", ts=2),
        commit(sha_a, [], subject="初始提交", ts=1),
    ]
    rows = layout_graph(fork)
    lanes = {row.lane for row in rows}
    check("分叉历史出现至少两条泳道", len(lanes) >= 2, str(sorted(lanes)))
    check("合并提交被标记 is_merge", rows[0].is_merge is True and rows[1].is_merge is False,
          str([row.is_merge for row in rows]))
    check("合并提交行有分叉连线",
          any(c["kind"] == "branch" for c in rows[0].connectors), str(rows[0].connectors))
    kinds = {c["kind"] for row in rows for c in row.connectors}
    check("图中出现 merge 连接线", "merge" in kinds, str(sorted(kinds)))
    merge_targets = {c["to"] for row in rows for c in row.connectors if c["kind"] == "merge"}
    check("merge 连线汇入公共祖先所在泳道", merge_targets == {rows[3].lane},
          f"{merge_targets} vs {rows[3].lane}")
    lane_by_sha = {row.sha: row.lane for row in rows}
    check("feature 分支落在与 main 不同的泳道",
          lane_by_sha[sha_f] != lane_by_sha[sha_d], str(lane_by_sha))
    check("合并后回到 main 的泳道",
          lane_by_sha[sha_b] == lane_by_sha[sha_d] == lane_by_sha[sha_m], str(lane_by_sha))

    # 3) parents 传递：每个父提交都应出现在后续行里
    unresolved = [
        (row.sha[:4], parent[:4])
        for index, row in enumerate(rows)
        for parent in row.parents
        if parent not in {item.sha for item in rows[index + 1:]}
    ]
    check("每个父提交都能在后续行中找到", not unresolved, str(unresolved))

    # 4) refs / HEAD 标记
    check("refs 正确透传", rows[0].refs == ["main", "HEAD"], str(rows[0].refs))
    check("refs 含 HEAD 的提交被标记 is_head", rows[0].is_head is True)
    check("非 HEAD 行不带 is_head", all(not row.is_head for row in rows[1:]))

    # 5) 稳定性与容错
    again = layout_graph(fork)
    check("同一输入输出确定",
          [(r.lane, r.connectors) for r in again] == [(r.lane, r.connectors) for r in rows])
    sparse = layout_graph([{"sha": "x" * 40}, {"sha": "y" * 40, "parents": ["x" * 40]}])
    check("缺少可选字段时不抛异常（且自动按子先于父校正）",
          len(sparse) == 2
          and sparse[0].short == "y" * 8
          and sparse[0].subject == ""
          and sparse[0].connectors == [{"from": 0, "to": 0, "kind": "straight"}],
          str(sparse[0]))
    check("空输入返回空列表", layout_graph([]) == [])
    limited = layout_graph(
        [commit("z" * 40, ["x" * 40, "y" * 40, "w" * 40]), commit("x" * 40),
         commit("y" * 40), commit("w" * 40)],
        max_lanes=2,
    )
    check("泳道数量受 max_lanes 限制", max(r.lane for r in limited) <= 1,
          str([r.lane for r in limited]))

    # 6) 真实仓库：造一个分叉 + 合并的历史
    repo = os.path.join(WORK, "graph-repo")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)

    def make_commit(name: str, text: str, message: str) -> None:
        with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        git("add", "-A", cwd=repo)
        git("commit", "-m", message, cwd=repo)

    make_commit("a.txt", "a\n", "A")
    make_commit("b.txt", "b\n", "B")
    git("checkout", "-b", "feature", cwd=repo)
    make_commit("f1.txt", "f1\n", "F1")
    make_commit("f2.txt", "f2\n", "F2")
    git("checkout", "main", cwd=repo)
    make_commit("c.txt", "c\n", "C")
    git("merge", "--no-ff", "feature", "-m", "merge feature", cwd=repo)
    git("tag", "v1", cwd=repo)

    data = GitEngine.graph(repo)
    check("引擎从所有引用读取提交图", len(data) >= 6, str(len(data)))
    check("引擎识别合并提交（两个父）",
          any(len(item["parents"]) == 2 for item in data),
          str([len(item["parents"]) for item in data]))
    check("引擎给出短标签（去掉 refs/ 前缀）",
          any(item["refs"] for item in data)
          and all(not ref.startswith("refs/") for item in data for ref in item["refs"]),
          str([item["refs"] for item in data]))
    check("HEAD 标记落在当前分支的提交上",
          any("HEAD" in item["refs"] for item in data),
          str([item["refs"] for item in data]))
    check("标签以短名出现在 refs 中",
          any("v1" in item["refs"] for item in data), str([item["refs"] for item in data]))
    check("提交按时间倒序",
          all(data[i]["timestamp"] >= data[i + 1]["timestamp"] for i in range(len(data) - 1)))
    check("不存在的路径返回空列表",
          GitEngine.graph(os.path.join(WORK, "no-such-graph-repo")) == [])
    real_rows = layout_graph(data)
    check("真实仓库可生成泳道布局", len(real_rows) == len(data), f"{len(real_rows)} / {len(data)}")
    check("真实仓库出现多条泳道", len({row.lane for row in real_rows}) >= 2,
          str([row.lane for row in real_rows]))

    # 7) 自绘控件
    view = CommitGraphView()
    view.resize(560, 60)  # 视口比内容矮时出现纵向滚动
    view.set_commits(layout_graph(fork))
    check("图控件行数与数据一致", view.row_count() == len(fork), str(view.row_count()))
    check("图控件滚动范围随行数变化", view.verticalScrollBar().maximum() > 0,
          str(view.verticalScrollBar().maximum()))
    painted = True
    try:
        view.grab()  # 触发一次真实绘制，验证自绘路径不抛异常
    except Exception as exc:  # noqa: BLE001
        painted = False
        print("     绘制异常:", exc)
    check("图控件可正常自绘", painted)
    view.set_commits([])
    check("空数据不崩溃且行数为 0", view.row_count() == 0)
    view.clear()
    check("clear 后行数为 0", view.row_count() == 0)
    view.set_commits([], error="读取提交图失败")
    check("错误空状态可安全绘制", view.row_count() == 0)
    view.set_commits(layout_graph(data))
    view.resize(640, 160)
    check("真实仓库图可绘制", view.row_count() == len(data))
    view.deleteLater()

    # 8) 拓扑顺序（P0：同一时间戳 / 父子倒挂）
    from gitdesk.core.graph import layout_graph as _layout

    reversed_chain = [
        commit("a" * 40, [], subject="root"),
        commit("b" * 40, ["a" * 40], subject="mid"),
        commit("c" * 40, ["b" * 40], subject="head"),
    ]
    fixed = _layout(reversed_chain)
    check("乱序输入被拓扑校正为子先于父",
          [row.short for row in fixed] == ["c" * 8, "b" * 8, "a" * 8],
          str([row.short for row in fixed]))
    ordered_chain = [
        commit("c" * 40, ["b" * 40], subject="head"),
        commit("b" * 40, ["a" * 40], subject="mid"),
        commit("a" * 40, [], subject="root"),
    ]
    check("已拓扑有序的输入保持原序",
          [row.short for row in _layout(ordered_chain)] == ["c" * 8, "b" * 8, "a" * 8])

    same_time = [
        commit("a" * 40, [], subject="root", ts=1000),
        commit("b" * 40, ["a" * 40], subject="mid", ts=1000),
        commit("f" * 40, ["b" * 40], subject="feature", ts=1000),
        commit("d" * 40, ["b" * 40], subject="main", ts=1000),
        commit("m" * 40, ["d" * 40, "f" * 40], subject="merge", ts=1000),
    ]
    fixed = _layout(same_time)
    fixed_index = {row.sha: index for index, row in enumerate(fixed)}
    check("同一时间戳的乱序输入会把合并提交排到两个父之前",
          fixed_index["m" * 40] < fixed_index["d" * 40]
          and fixed_index["m" * 40] < fixed_index["f" * 40],
          str([row.short for row in fixed]))
    check("拓扑校正后父提交永远在子提交之后",
          all(
              fixed_index[parent] > index
              for index, row in enumerate(fixed)
              for parent in row.parents
              if parent in fixed_index
          ),
          str([(row.short, [p[:4] for p in row.parents]) for row in fixed]))

    same_env = dict(
        os.environ,
        GIT_AUTHOR_DATE="2026-01-01T00:00:00",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00",
    )

    def same_time_commit(cwd: str, name: str, message: str) -> None:
        with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
            fh.write(message + "\n")
        git("add", "-A", cwd=cwd, env=same_env)
        git("commit", "-m", message, cwd=cwd, env=same_env)

    linear_repo = os.path.join(WORK, "graph-same-time")
    git("init", "--initial-branch=main", linear_repo, env=same_env)
    git("config", "user.name", "Tester", cwd=linear_repo)
    git("config", "user.email", "tester@example.com", cwd=linear_repo)
    for step in range(1, 6):
        same_time_commit(linear_repo, f"c{step}.txt", f"提交 {step}")

    same_data = GitEngine.graph(linear_repo)
    expected = git("rev-list", "HEAD", cwd=linear_repo, env=same_env).stdout.split()
    same_shorts = [item["short"] for item in same_data]
    same_index = {item["sha"]: index for index, item in enumerate(same_data)}
    check("同一时间戳的线性仓库按 HEAD→root 返回",
          [item["sha"] for item in same_data] == expected, str(same_shorts))
    check("同一时间戳下引擎仍保证父提交在后",
          all(same_index[parent] > same_index[item["sha"]]
              for item in same_data
              for parent in item["parents"] if parent in same_index),
          str(same_shorts))

    fork_repo = os.path.join(WORK, "graph-same-time-fork")
    git("init", "--initial-branch=main", fork_repo, env=same_env)
    git("config", "user.name", "Tester", cwd=fork_repo)
    git("config", "user.email", "tester@example.com", cwd=fork_repo)
    same_time_commit(fork_repo, "a.txt", "A")
    same_time_commit(fork_repo, "b.txt", "B")
    git("checkout", "-b", "feature", cwd=fork_repo, env=same_env)
    same_time_commit(fork_repo, "f1.txt", "F1")
    git("checkout", "main", cwd=fork_repo, env=same_env)
    same_time_commit(fork_repo, "c.txt", "C")
    git("merge", "--no-ff", "feature", "-m", "merge feature", cwd=fork_repo, env=same_env)

    fork_data = GitEngine.graph(fork_repo)
    fork_index = {item["sha"]: index for index, item in enumerate(fork_data)}
    merge_item = next(item for item in fork_data if len(item["parents"]) == 2)
    check("同一时间戳的分叉合并仓库把合并提交放在最前",
          fork_index[merge_item["sha"]] == 0, str([i["short"] for i in fork_data]))
    check("同一时间戳下合并提交位于两个父之前",
          all(fork_index[parent] > 0 for parent in merge_item["parents"]),
          str({p[:8]: fork_index.get(p) for p in merge_item["parents"]}))
    check("同一时间戳下分叉仓库也无父子倒挂",
          all(fork_index[parent] > fork_index[item["sha"]]
              for item in fork_data
              for parent in item["parents"] if parent in fork_index),
          str([i["short"] for i in fork_data]))

    truncated = GitEngine.graph(fork_repo, 3)
    check("截断条数仍以 HEAD 开头（有界扩窗）",
          len(truncated) == 3 and any("HEAD" in item["refs"] for item in truncated),
          str([(i["short"], i["refs"]) for i in truncated]))
    truncated_rows = _layout(truncated)
    truncated_index = {row.sha: index for index, row in enumerate(truncated_rows)}
    check("截断后仍无父子倒挂（父不在列表时按根处理）",
          len(truncated_rows) == 3
          and all(
              truncated_index[parent] > index
              for index, row in enumerate(truncated_rows)
              for parent in row.parents
              if parent in truncated_index
          ),
          str([(row.short, [p[:4] for p in row.parents]) for row in truncated_rows]))
    # 8.1) criss-cross（双向互相合并）：每个父提交都必须能沿线走到
    def _graph_reachable(layout_rows, start: int, lane: int, target_row: int, target_lane: int) -> bool:
        """沿 connectors 从 (start, lane) 走一行一行，能否到达 (target_row, target_lane)。"""
        seen = {(start, lane)}
        stack = [(start, lane)]
        while stack:
            row_index, current = stack.pop()
            if (row_index, current) == (target_row, target_lane):
                return True
            if row_index >= target_row:
                continue
            for connector in layout_rows[row_index].connectors:
                if int(connector["from"]) != current:
                    continue
                step = (row_index + 1, int(connector["to"]))
                if step not in seen:
                    seen.add(step)
                    stack.append(step)
        return False

    def broken_links(layout_rows) -> list[tuple[str, str]]:
        """返回「父提交在输出中但连线走不到」的 (子, 父) 列表。"""
        position = {row.sha: index for index, row in enumerate(layout_rows)}
        result: list[tuple[str, str]] = []
        for index, row in enumerate(layout_rows):
            for parent in row.parents:
                if parent not in position:
                    continue
                target = position[parent]
                if not _graph_reachable(
                    layout_rows, index, row.lane, target, layout_rows[target].lane
                ):
                    result.append((row.sha[:8], parent[:8]))
        return result

    def out_of_range(layout_rows) -> list[str]:
        """返回端点落在「没有任何圆点」的越界泳道上的连线。

        不变量：任何 connector 的 from/to 都必须 ≤ 圆点实际出现过的最大泳道号，
        即图中不允许存在「永远没有圆点落座」的泳道（截断时窗口外父提交直接
        当根处理、不占泳道，因此该不变量对完整图与截断图都成立）。
        """
        max_lane = max((row.lane for row in layout_rows), default=0)
        return [
            f"{row.sha[:8]}:{connector['from']}→{connector['to']}"
            for row in layout_rows
            for connector in row.connectors
            if int(connector["from"]) > max_lane or int(connector["to"]) > max_lane
        ]

    criss_cross = [
        {"sha": "M3", "parents": ["M1", "M2"], "timestamp": 6},
        {"sha": "M2", "parents": ["x1", "M1"], "timestamp": 5},
        {"sha": "M1", "parents": ["m2", "x1"], "timestamp": 4},
        {"sha": "m2", "parents": ["m1"], "timestamp": 3},
        {"sha": "x1", "parents": ["m1"], "timestamp": 2},
        {"sha": "m1", "parents": [], "timestamp": 1},
    ]
    criss_rows = _layout(criss_cross)
    check("criss-cross 场景无断裂父子连线",
          broken_links(criss_rows) == [], str(broken_links(criss_rows)))
    criss_pos = {row.sha: index for index, row in enumerate(criss_rows)}
    check("criss-cross 中 M2→M1 的连线可达",
          _graph_reachable(criss_rows, criss_pos["M2"], criss_rows[criss_pos["M2"]].lane,
                           criss_pos["M1"], criss_rows[criss_pos["M1"]].lane))
    check("criss-cross 不为已等待的父提交重复占泳道",
          max(row.lane for row in criss_rows) <= 1,
          str([(row.sha, row.lane) for row in criss_rows]))
    check("criss-cross 的连线端点都落在有圆点的泳道上",
          out_of_range(criss_rows) == [], str(out_of_range(criss_rows)))
    check("分叉合并场景同样无断裂父子连线",
          broken_links(_layout(fork)) == [], str(broken_links(_layout(fork))))
    check("分叉合并场景的连线端点都合法",
          out_of_range(_layout(fork)) == [], str(out_of_range(_layout(fork))))
    check("截断图的连线端点同样不越界（窗口外父提交当根、不占泳道）",
          out_of_range(_layout(truncated)) == [], str(out_of_range(_layout(truncated))))

    cross_repo = os.path.join(WORK, "graph-criss-cross")
    git("init", "--initial-branch=main", cross_repo)
    git("config", "user.name", "Tester", cwd=cross_repo)
    git("config", "user.email", "tester@example.com", cwd=cross_repo)

    def cross_commit(name: str, message: str) -> None:
        with open(os.path.join(cross_repo, name), "w", encoding="utf-8") as fh:
            fh.write(message + "\n")
        git("add", "-A", cwd=cross_repo)
        git("commit", "-m", message, cwd=cross_repo)

    cross_commit("m1.txt", "m1")
    git("checkout", "-b", "x", cwd=cross_repo)
    cross_commit("x1.txt", "x1")
    git("checkout", "main", cwd=cross_repo)
    cross_commit("m2.txt", "m2")
    git("merge", "--no-ff", "x", "-m", "M1 合并 x", cwd=cross_repo)
    git("checkout", "x", cwd=cross_repo)
    git("merge", "--no-ff", "main", "-m", "M2 合并 main", cwd=cross_repo)
    git("checkout", "main", cwd=cross_repo)
    git("merge", "--no-ff", "x", "-m", "M3 再次合并 x", cwd=cross_repo)

    cross_data = GitEngine.graph(cross_repo)
    cross_rows = _layout(cross_data)
    check("真实 criss-cross 仓库读到 6 个提交", len(cross_data) == 6,
          str([item["short"] for item in cross_data]))
    check("真实 criss-cross 仓库无断裂父子连线",
          broken_links(cross_rows) == [], str(broken_links(cross_rows)))
    check("真实 criss-cross 仓库合并提交数正确",
          sum(1 for item in cross_data if len(item["parents"]) == 2) == 3,
          str([len(item["parents"]) for item in cross_data]))
    check("真实 criss-cross 仓库连线端点都落在有圆点的泳道上",
          out_of_range(cross_rows) == [], str(out_of_range(cross_rows)))

    # 8.2) 空路径不能回退到当前工作目录（dulwich 的 Repo("") 等价 "."，测试时 cwd 本身就是仓库）
    check("graph('') 在 cwd 是仓库时也返回空列表", GitEngine.graph("") == [])
    check("graph(普通文件) 返回空列表",
          GitEngine.graph(os.path.join(ROOT, "README.md")) == [])

    # 9) 集成到「分支」页签（真实窗口 + 后台任务链路）
    from PySide6.QtCore import Qt as _QtCore
    from PySide6.QtWidgets import QSplitter

    app = QApplication.instance()
    window = MainWindow()
    window.resize(1360, 860)
    window.show()
    app.processEvents()
    window.goto_key("repos")
    window.repos_page._register_path(repo)
    detail = window.repos_page.detail
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        if detail.graph_view.row_count() > 0:
            break
        time.sleep(0.02)
    app.processEvents()
    check("分支页签包含提交演化图控件", hasattr(detail, "graph_view"))
    check("切换仓库后自动填充提交图", detail.graph_view.row_count() >= 6,
          str(detail.graph_view.row_count()))
    branch_page = detail.tabs.widget(3)
    check("分支页签仍是第 4 个页签", detail.tabs.tabText(3) == "分支", detail.tabs.tabText(3))
    orientations = [s.orientation() for s in branch_page.findChildren(QSplitter)]
    check("分支页签用垂直分隔器承载演化图",
          _QtCore.Vertical in orientations, str(orientations))
    check("演化图有刷新按钮", detail.btn_graph_refresh.text() == "刷新")
    detail.graph_view.clear()
    check("清空后不残留旧仓库提交", detail.graph_view.row_count() == 0)
    detail.btn_graph_refresh.click()
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        if detail.graph_view.row_count() > 0:
            break
        time.sleep(0.02)
    app.processEvents()
    check("点击刷新可重新读取提交图", detail.graph_view.row_count() >= 6,
          str(detail.graph_view.row_count()))
    # 收尾：把临时仓库从管理列表中移除，避免影响后续用例的仓库清单断言
    detail.set_repo("")
    window.repos_page.store.remove(repo)
    window.repos_page.reload()
    window.repos_page.shutdown()
    window.close()
    app.processEvents()


def test_upload_cancel() -> None:
    """上传（推送 / 发布）可被「停止」中断。"""
    print("\n== 上传可停止 ==")
    from gitdesk.core import gitengine as engine
    from gitdesk.core.publish import PublishPlan, publish_local

    # 1) 已取消的令牌：不再发起任何尝试
    started: list[int] = []

    def never_called():
        started.append(1)
        return "ok"

    token = CancelToken()
    token.cancel()
    caught = None
    try:
        engine._retry_network(never_called, cancel=token)
    except Exception as exc:  # noqa: BLE001
        caught = exc
    check("取消后不再发起新的尝试",
          isinstance(caught, engine.OperationCancelled) and not started, str(caught))

    # 2) 退避等待期被打断：只尝试了一次就退出
    class _WaitCancelled:
        """模拟「等待重试时用户点了停止」的令牌。"""

        cancelled = False

        def wait(self, _timeout: float) -> bool:
            return True

    attempts: list[int] = []

    def boom():
        attempts.append(1)
        raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")

    caught = None
    try:
        engine._retry_network(boom, cancel=_WaitCancelled())
    except Exception as exc:  # noqa: BLE001
        caught = exc
    check("重试等待期被取消则立刻停止重试",
          isinstance(caught, engine.OperationCancelled) and len(attempts) == 1, str(attempts))

    # 3) 真实推送：令牌已取消时抛出 CancelledError（不碰网络）
    local = os.path.join(WORK, "cancel-push")
    git("init", "--initial-branch=main", local)
    git("config", "user.name", "Tester", cwd=local)
    git("config", "user.email", "tester@example.com", cwd=local)
    with open(os.path.join(local, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
    git("add", "-A", cwd=local)
    git("commit", "-m", "init", cwd=local)
    url = make_bare_remote("cancel-push")
    git("remote", "add", "origin", url, cwd=local)

    token = CancelToken()
    token.cancel()
    caught = None
    try:
        GitEngine.push(local, "origin", "main", cancel=token)
    except Exception as exc:  # noqa: BLE001
        caught = exc
    check("推送支持取消并抛出 CancelledError",
          isinstance(caught, CancelledError) and "已停止推送" in caught.message,
          f"{type(caught).__name__}: {caught}")

    # 4) 正常推送仍然可用（保证取消是可选能力）
    result = GitEngine.push(local, "origin", "main")
    check("可选参数不影响正常推送", result.get("branch") == "main", str(result))

    # 5) 发布链路透传取消
    plan = PublishPlan(path=local, url=make_bare_remote("cancel-publish"), branch="main")
    token = CancelToken()
    token.cancel()
    caught = None
    try:
        publish_local(plan, cancel=token)
    except Exception as exc:  # noqa: BLE001
        caught = exc
    check("发布支持取消", isinstance(caught, CancelledError), f"{type(caught).__name__}: {caught}")


def test_ui_layout(app: QApplication) -> None:
    """概览页在空间不足时应滚动，而不是把控件压到重叠。

    场景：窗口缩到最小尺寸 + 传输详情面板完全展开（原先会把
    「仓库状态 / 同步操作」卡片压扁，按钮与说明文字叠在一起）。
    """
    print("\n== 界面布局：详情展开后不重叠 ==")
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    repo = os.path.join(WORK, "layout-repo")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# layout\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "init", cwd=repo)

    window.goto_key("repos")
    window.repos_page._register_path(repo)
    for _ in range(60):
        app.processEvents()
        time.sleep(0.02)

    detail = window.repos_page.detail
    check("详情面板已显示", detail.isVisible())
    detail.transfer.reset()

    window.resize(1040, 680)
    detail.transfer.begin("正在推送分支 main…", kind="上传 · 推送", cancellable=True)
    detail.transfer.toggle_btn.setChecked(True)
    for _ in range(10):
        app.processEvents()

    overview = detail.tabs.widget(0)
    check("概览页使用滚动容器", isinstance(overview, QScrollArea), type(overview).__name__)
    content = overview.widget() if isinstance(overview, QScrollArea) else overview
    check("概览内容处于滚动容器内", content.isAncestorOf(detail.btn_publish))
    check("概览内容未被压到低于最小高度",
          content.height() >= content.minimumSizeHint().height(),
          f"{content.height()} < {content.minimumSizeHint().height()}")

    status_card = detail.ov_labels["current_branch"].parentWidget()
    actions_card = detail.ov_hint.parentWidget()
    check("仓库状态卡片与同步操作卡片不重叠",
          actions_card.geometry().top() > status_card.geometry().bottom(),
          f"{actions_card.geometry().top()} <= {status_card.geometry().bottom()}")
    check("同步操作两行按钮不重叠",
          detail.btn_publish.geometry().top() > detail.btn_fetch.geometry().bottom(),
          f"{detail.btn_publish.geometry().top()} <= {detail.btn_fetch.geometry().bottom()}")
    check("按钮行未压住下方说明文字",
          detail.ov_hint.geometry().top() > detail.btn_publish.geometry().bottom(),
          f"{detail.ov_hint.geometry().top()} <= {detail.btn_publish.geometry().bottom()}")
    check("两张卡片都在可见区域内（可滚动）", actions_card.isVisible())

    window.resize(1400, 900)
    for _ in range(5):
        app.processEvents()
    detail.transfer.reset()
    window.close()


def test_remote_multi(app: QApplication) -> None:
    """远程列表多选：勾选多个远程后一次推送 / 批量删除。"""
    print("\n== 远程多选：一次推送到多个远程 ==")
    from PySide6.QtCore import Qt as _Qt

    import gitdesk.ui.pages.repos as repos_module
    from gitdesk.core.gitengine import GitEngine as Engine

    repo = os.path.join(WORK, "multi-remote")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "https://gitee.com/me/multi-remote.git", cwd=repo)
    git("remote", "add", "github", "https://github.com/me/multi-remote.git", cwd=repo)

    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    app.processEvents()
    window.goto_key("repos")
    window.repos_page._register_path(repo)

    detail = window.repos_page.detail
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        if detail.remote_list.rowCount() >= 2 and (detail._status or {}).get("branch"):
            break
        time.sleep(0.02)
    app.processEvents()

    def row_marks() -> list[bool]:
        return [
            detail.remote_list.item(r, 0).checkState() == _Qt.Checked
            for r in range(detail.remote_list.rowCount())
        ]

    check("远程列表新增勾选列", detail.remote_list.columnCount() == 3,
          str(detail.remote_list.columnCount()))
    check("远程列表行数正确", detail.remote_list.rowCount() == 2,
          str(detail.remote_list.rowCount()))
    check("名称与地址列仍可读",
          detail.remote_list.item(1, 1).text() == "github"
          and detail.remote_list.item(1, 2).text().endswith("multi-remote.git"),
          f"{detail.remote_list.item(1, 1).text()} / {detail.remote_list.item(1, 2).text()}")
    check("默认勾选全部远程", row_marks() == [True, True], str(row_marks()))
    check("已选数量提示正确", "已勾选 2 / 2" in detail.remote_sel_hint.text(),
          detail.remote_sel_hint.text())
    check("全选按钮初始为选中态", detail.btn_remote_all.isChecked())
    check("新增「推送到选中远程」按钮",
          detail.btn_remote_push.text() == "推送到选中远程")

    # ---- 单独取消一个勾选 ----
    detail.remote_list.item(0, 0).setCheckState(_Qt.Unchecked)
    app.processEvents()
    check("取消单个勾选后数量同步", detail._checked_remotes() == ["github"],
          str(detail._checked_remotes()))
    check("数量提示随勾选变化", "已勾选 1 / 2" in detail.remote_sel_hint.text(),
          detail.remote_sel_hint.text())
    check("部分勾选时全选按钮不亮", not detail.btn_remote_all.isChecked())

    # ---- 全选 / 全不选 ----
    detail.btn_remote_all.click()
    app.processEvents()
    check("全选按钮可一键勾选全部", detail._checked_remotes() == ["origin", "github"],
          str(detail._checked_remotes()))
    detail.btn_remote_all.click()
    app.processEvents()
    check("再次点击可取消全部勾选", detail._checked_remotes() == [],
          str(detail._checked_remotes()))

    # ---- 未勾选时给出提示而不是静默 ----
    notices: list[tuple] = []
    detail.notify.connect(lambda message, level="info": notices.append((message, level)))
    detail.do_push_selected()
    app.processEvents()
    check("未勾选时提示先勾选远程",
          any("请先勾选" in m for m, _ in notices), str(notices[-1:]))
    check("未勾选时不启动推送任务", detail._upload_task is None)

    # ---- 多选推送：一次推送到两个远程 ----
    calls: list[dict] = []
    fail_on: list[str] = []
    original_push = Engine.push

    def fake_push(path, remote, branch, **kwargs):
        calls.append(
            {
                "remote": remote,
                "branch": branch,
                "platform": kwargs.get("platform"),
                "set_upstream": kwargs.get("set_upstream"),
            }
        )
        if fail_on and remote == fail_on[0]:
            raise GitError("模拟推送失败：远端拒绝认证")
        return {"branch": branch}

    original_confirm = repos_module.confirm
    original_show_error = repos_module.show_error
    cfg_obj = detail.cfg
    original_token, original_username = cfg_obj.token, cfg_obj.username
    errors: list[tuple] = []
    Engine.push = staticmethod(fake_push)
    repos_module.confirm = lambda *a, **k: True
    repos_module.show_error = lambda *a, **k: errors.append(a)
    cfg_obj.token = lambda platform: "test-token"
    cfg_obj.username = lambda platform: "tester"
    try:
        detail.btn_remote_all.click()   # 全选
        app.processEvents()
        check("勾选多个远程后可批量推送", detail._checked_remotes() == ["origin", "github"],
              str(detail._checked_remotes()))
        detail.do_push_selected()
        deadline = time.time() + 25
        while time.time() < deadline:
            app.processEvents()
            if len(calls) >= 2 and detail._upload_task is None:
                break
            time.sleep(0.02)
        app.processEvents()
        check("推送按勾选顺序逐个执行",
              [c["remote"] for c in calls] == ["origin", "github"], str(calls))
        check("按远程地址自动匹配平台",
              [c["platform"] for c in calls] == ["gitee", "github"], str(calls))
        check("从未建立上游时多选推送不改写上游",
              [c["set_upstream"] for c in calls] == [False, False], str(calls))
        check("多远程推送完成后提示数量",
              any("已推送到 2 个远程" in m for m, _ in notices), str(notices[-2:]))
        check("多远程推送不使用单远程文案",
              not any(m == "推送成功，本地与远程已同步。" for m, _ in notices))

        # ---- 部分成功：第二个远程失败时给出可读提示 ----
        calls.clear()
        notices.clear()
        fail_on.append("github")
        detail.do_push_selected()
        deadline = time.time() + 25
        while time.time() < deadline:
            app.processEvents()
            if errors and detail._upload_task is None:
                break
            time.sleep(0.02)
        app.processEvents()
        failed_message = errors[-1][2] if errors else ""
        check("部分成功时说明已推送的远程",
              "已成功推送到 origin" in failed_message and "推送到 github 失败" in failed_message,
              failed_message[:120])
        check("失败后停止继续推送后续远程", len(calls) == 2, str(len(calls)))
        check("失败后错误面板可用",
              detail.transfer.bar.property("state") == "error",
              str(detail.transfer.bar.property("state")))
        fail_on.clear()

        # ---- 勾选状态在刷新后保留 ----
        detail.remote_list.item(0, 0).setCheckState(_Qt.Checked)
        detail.remote_list.item(1, 0).setCheckState(_Qt.Checked)
        app.processEvents()
        detail.remote_list.item(1, 0).setCheckState(_Qt.Unchecked)
        app.processEvents()
        detail.refresh_remotes()
        deadline = time.time() + 25
        while time.time() < deadline:
            app.processEvents()
            if detail.remote_list.item(1, 0) is not None and detail.remote_list.rowCount() == 2:
                break
            time.sleep(0.02)
        app.processEvents()
        check("刷新远程列表后保留勾选",
              detail._checked_remotes() == ["origin"], str(detail._checked_remotes()))

        # ---- 批量删除勾选的远程 ----
        removed: list[str] = []
        original_remove = Engine.remove_remote
        Engine.remove_remote = staticmethod(lambda path, name: removed.append(name))
        try:
            detail.remote_list.item(1, 0).setCheckState(_Qt.Checked)
            app.processEvents()
            check("批量删除前已勾选两个远程",
                  detail._checked_remotes() == ["origin", "github"],
                  str(detail._checked_remotes()))
            detail._remove_remote()
            deadline = time.time() + 25
            while time.time() < deadline:
                app.processEvents()
                if any("已删除 2 个远程" in m for m, _ in notices):
                    break
                time.sleep(0.02)
            app.processEvents()
            check("删除远程会处理全部勾选项",
                  removed == ["origin", "github"], str(removed))
            check("批量删除后提示数量",
                  any("已删除 2 个远程" in m for m, _ in notices), str(notices[-3:]))
        finally:
            Engine.remove_remote = original_remove
    finally:
        Engine.push = original_push
        repos_module.confirm = original_confirm
        repos_module.show_error = original_show_error
        cfg_obj.token = original_token
        cfg_obj.username = original_username

    detail.transfer.reset()
    window.close()
    app.processEvents()


def test_sync_target(app: QApplication) -> None:
    """同步操作以当前分支的上游远程为准，徽标点名是哪个远程。"""
    print("\n== 同步目标与徽标 ==")
    from gitdesk.core.gitengine import SyncState
    from gitdesk.core.gitengine import upstream_remote_name

    check("上游引用解析出远程名",
          upstream_remote_name("refs/remotes/github/main") == "github",
          upstream_remote_name("refs/remotes/github/main"))
    check("非远程上游不解析远程名",
          upstream_remote_name("refs/heads/main") == ""
          and upstream_remote_name("") == ""
          and upstream_remote_name(None) == "")
    check("SyncState 暴露上游远程名",
          SyncState(has_upstream=True, upstream="refs/remotes/origin/main").remote_name
          == "origin")

    repo = os.path.join(WORK, "sync-target")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "https://gitee.com/me/sync-target.git", cwd=repo)
    git("remote", "add", "github", "https://github.com/me/sync-target.git", cwd=repo)
    git("config", "branch.main.remote", "github", cwd=repo)
    git("config", "branch.main.merge", "refs/heads/main", cwd=repo)

    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    app.processEvents()
    window.goto_key("repos")
    window.repos_page._register_path(repo)
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        current = window.repos_page.detail
        sync_now = current._sync or SyncState()
        if current.remote_list.rowCount() >= 2 and sync_now.remote_name == "github":
            break
        time.sleep(0.02)
    app.processEvents()

    detail = window.repos_page.detail
    target = detail._sync_remote() or {}
    check("同步目标跟随上游远程", target.get("name") == "github", str(target))

    # 徽标要点名是哪个远程（避免多远程时把「与远程一致」误读成「全都一致」）
    detail._sync = SyncState(has_upstream=True, upstream="refs/remotes/github/main")
    detail._render_overview()
    check("一致时徽标点名远程", detail.sync_badge.text() == "与 github 一致",
          detail.sync_badge.text())

    detail._sync = SyncState(ahead=2, has_upstream=True, upstream="refs/remotes/github/main")
    detail._render_overview()
    check("领先时徽标点名远程", detail.sync_badge.text() == "领先 github 2",
          detail.sync_badge.text())

    detail._sync = SyncState(behind=3, has_upstream=True, upstream="refs/remotes/github/main")
    detail._render_overview()
    check("落后时徽标点名远程", detail.sync_badge.text() == "落后 github 3",
          detail.sync_badge.text())

    detail._sync = SyncState(ahead=1, behind=1, has_upstream=True,
                             upstream="refs/remotes/github/main")
    detail._render_overview()
    check("分叉时徽标点名远程", detail.sync_badge.text() == "与 github 已分叉",
          detail.sync_badge.text())

    detail._sync = SyncState(has_upstream=True, upstream="refs/heads/main")
    detail._render_overview()
    check("上游非远程时回退通用文案", detail.sync_badge.text() == "与上游一致",
          detail.sync_badge.text())

    # 概览提示要说明这组按钮作用于哪个远程
    detail._sync = SyncState(has_upstream=True, upstream="refs/remotes/github/main")
    detail._render_overview()
    check("概览提示写明同步目标", "「github」" in detail.ov_hint.text(),
          detail.ov_hint.text()[:60])

    # 「推送本地提交」应推到上游远程，且不改写上游配置
    calls: list[dict] = []
    from gitdesk.core.gitengine import GitEngine as Engine

    original_push = Engine.push
    import gitdesk.ui.pages.repos as repos_module

    original_show_error = repos_module.show_error
    original_token = detail.cfg.token
    Engine.push = staticmethod(
        lambda path, remote, branch, **kwargs: calls.append(
            {"remote": remote, "set_upstream": kwargs.get("set_upstream"), "branch": branch}
        ) or {"branch": branch}
    )
    repos_module.show_error = lambda *a, **k: None
    detail.cfg.token = lambda platform: "test-token"
    try:
        detail._sync = SyncState(has_upstream=True, upstream="refs/remotes/github/main")
        detail.do_push()
        deadline = time.time() + 25
        while time.time() < deadline:
            app.processEvents()
            if calls and detail._upload_task is None:
                break
            time.sleep(0.02)
        app.processEvents()
        check("推送本地提交推向上游远程",
              [c["remote"] for c in calls] == ["github"], str(calls))
        check("推送到上游远程时不改写上游配置",
              calls and calls[0]["set_upstream"] is False, str(calls))

        # 上游不在远程列表中时，同步操作退回 origin
        git("config", "--unset", "branch.main.remote", cwd=repo)
        remotes = [r for r in Engine.remotes(repo)]
        detail._sync = SyncState()
        fallback = detail._sync_remote() or {}
        check("无上游时同步目标退回 origin", fallback.get("name") == "origin",
              f"{fallback} / {remotes}")
    finally:
        Engine.push = original_push
        repos_module.show_error = original_show_error
        detail.cfg.token = original_token

    window.close()
    app.processEvents()


def test_merge_branch(app: QApplication) -> None:
    """分支合并：快进 / 合并提交 / 已包含 / 冲突回滚 / 脏工作区 / UI 入口。"""
    print("\n== 分支合并 ==")
    from gitdesk.core.gitengine import GitEngine as _Engine

    def make_repo(name: str) -> str:
        repo = os.path.join(WORK, name)
        git("init", "--initial-branch=main", repo)
        git("config", "user.name", "Tester", cwd=repo)
        git("config", "user.email", "tester@example.com", cwd=repo)
        return repo

    def commit(repo: str, fname: str, content: str, msg: str) -> str:
        with open(os.path.join(repo, fname), "w", encoding="utf-8") as fh:
            fh.write(content)
        git("add", "-A", cwd=repo)
        git("commit", "-m", msg, cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    def head(repo: str) -> str:
        return git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    # ---- 快进 ----
    repo = make_repo("merge-ff")
    commit(repo, "a.txt", "a\n", "m1")
    git("checkout", "-b", "feature", cwd=repo)
    commit(repo, "b.txt", "b\n", "f1")
    feature_sha = commit(repo, "b.txt", "b2\n", "f2")
    git("checkout", "main", cwd=repo)
    result = _Engine.merge_branch(repo, "feature")
    check("能快进时直接快进", result.get("result") == "fast_forward", str(result))
    check("快进后当前分支指向源分支", head(repo) == feature_sha)
    check("快进报告前进提交数", result.get("advanced") == 2, str(result.get("advanced")))

    # ---- 真合并（分叉 → 产生合并提交）----
    repo = make_repo("merge-real")
    commit(repo, "a.txt", "a\n", "m1")
    git("checkout", "-b", "feature", cwd=repo)
    commit(repo, "f.txt", "f\n", "f1")
    git("checkout", "main", cwd=repo)
    before_sha = commit(repo, "m.txt", "m\n", "m2")
    result = _Engine.merge_branch(repo, "feature", author="Tester", email="tester@example.com")
    parents = git("log", "-1", "--format=%P", cwd=repo).stdout.split()
    check("分叉时生成合并提交", result.get("result") == "merged", str(result))
    check("合并提交有两个父", len(parents) == 2, str(parents))
    check("合并后当前分支前进", head(repo) != before_sha)
    check("合并提交说明可读",
          "合并分支" in git("log", "-1", "--format=%s", cwd=repo).stdout,
          git("log", "-1", "--format=%s", cwd=repo).stdout)
    check("重复合并返回已包含",
          _Engine.merge_branch(repo, "feature").get("result") == "already_merged")

    # ---- 冲突：必须回滚，且不动工作区 ----
    repo = make_repo("merge-conflict")
    commit(repo, "c.txt", "base\n", "m1")
    git("checkout", "-b", "feature", cwd=repo)
    commit(repo, "c.txt", "feature\n", "f1")
    git("checkout", "main", cwd=repo)
    before_sha = commit(repo, "c.txt", "main\n", "m2")
    content_before = open(os.path.join(repo, "c.txt"), encoding="utf-8").read()
    error = ""
    try:
        _Engine.merge_branch(repo, "feature")
    except GitError as exc:
        error = exc.message
    check("冲突时给出明确提示", "冲突" in error, error[:80])
    check("冲突后 HEAD 未变", head(repo) == before_sha)
    check("冲突后工作区文件未变",
          open(os.path.join(repo, "c.txt"), encoding="utf-8").read() == content_before)
    check("冲突后不残留 MERGE_HEAD",
          not os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD")))

    # ---- 远程分支 / 脏工作区 / 不存在 / 自合并 ----
    repo = make_repo("merge-remote")
    commit(repo, "a.txt", "a\n", "m1")
    git("checkout", "-b", "hotfix", cwd=repo)
    hotfix_sha = commit(repo, "h.txt", "h\n", "h1")
    git("checkout", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/hotfix", hotfix_sha, cwd=repo)
    result = _Engine.merge_branch(repo, "origin/hotfix")
    check("可合并远程跟踪分支", result.get("result") == "fast_forward" and head(repo) == hotfix_sha,
          str(result))

    git("checkout", "-b", "dirty-src", cwd=repo)
    commit(repo, "d.txt", "d\n", "d1")
    git("checkout", "main", cwd=repo)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("dirty\n")
    error = ""
    try:
        _Engine.merge_branch(repo, "dirty-src")
    except GitError as exc:
        error = exc.message
    check("工作区有改动时拒绝合并", "未提交" in error, error[:60])
    git("checkout", "--", ".", cwd=repo)

    error = ""
    try:
        _Engine.merge_branch(repo, "no-such-branch")
    except GitError as exc:
        error = exc.message
    check("合并不存在的分支给出提示", "找不到" in error, error[:60])
    check("自己合并自己返回无需操作",
          _Engine.merge_branch(repo, "main").get("result") == "up_to_date")

    # ---- UI：分支页签入口 ----
    repo = os.path.join(WORK, "merge-ui")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)
    commit(repo, "a.txt", "a\n", "m1")
    git("checkout", "-b", "feature/ui", cwd=repo)
    commit(repo, "f.txt", "f\n", "f1")
    git("checkout", "main", cwd=repo)
    commit(repo, "m.txt", "m\n", "m2")

    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    app.processEvents()
    window.goto_key("repos")
    window.repos_page._register_path(repo)
    detail = window.repos_page.detail
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        if detail.local_table.rowCount() >= 2 and (detail._status or {}).get("branch"):
            break
        time.sleep(0.02)
    app.processEvents()

    def row_of(table, name: str) -> int:
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item and item.text().replace("★ ", "").strip() == name:
                return r
        return -1

    check("分支页签新增合并按钮",
          detail.btn_branch_merge.text() == "合并到当前分支",
          detail.btn_branch_merge.text())

    notices: list[tuple] = []
    detail.notify.connect(lambda message, level="info": notices.append((message, level)))

    detail.local_table.setCurrentCell(-1, -1)
    detail.local_table.clearSelection()
    detail.remote_table.setCurrentCell(-1, -1)
    detail.remote_table.clearSelection()
    detail._merge_selected_branch()
    app.processEvents()
    check("未选中分支时给出提示",
          any("请先" in m and "合并" in m for m, _ in notices), str(notices[-1:]))

    current_row = row_of(detail.local_table, "main")
    detail.local_table.setCurrentCell(current_row, 0)
    detail._merge_selected_branch()
    app.processEvents()
    check("选中当前分支时提示无需合并",
          any("当前分支" in m for m, _ in notices), str(notices[-1:]))

    # 选中 feature/ui → 打桩 confirm + 引擎，验证 UI 串起来
    import gitdesk.ui.pages.repos as repos_module

    calls: list[tuple] = []
    original_merge = _Engine.merge_branch
    original_confirm = repos_module.confirm
    _Engine.merge_branch = staticmethod(
        lambda path, source, **kwargs: calls.append((source, kwargs))
        or {"result": "fast_forward", "source": f"refs/heads/{source}",
            "branch": "main", "advanced": 1, "conflicts": []}
    )
    repos_module.confirm = lambda *a, **k: True
    try:
        feature_row = row_of(detail.local_table, "feature/ui")
        detail.local_table.setCurrentCell(feature_row, 0)
        detail._merge_selected_branch()
        deadline = time.time() + 25
        while time.time() < deadline:
            app.processEvents()
            if calls and not detail._busy:
                break
            time.sleep(0.02)
        app.processEvents()
        check("选中分支后调用合并", [c[0] for c in calls] == ["feature/ui"], str(calls))
        check("合并会带上提交身份", bool(calls and calls[0][1].get("author")), str(calls[:1]))
        check("合并完成给出快进提示",
              any("已快进合并" in m for m, _ in notices), str(notices[-2:]))
    finally:
        _Engine.merge_branch = original_merge
        repos_module.confirm = original_confirm

    window.close()
    app.processEvents()


def test_live(app: QApplication) -> None:
    """联网用例：仅在 GITDESK_LIVE=1 时执行。"""
    if os.environ.get("GITDESK_LIVE") != "1":
        print("\n== 联网用例（跳过，设置 GITDESK_LIVE=1 启用） ==")
        return
    print("\n== 联网用例 ==")
    payload = trending.fetch_board("trending", "weekly", 10, translate=True, with_avatars=True)
    check("真实趋势榜返回 10 条", len(payload["items"]) == 10, str(len(payload["items"])))
    check("真实简介已中文化", translator.has_chinese(payload["items"][0]["description_zh"]),
          payload["items"][0]["description_zh"][:40])
    check("真实星标数正常", payload["items"][0]["stars"] > 0, str(payload["items"][0]["stars"]))
    check("真实头像已下载", bool(payload["items"][0].get("avatar_bytes")))
    top = trending.fetch_board("stars", "daily", 10, translate=True, with_avatars=False)
    check("星标总榜返回 10 条", len(top["items"]) == 10, str(len(top["items"])))
    check("星标总榜按星标倒序",
          [i["stars"] for i in top["items"]] == sorted((i["stars"] for i in top["items"]), reverse=True))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    test_clone_cancel()
    test_translator()
    test_trending_parse()
    test_delete_repo_api()
    test_network_and_details()
    test_upload_cancel()
    test_structure_tree()
    test_commit_graph()
    test_ui(app)
    test_ui_layout(app)
    test_remote_multi(app)
    test_sync_target(app)
    test_merge_branch(app)
    test_live(app)
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  ✗", item)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
