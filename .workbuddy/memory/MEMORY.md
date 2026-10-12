# GitDesk 项目长期备忘

## 项目定位
Windows 桌面端 GitHub/Gitee 仓库管理器，交付单文件绿色 exe，内置 Git 引擎（用户无需安装 Git）。

## 技术约束（保持一致性）
- GUI 固定用 **PySide6**；不要引入 tkinter 或 Qt 附加模块（打包体积敏感）
- Git 操作一律走 `gitdesk/core/gitengine.py`，底层 **dulwich**，禁止调用系统 `git` 命令
- 界面层不得直接 import dulwich，必须通过 GitEngine
- 所有耗时操作必须经 `TaskCenter.run()` 放入后台线程；周期性任务传 `silent=True` 不污染忙状态
- 令牌必须经 `core/crypto.py` 加密后再落盘，禁止明文写入 config.json

## dulwich 使用红线（踩过的坑，勿重复）
- `status().staged` 是 `{'add':[],'delete':[],'modify':[]}` 结构，判空必须用 `_normalize_staged()`
- 索引统计字段为零化的 `IndexEntry(0,0,0,0,mode,0,0,0,sha)`，否则状态比对失真
- 合并前先用 `porcelain.merge_tree()` 只读试合并预判冲突，冲突直接中止不落地
- 分支切换/新建用 `porcelain.checkout(repo, b"HEAD", new_branch=name)`
- `Repo.init_bare()` 在 Windows 上不自建目录，必须先 `os.makedirs(path, exist_ok=True)`
- 克隆进度只从 `errstream` 得到（`report_activity` 只报 read/write 字节）；两条回调抛异常
  都不会被 dulwich 吞掉，是打断阻塞克隆的唯一手段
- 网络类瞬时故障（`ConnectionResetError(10054)` / `ConnectionAbortedError(10053)` / 超时）
  统一走 `_retry_network()`（3 次退避重试）；`humanize_error(exc, retried=N)` 才带得上重试文案
- 所有网络操作统一用 `TransferMonitor` 作 `errstream` + `report_activity`，
  它同时产出可读进度与结构化详情（界面「进度条 + 详情」共用一个数据契约）
- 要支持「中途停止」的网络方法必须把 `cancel: CancelToken` 一路透传到
  `_retry_network(..., cancel=)`；`CancelToken.wait(timeout)` 负责让退避等待也能被立刻打断
  （`_sleep_or_cancel`）。取消一律转成 `CancelledError`（`Task` 会走 `cancelled` 信号而非 `failed`）


## 发布到远程（一键建仓）
- 编排在 `core/publish.py`（纯函数、可单测），UI 只做平台/名称收集与提示
- 建仓时 `auto_init=False`：远程若带初始提交，首次推送会因历史无关被拒
- ApiError 不是 GitError，放进 TaskCenter 前须转成 GitError，否则报「未预期的错误」
- 推送失败但远程已建好时，不自动删远程，只提示用户到「远程」页重试推送

## 克隆中止
- `CancelToken` + `CancelledError(GitError)`；`Task.cancel()` / `Task.cancelled` 信号
- 引擎侧 `_CloneMonitor` 同时挂 `errstream` 与 `report_activity` 两条回调，抛异常打断阻塞克隆
- 取消后必须 rmtree 未完成目录；`CancelledError` 由界面按「已取消」文案处理，不弹错误框

## 界面层约定
- **后台任务的进度必须用 `on_progress=`（信号）**；`progress=` 会在线程里直接调用回调，
  传 Qt 控件方法进去就是跨线程操作控件，进度会静默失效
- 结构化详情同理：`tasks.run(..., on_detail=...)`，靠 `Task.detailed` 信号投递
- 上传类任务要能停止：`_set_busy(..., cancellable=True)` + 用 `self._upload_task` 持有任务，
  接到 `transfer.stop_requested` 上；面板按钮在结束后自动变「关闭」（失败后仍可清理面板）
- **页签内容必须能优雅降级**：布局是 `头 + 路径 + tabs(stretch=1) + 传输面板`，
  面板展开会吃掉 ~280px。表单型页签（卡片 + 按钮组）必须用
  `ui/widgets.py::scrollable()` 包一层 `QScrollArea`，否则被压到低于最小高度时
  Qt 会让控件**互相重叠**。带表格/树的页签自身能收缩，不包
- 判断布局是否错乱要看 **geometry 上下边关系**（`top2 > bottom1`）而不是比高度：
  `QLabel` 的 `minimumSizeHint()` 含 heightForWidth 误差，高度对比会误报
- 页面跳转用 `MainWindow.goto_key("explore"|"repos"|"accounts"|"logs"|"settings"|"trending")`，
  不硬编码索引；侧边栏共 6 项，末位为「GitHub 热门」
- 本地仓库详情的页签索引：0 概览 / 1 变更 / 2 提交 / 3 分支 / 4 远程 / 5 暂存区 / 6 项目结构。
  **新页签一律追加在末尾**（`btn_add_remote` 依赖索引 4）
- 远程列表（`remote_list`）是 **3 列**：`[勾选框, 名称, 地址]`。
  勾选缓存 `_remote_checked`（None = 默认全选）+ `_remote_path`（仓库变化即重置全选），
  **渲染空列表时不要写缓存**，否则首次拿到 0 条会把缓存钉死、之后真实远程全不勾选。
  批量推送 `do_push_selected()` 按勾选顺序循环 `GitEngine.push`，缺令牌在开始前统一提示，
  upstream 只给 `origin`（或唯一目标），失败抛「已成功推送到 X；推送到 Y 失败：…」
- **「与远程一致 / 领先 / 落后」徽标只比当前分支的上游**（`_upstream_ref()` 读
  `.git/config` 的 `branch.<name>.remote`），其余远程不参与判定。
  现已明确化：`SyncState.remote_name`（由 `upstream_remote_name()` 解析）让徽标点名叫出远程
  （「与 github 一致 / 领先 github 2」），获取 / 拉取 / 推送统一走 `_sync_remote()`
  （**跟踪远程 → origin → 第一个**），`do_push` 仅在目标≠上游时 `set_upstream=True`，
  概览提示 `_hint_text()` 写明当前作用的远程
- 主题：`ui/style.py::apply_light_theme()` 把应用锁定为浅色（`QStyleHints.setColorScheme(Light)`）。
  原因：`QScrollArea.setWidget()` 会打开子控件 autoFillBackground，深色系统主题下会自绘成黑底；
  凡是「按调色板自绘背景」的控件都必须显式指定浅色背景

## 测试桩的坑
- `explore.tasks` 与 `repos_page.detail.tasks` 是**同一个 TaskCenter 实例**；
  在测试里打桩 `tasks.run` 后务必在 finally 里恢复，否则后续用例全部静默失效
- `ReposPage` 未选中仓库时 `detail` 是隐藏的，控件几何全为残值 →
  涉及布局的断言必须先用 `_register_path()` 注册真实仓库并等 `detail.isVisible()`
- 离屏探针：`MainWindow` 的后台线程会让解释器不退出，末尾需 `flush()` 后 `os._exit(0)`；
  一律把 `GITDESK_DATA_DIR` 指到临时目录，避免污染真实用户数据


## 外部服务（已实测可用）
- 翻译：`clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl=zh-CN`（免密，质量好）
  → 备选 MyMemory；结果缓存 `GitDeskData/cache/translate-zh.json`
- 榜单：正则解析 `github.com/trending?since=daily|weekly|monthly`（可取当期新增星标）
  + `api.github.com/search/repositories?q=stars:>1000&sort=stars` 总榜
- 头像：`https://github.com/<owner>.png?size=96`
- 瀑布流：Qt 无 masonry，`ui/masonry.py` 自实现「最短列优先」+ QFontMetrics 预算简介行高

## 构建与验证流程
1. `python build/make_icon.py`（图标）
2. `python -m PyInstaller build/gitdesk.spec --noconfirm --distpath dist --workpath build/work --clean`
3. 回归：`tests/smoke_test.py`、`tests/ui_test.py`、`tests/publish_test.py`、`tests/feature_test.py`
4. 交付前必跑：`dist/GitDesk.exe --self-test <report>`（v1.1.0 基线 83 项）与 `dist/GitDesk.exe --net-check`（验证冻结包）
5. 交付同步：把根目录 `使用说明.md` / `README.md` 复制到 `dist/`，并记录 exe 体积与 SHA256

> `dist/GitDeskData/` 是绿色版**运行数据**（密文令牌、仓库清单、日志、翻译缓存），
> 属于用户实际配置，不是构建产物 —— 清理/重打包时不要删除它。

## 环境
- 隔离 venv：`C:\Users\32721\.workbuddy\binaries\python\envs\gitdesk`
- PySide6 需从官方 PyPI 安装（清华镜像不收录 PySide6）
