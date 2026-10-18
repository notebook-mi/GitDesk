# GitDesk · GitHub / Gitee 仓库管理器

一款面向 Windows 的桌面端 Git 仓库管理工具，同时对接 **GitHub** 与 **Gitee**，
内置纯 Python 实现的 Git 引擎（dulwich），**用户无需额外安装 Git 环境**即可完成
克隆、拉取、推送、分支管理等操作。最终交付物为**单文件、免安装的 `GitDesk.exe`**。

---

## 一、功能清单

| 编号 | 功能 | 说明 |
| --- | --- | --- |
| 1 | 双平台账号授权 | GitHub / Gitee 分别配置访问令牌，一键测试连接并显示账号信息 |
| 2 | 远程仓库浏览与搜索 | 拉取「我的仓库」列表，或按关键字搜索；SSH/HTTPS 克隆地址自动生成 |
| 3 | 一键克隆到本地 | 选择目标目录后双击仓库即可克隆，带实时进度提示 |
| 4 | 导入本地仓库 | 把已有的 Git 项目纳入管理，自动识别平台与远程地址 |
| 5 | 双向同步 | 拉取（快进/合并/强制覆盖）、推送、仅获取、分支切换与增删 |
| 6 | 状态可视化 | 当前分支、上游分支、领先/落后提交数、HEAD 信息、变更文件清单 |
| 7 | 提交与差异 | 暂存/取消暂存/丢弃、提交、查看任意提交的完整 diff |
| 8 | 暂存区（stash） | 暂存与恢复未提交改动，便于先同步远程再回填本地 |
| 9 | 内置 Git 引擎 | dulwich 纯 Python 实现，不需要 `git.exe` |
| 10 | 绿色单文件 | PyInstaller onefile 打包，运行时不写注册表 |
| 11 | 操作日志 | 界面内实时日志 + 按天滚动写入本地文件，便于排查同步异常 |
| 12 | 实时变更检测 | 定时扫描工作区，发现未提交改动即提示；定期探测远程是否有新提交 |
| 13 | 冲突预警与保护 | 拉取前检查工作区；分叉时只读试合并预判冲突，**冲突时不做任何改动** |
| 14 | 令牌安全存储 | Windows DPAPI 加密，与当前用户账户绑定，不落明文 |
| 15 | **一键发布到远程** | 新建仓库后可直接在 **GitHub 或 Gitee** 建仓并推送：平台可选、名称/描述/可见性可填、空目录自动生成首个提交 |
| 16 | **克隆可中止** | 下载过程中「停止克隆」即可中断；已取消的克隆会清理未完成目录，不留半个仓库 |
| 17 | **左键仅选中 / 右键看详情** | 单击只做选中，双击或右键菜单「查看详情」直接在浏览器打开该仓库页面 |
| 18 | **删除远程仓库** | 「我的仓库」支持删除远程仓库，含后果告知 + 输入仓库全名的二次确认（需 `delete_repo` / `projects` 权限） |
| 19 | **GitHub 热门榜（瀑布流）** | 新增独立页面：趋势榜（今日/本周/本月）与星标总榜 Top N，**瀑布流卡片**展示，**简介自动翻译成中文** |
| 20 | **网络中断自动重试** | 推送/拉取/克隆遇连接被中断（10054/10053 等瞬时故障）自动退避重试 3 次，并给出可读提示与处理建议 |
| 21 | **上传/下载进度详情** | 进度条下方新增传输详情面板：阶段、已收发字节、对象计数、平均速度、耗时、重试次数与过程日志 |
| 22 | **项目结构树** | 本地仓库新增「项目结构」页签，目录树展示工作区结构，按需加载 / 跨层搜索 / 仅显示目录 / 双击打开 |
| 23 | **浅色卡片外观** | 固定浅色主题，热门页卡片区始终为浅色卡片底，深色 Windows 主题下不再出现黑底 |
| 24 | **上传可随时停止** | 推送/发布时面板提供「停止」按钮，传输中或自动重试等待期均可立刻中止；操作结束后按钮变为「关闭」，失败后同样可点 |
| 25 | **页签内容不再被压扁** | 概览页放入滚动容器：窗口变矮或传输详情展开时改为滚动，卡片/按钮/说明文字不再互相重叠 |
| 26 | **远程多选推送** | 远程列表支持勾选（默认全选 / 一键全选），可把当前分支一次推送到多个远程（如同时推 Gitee 与 GitHub）；按地址自动匹配平台与令牌，失败时说明哪些远程已成功；删除远程同样支持多选 |
| 27 | **同步目标明确化** | 徽标点名上游远程（「与 github 一致 / 落后 origin 3」），获取·拉取·推送统一以跟踪远程为目标且不擅自改写上游；概览直接写明当前作用于哪个远程 |
| 28 | **提交演化图** | 「分支」页签下半区新增泳道图：遍历**全部本地/远程分支**画出最近 80 条提交的分叉与合并，HEAD 实心高亮、refs 徽标、虚线表示汇入；纯 QPainter 自绘，支持滚动/悬停/深浅主题 |
| 29 | **分支合并** | 「分支」页签新增「合并到当前分支」：选中本地或远程分支即可合并；能快进则快进，分叉则生成合并提交；**冲突时打开解决窗口**，可逐文件编辑（三方着色 / 一键采用某侧）并完成或中止合并 |
| 30 | **删除远程分支** | 「删除分支」按选中位置分派：选中远程分支（如 `origin/feature`）即执行 `push --delete` 真正删除远程仓库上的分支，远程默认分支受保护、远程拒绝原因原样转达，成功后本地 `refs/remotes/...` 同步清理 |

---

## 二、目录结构

```
.
├─ gitdesk/                  应用源码
│  ├─ core/                  核心层（与界面无关，可独立测试）
│  │  ├─ paths.py            绿色优先的路径解析
│  │  ├─ crypto.py           DPAPI 令牌加解密
│  │  ├─ config.py           配置与仓库清单持久化
│  │  ├─ logger.py           日志系统（文件 + 界面订阅）
│  │  ├─ platapi.py          GitHub / Gitee API 客户端（含建仓 / 删仓接口）
│  │  ├─ gitengine.py        dulwich Git 引擎封装（克隆取消令牌 / 传输详情 / 网络重试 / 提交图遍历）
│  │  ├─ graph.py            提交泳道布局：拓扑校正 + 活跃泳道 + 分叉/汇入连线（纯函数）
│  │  ├─ publish.py          发布编排：配远程 → 补齐首个提交 → 推送
│  │  ├─ translator.py       简介中文化：多源翻译 + 磁盘缓存
│  │  ├─ trending.py         GitHub 榜单抓取：趋势榜解析 + 星标总榜 + 头像
│  │  └─ tree.py             项目结构树：目录读取 / 递归搜索 / 统计（纯函数）
│  ├─ ui/                    界面层（PySide6）
│  │  ├─ style.py            主题与 QSS（含浅色主题锁定）
│  │  ├─ widgets.py          通用组件（卡片/徽标/Diff 视图/浮层提示/传输详情面板）
│  │  ├─ workers.py          后台任务封装（支持取消 + 结构化详情回调）
│  │  ├─ masonry.py          瀑布流控件与仓库卡片（最短列优先排布）
│  │  ├─ graphview.py        提交演化图控件：QPainter 自绘泳道/圆点/连线/refs 徽标
│  │  ├─ publish_dialog.py   发布到远程仓库对话框（平台/名称/可见性）
│  │  ├─ main_window.py      主窗口与导航
│  │  └─ pages/              各功能页面（含 trending.py 热门榜）
│  └─ main.py                程序入口
├─ tests/
│  ├─ smoke_test.py          Git 引擎 / 配置 / 加密 端到端测试（47 项）
│  ├─ ui_test.py             界面层冒烟测试（25 项，offscreen）
│  ├─ publish_test.py        发布链路 + 建仓接口 + 发布对话框（54 项）
│  └─ feature_test.py        克隆中止 / 翻译 / 榜单 / 删仓 / 瀑布流 / 重试 / 详情面板 / 结构树 / 停止上传 / 布局 / 远程多选 / 同步目标 / 提交演化图 / 分支合并 / 冲突窗口 / 删除远程分支（369 项）
├─ build/
│  ├─ gitdesk.spec           PyInstaller 打包配置
│  └─ make_icon.py           生成多尺寸应用图标
├─ assets/                   图标资源
├─ run_gitdesk.py            可执行文件入口
└─ requirements.txt
```

---

## 三、从源码运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run_gitdesk.py
```

## 四、运行测试

```bash
# Git 引擎 / 配置 / 加密（使用本机 git 仅作测试夹具）
.venv\Scripts\python tests\smoke_test.py

# 界面层（offscreen，无需显示器）
.venv\Scripts\python tests\ui_test.py

# 发布链路（本地裸仓库模拟远程，不依赖系统 Git 与真实令牌）
.venv\Scripts\python tests\publish_test.py

# 本次迭代新增能力（克隆中止 / 翻译 / 榜单解析 / 删仓接口 / 瀑布流排布 /
#   网络重试 / 传输详情面板 / 项目结构树）
.venv\Scripts\python tests\feature_test.py

# 追加联网用例（真实抓取榜单并翻译）
set GITDESK_LIVE=1 && .venv\Scripts\python tests\feature_test.py

# 打包后自检（验证冻结包内的 Git 引擎、发布链路与新增能力）
dist\GitDesk.exe --self-test dist\selftest-report.txt
```

## 五、打包为单文件 exe

```bash
python build/make_icon.py            # 生成图标（可选，仓库已包含）
python -m PyInstaller build/gitdesk.spec --noconfirm --distpath dist --workpath build/work --clean
```

产物：`dist/GitDesk.exe`

### 交付前自检（必跑）

```bash
dist/GitDesk.exe --self-test dist/selftest-report.txt   # 离线自检，写入报告文件
dist/GitDesk.exe --net-check                            # 联网自检（GitHub API / 榜单解析 / 翻译）
```

`--self-test` 覆盖仓库操作、发布流程、令牌加密、翻译缓存、瀑布流布局、网络故障识别与重试、
上传停止、传输详情解析、项目结构树、页签滚动布局、远程多选、同步目标、提交演化图、分支合并、冲突解决窗口、删除远程分支、界面组件等 122 项用例，退出码 0 表示全部通过。

本轮构建（v1.2.2）实测：

| 项 | 值 |
| --- | --- |
| 体积 | 53,042,681 字节（约 50.6 MiB） |
| SHA256 | `63c7795a3d420d3dfd2825ad72592c1b45110a28b8611e13d50d7d08f1cb467a` |
| 离线自检 | 通过 118 项，失败 0 项 |
| 联网自检 | GitHub API 通过（HTTP 200）；趋势榜 / 翻译接口是否可用取决于当前网络与代理环境 |

---

## 六、数据存储位置

程序优先使用**自身所在目录**下的 `GitDeskData/`（真正的绿色便携）；
若该目录不可写（例如放在 `Program Files`），会自动回退到 `%APPDATA%\GitDesk`。

| 文件 | 内容 |
| --- | --- |
| `config.json` | 平台令牌（密文）、克隆目录、检测间隔、提交身份等 |
| `repos.json` | 已纳入管理的本地仓库清单 |
| `logs/gitdesk-YYYY-MM-DD.log` | 按天滚动的操作日志（单文件 2MB，保留 5 份） |

也可通过环境变量 `GITDESK_DATA_DIR` 指定数据目录。

---

## 七、技术选型说明

- **GUI**：PySide6（Qt for Python）6.x —— 原生 Windows 观感、高 DPI 支持完善。
- **Git**：dulwich 1.x —— 纯 Python 实现，无需系统 Git；`merge3` 提供三方合并。
- **HTTP**：requests —— GitHub REST v3 / Gitee OpenAPI v5。
- **翻译**：Google 免密翻译接口 + MyMemory 双源回退，`concurrent.futures` 并发，结果落本地缓存。
- **榜单**：GitHub Trending 页面解析（正则，含当期新增星标）+ 搜索 API（`stars:>1000` 倒序）。
- **瀑布流**：Qt 没有 masonry 布局，`ui/masonry.py` 用「最短列优先」算法手工排布，
  卡片按当前列宽重算简介行高以保证高度可精确预测。
- **打包**：PyInstaller onefile + windowed，体积约 50MB（已排除无用的 Qt 模块）。

> 说明：dulwich 与 CPython 自带 Git 的差异在于不依赖外部二进制，
> 因此 `GitDesk.exe` 可在未安装 Git 的 Windows 11 机器上直接使用核心功能。

### 克隆中止的实现要点

- `CancelToken`（`threading.Event`）由界面线程置位、工作线程读取；
- 取消检查同时挂在 dulwich 的两条回调上：`errstream.write`（服务端进度）与
  `report_activity`（传输层每次收发包），任一触发即抛出内部中断异常打断阻塞中的克隆；
  这样即使在「已发请求、等服务端响应」的网络等待期也能尽快生效；
- 中断后清理未完成的目标目录，并将 `CancelledError`（`GitError` 子类）交由界面统一处理；
- 顺带解决了两个体验问题：克隆进度改为服务端真实进度文本（原先只有「read/write」噪声），
  长时间无进度时回退为「已下载 xx MB」的字节计数。

### 网络中断重试的实现要点

- `is_transient_error()` 沿异常链（`__cause__` / `__context__` / `args` 中的嵌套异常）判断是否为
  瞬时网络故障：`ConnectionResetError(10054)`、`ConnectionAbortedError(10053)`、
  `socket.timeout`、`ssl.SSLEOFError` 等，并兼容 dulwich 把原始异常压成字符串的情况；
- `_retry_network()` 对 `push / fetch / pull / clone / ls-remote` 最多尝试 3 次，
  退避 1.2s / 2.4s；**认证失败、推送被拒绝等确定性错误不重试**，避免无意义地反复打扰服务端；
- 重试次数由监视器通过进度回调上报，界面在详情面板中逐条记录「网络中断，X 秒后自动重试」。

### 传输详情面板的实现要点

- `TransferMonitor` 同时充当 dulwich 的 `errstream` 与 `report_activity`：
  前者解析服务端进度文本（阶段 / 对象计数 / 百分比 / 速度），后者累计已收/已发字节；
- 结构化结果 `TransferStats.as_dict()` 经 `Task.detailed` 信号投递到界面线程（避免跨线程操作控件），
  由 `ui/widgets.py::TransferPanel` 渲染为「阶段 + 进度条 + 摘要 + 可展开明细 + 过程日志」；
- 下载（克隆/获取/拉取）与上传（推送/发布）共用同一个面板与同一套数据契约；
- 结束时的进度条配色由动态属性 `state`（`""` / `error` / `cancel`）决定：成功蓝、失败红、已停止灰，
  避免「明明失败却显示满格蓝色」造成误解。

### 上传可停止（推送 / 发布）

- `TransferPanel` 在 `begin(cancellable=True)` 时显示「停止」按钮并发出 `stop_requested`；
  页面把该信号接到正在运行的上传任务上（`Task.cancel()` → `CancelToken`）；
- 引擎侧 `GitEngine.push(..., cancel=token)` 与 `publish.publish_local(..., cancel=token)` 透传令牌：
  `TransferMonitor` 通过 `errstream` / `report_activity` 两条回调在**传输途中**检查取消，
  `_retry_network(..., cancel=...)` 还会在**每次尝试前**与**退避等待期**检查；
- 退避等待改用 `CancelToken.wait(timeout)`（`threading.Event.wait`），
  因此点「停止」不必等 1.2s/2.4s 走完，重试立即终止并抛出 `CancelledError`；
- 任务结束后按钮退化为「关闭」（`reset()` 收起面板），**失败之后依然可用**，
  不会留下一个卡住且无法清理的失败状态；
- 发布途中被停止时，提示会说明远程仓库是否已建好，便于到「远程」页重试推送。

### 页签内容不再被压扁（滚动容器）

- 现象：传输详情展开后面板约 280px，标签页区被压缩到低于概览页 `sizeHint`（实测 469 → 396），
  Qt 在「可用高度 < 布局最小高度」时只能让子控件互相重叠（按钮压住说明文字、状态行叠在一起）；
- 修复：`ui/widgets.py::scrollable()` 把表单型页签（概览）包进 `QScrollArea`
  （`setWidgetResizable(True)` + 关闭 `autoFillBackground`），空间不足时出现滚动条而不是压缩；
- 带表格/树等可自适应收缩控件的页签不需要包裹，它们自身就能优雅降级。

### 浅色卡片外观

- `QScrollArea.setWidget()` 会给子控件打开 `autoFillBackground`，深色系统主题下
  该控件会用深色的 Window 色自绘，表现为「黑底」；
- 因此瀑布流画布 / 视口都显式指定了浅色背景，并在 `ui/style.py::apply_light_theme()`
  中通过 `QStyleHints.setColorScheme(Light)` + 浅色调色板把整个应用锁定为浅色主题，
  与系统是否开启深色模式无关。

### 项目结构树

- `core/tree.py` 只做文件系统读取（纯函数，便于测试）：`list_dir` 列一层、`summarize` 递归统计、
  `filter_tree` 跨层搜索并保留祖先目录；
- 界面层按需加载（展开目录时才读取该层），因此超大仓库也不会卡顿；
- `.git` 始终隐藏，`max_entries` 兜底防止异常目录结构拖垮界面。

### 远程多选推送

- 远程列表第 0 列是 `Qt.ItemIsUserCheckable` 单元格，工具栏「全选」按钮与 `_sync_remote_selection()`
  共同维护「已勾选 N / M」提示；渲染期间用 `_remote_rendering` 屏蔽 `itemChanged` 回写，避免自触发；
- 勾选状态按「仓库 + 远程名」缓存（`_remote_checked` / `_remote_path`）：同仓库刷新保留用户选择，
  切换仓库则回到默认全选；
- 推送在界面层按勾选顺序循环调用 `GitEngine.push`，每个远程用 `_guess_platform(url)` 独立匹配
  平台与令牌；缺令牌时**在开始前**统一提示，不会推到一半才失败；
- 多选推送**不擅自改写上游**：已有上游且本次推送到它 → 保持跟踪；从未建立上游且只推一个远程 → 建立跟踪；
- 中途失败抛出带上下文的 `GitError`（「已成功推送到 origin；推送到 github 失败：…」），
  `CancelledError` 原样透传以便「停止」按钮继续生效。

### 同步目标：以「跟踪远程」为准

- `SyncState.remote_name`（由 `upstream_remote_name()` 从 `refs/remotes/<name>/<branch>` 解析）
  暴露出当前分支的上游远程；
- 概览徽标由「与远程一致」改为点名远程（「与 github 一致 / 领先 github 2 / 落后 origin 3」），
  上游不是远程分支时回退为「与上游一致」；
- 获取 / 拉取 / 推送统一走 `_sync_remote()`：**优先跟踪远程 → origin → 第一个远程**，
  与徽标同源；`_credential()` 也随之取该远程对应平台的令牌；
- `do_push()` 仅在目标远程与当前上游不同时才 `set_upstream=True`，不再把上游改写成 origin；
- 概览提示文字（`_hint_text()`）会写明这组按钮当前作用于哪个远程，
  「远程上有新提交」的提醒也带上远程名。

### 提交演化图（泳道图）

- **数据侧必须用拓扑序**：`GitEngine.graph()` 走 `get_walker(..., order=ORDER_TOPO)`。
  dulwich 的默认 `order="date"` 在 `commit_time` 相同时（脚本里连续提交、导入的历史仓库很常见）
  **不保证「子先于父」**，会让父提交排到子提交上方、连线整体倒挂 —— 这是本项目实测踩到的坑。
- dulwich 是**先按 `max_entries` 截断、再做拓扑重排**，同秒提交多时窗口可能不含 HEAD；
  因此加了**有界扩窗**：窗口里没有 HEAD 就 ×4 重试（最多 4 次、上限 5120），命中或历史耗尽即停。
- `core/graph.py::layout_graph()` 再补一层**稳定拓扑校正**（纯函数、不依赖 dulwich）：
  快路径检测输入已有序则原样返回；需校正时用 Kahn，候选按 `timestamp` 降序 → 原下标升序出队。
  只约束「列表内存在」的父子关系，被截断/浅克隆掉的父按根处理，不丢行、不误判环。
- 泳道分配：`active` 数组记录每条泳道在等待的 sha；
  `parents[0]` 继承本泳道，`parents[1:]` **先查是否已有别的泳道在等这个父**
  （命中则连一条 `merge` 虚线、不新占泳道，否则才 `_first_free/_grow` 开新泳道）。
  共享祖先被多条泳道认领时**保留最左泳道作主线**（`survivor = min(...)`），避免主线被拉到右边。
- 两条不变量（写进了测试，后续改泳道规则时兜底）：
  1. 任何 connector 的 `from`/`to` 都不得超过「真正出现过圆点的最大泳道」——不许有指向空泳道的悬空线；
  2. 「从本行圆点出发、落在下一行圆点」的连线，下一行提交必须是本行的父提交 —— 不许误指。
- 渲染：`ui/graphview.py` 用 `QPainter` 自绘（不用 QGraphicsView），
  **背景显式填白**（`WA_OpaquePaintEvent` + 每次 `paintEvent` 整块刷白），
  否则深色系统主题下会露出深色调色板 —— 与瀑布流黑底是同一个坑；
  先画 connectors 再画圆点，保证线不会盖住圆点。

### 分支合并

- `GitEngine.merge_branch(path, source, *, no_ff, message, author, email)`：
  `_resolve_merge_source()` 把 `feature/x`、`origin/main`、`refs/...` 三种写法统一解析成 ref
  （本地同名分支优先），因此本地分支与远程跟踪分支都能合并；
- 合并语义对齐 git：`merge_base` 判定 —— base==source 时返回 `already_merged`；
  base==local 时快进（更新 ref + `reset --hard`）；否则走真合并生成双父提交；
- 冲突处理复用拉取那套：先 `merge_tree()` 只读预检（不动工作区/索引），有冲突直接抛错；
  真合并抛异常或返回冲突时用 `_abort_merge()` 回滚到操作前 HEAD；
- 合并前会拦三种前置问题：游离 HEAD、存在 `MERGE_HEAD`（上次合并未完成）、工作区有未提交改动；
- UI 侧 `_selected_branch_name()` 按焦点在本地/远程表之间取选中项，
  `_on_merge_done()` 根据 `result` 给出不同文案（快进/合并提交/已包含/无需操作）。

### 冲突解决窗口

- `start_merge()` 与 `merge_branch()` 的区别：前者**不预检阻止**，让 dulwich 真正把冲突写进
  工作区（带 `<<<<<<< ours / ======= / >>>>>>> theirs` 标记）与索引，并留下 `MERGE_HEAD`；
  后者仍是「有冲突就中止并回滚」的保守路径。
- 调用 `porcelain.merge` 前先 `reset --hard`：dulwich 的 `update_working_tree` 会比对索引 stat，
  索引不新鲜时会把干净工作区误判成「有本地改动」（`WorkingTreeModifiedError`）。
- 冲突条目读取要兼容两种形态：dulwich 1.2.x 用 `ConflictedIndexEntry`（ancestor/this/other
  放在单个 `(path, 0)` 条目里），旧格式/git CLI 是 `(path, 1/2/3)`。`Index.items()` 的键
  可能是 `bytes` 也可能是元组，取值两种都试。
- `finish_merge()` 自己构造 `Commit`（`index.commit(store)` 生成 tree + 双父 + **整数**时间戳
  —— float 会写出畸形提交导致后续解析失败），再更新 `refs/HEAD` 并清理 `MERGE_HEAD/MERGE_MSG`。
- `abort_merge()` 复用 `_abort_merge()`；后者现在会**先删掉「索引里有、目标提交里没有」的文件**，
  否则合并带进来的新文件会在中止后残留为未跟踪文件（`status().clean` 为 False）。
- 窗口 `ui/merge_dialog.py`：左侧冲突列表 + 右侧 `QPlainTextEdit`，
  用 `QTextEdit.ExtraSelection` 给冲突块着色（我方 `#eaf1fd` / 对方 `#e6f4ea` / 标记 `#fff3cd`）。
  **选中的 cursor 必须 `select(LineUnderCursor)`**：零宽光标不会渲染背景色（实测踩过）。
- 关闭窗口不中止合并，保留现场；下次点「合并到当前分支」检测到 `MERGE_HEAD` 就直接重新打开窗口。
