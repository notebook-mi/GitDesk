"""新增功能回归测试。

覆盖三项本次迭代交付的能力：

1. 克隆可中止（停止克隆按钮 + 取消令牌 + 残留目录清理）；
2. 远程仓库页交互（左键仅选中、右键查看详情、删除远程仓库）；
3. GitHub 热门榜瀑布流（榜单解析 + 简介中文化 + 最短列排布）。

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


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


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
    test_ui(app)
    test_ui_layout(app)
    test_live(app)
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  ✗", item)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
