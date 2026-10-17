"""提交演化图（泳道图）的布局算法。

本模块是**纯函数**实现，不依赖 Qt：把「按时间倒序排列的提交列表」转换成
带泳道坐标、可直接自绘的绘制行（:class:`GraphRow`），供
:class:`gitdesk.ui.graphview.CommitGraphView` 使用。

设计要点
--------
* 采用经典的「活跃泳道（active lanes）」算法：``active[i]`` 表示第 ``i`` 条泳道
  正在等待出现的提交 sha，``None`` 表示该泳道空闲。
* 当前提交若已被某条泳道等待，就落在该泳道；否则复用一个空闲泳道。
* 第一个父提交继承当前泳道，其余父提交各占一条空闲泳道（分叉）。
* 多条泳道同时等待同一个提交时（合并汇入点），只有最左侧泳道保留圆点，
  其余泳道汇入该泳道并被释放。
* ``connectors`` 描述「本行圆点 → 下一行圆点」之间的连线；``kind="merge"``
  （虚线）出现在四种汇入场景：本行释放的泳道并入主线、本行父提交已被更左侧
  泳道等待（本行收拢过去）、其它父提交已被现有泳道等待（criss-cross 直接连
  过去）、下一行是多个泳道的共同汇入点。所有连线都从**本行圆点落到下一行
  圆点**，因此渲染方向与真实历史一致，且保证每个父提交都可沿线到达。

输出不依赖字典键的顺序，同一输入必然得到完全相同的输出。

输入若已满足「子提交先于父提交」，布局结果与输入顺序完全一致；若检测到父子
倒挂（例如同一秒内的多个提交、时钟回拨、或上游数据源按时间排序），会先做一次
**稳定拓扑校正**再布局，保证父提交永远画在子提交下方。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 连线类型：同泳道直下
KIND_STRAIGHT = "straight"
#: 连线类型：父提交换了泳道（分叉 / 换道）
KIND_BRANCH = "branch"
#: 连线类型：多余泳道汇入（虚线绘制）
KIND_MERGE = "merge"

#: 泳道数量下限（即使历史很短也留出呼吸感）
MIN_LANES = 2

#: 连线字典的结构别名：``{"from": int, "to": int, "kind": str}``
Connector = dict


@dataclass
class GraphRow:
    """提交演化图中的一行（一个提交及其泳道坐标、连线）。

    Attributes:
        sha: 完整提交 id。
        short: 短提交 id（默认取前 8 位）。
        subject: 提交说明首行。
        author: 作者名（不含邮箱）。
        date: 已格式化的提交时间。
        lane: 本行圆点所在泳道，从 0 开始。
        parents: 父提交 sha 列表（顺序与 Git 中一致）。
        refs: 指向该提交的短引用名（如 ``main`` / ``origin/main`` / ``v1`` / ``HEAD``）。
        connectors: 本行圆点到下一行圆点的连线，
            每项形如 ``{"from": int, "to": int, "kind": str}``。
        is_merge: 是否为合并提交（父提交多于一个）。
        is_head: 该提交是否被 ``HEAD`` 指向。
    """

    sha: str = ""
    short: str = ""
    subject: str = ""
    author: str = ""
    date: str = ""
    lane: int = 0
    parents: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    connectors: list[Connector] = field(default_factory=list)
    is_merge: bool = False
    is_head: bool = False


def layout_graph(commits: list[dict], *, max_lanes: int = 8) -> list[GraphRow]:
    """把提交列表布局成泳道图的行序列。

    Args:
        commits: 按「拓扑 + 时间倒序」（新 → 旧，且子提交先于父提交）排列的
            提交字典列表。每项可含 ``sha`` / ``short`` / ``subject`` / ``author`` /
            ``date`` / ``timestamp`` / ``parents`` / ``refs``，缺失字段使用空值兜底。
            若检测到父子倒挂，函数内部会先做稳定拓扑校正（见 :func:`_topo_order`）。
        max_lanes: 泳道数量上限；超出时复用最右侧泳道，保证图形不会被拉得过宽。

    Returns:
        与输入等长（重复 sha 会被去重）的 :class:`GraphRow` 列表。

    Raises:
        TypeError: 当 ``commits`` 不是可迭代对象时。
    """
    items = _normalize(commits)
    if not items:
        return []
    items = _topo_order(items)
    limit = max(MIN_LANES, int(max_lanes or MIN_LANES))

    lanes, carry, forks, joins, links, snapshots = _assign_lanes(items, limit)
    rows: list[GraphRow] = []
    total = len(items)
    for index, item in enumerate(items):
        lane = lanes[index]
        connectors: list[Connector] = []
        if index + 1 < total:
            next_lane = lanes[index + 1]
            next_sha = items[index + 1]["sha"]
            connectors = _build_connectors(
                lane=lane,
                next_lane=next_lane,
                next_sha=next_sha,
                carry_lane=carry[index],
                fork_lanes=forks[index],
                join_lanes=joins[index],
                link_lanes=links[index],
                snapshot=snapshots[index],
            )
        rows.append(
            GraphRow(
                sha=item["sha"],
                short=item["short"],
                subject=item["subject"],
                author=item["author"],
                date=item["date"],
                lane=lane,
                parents=list(item["parents"]),
                refs=list(item["refs"]),
                connectors=connectors,
                is_merge=len(item["parents"]) > 1,
                is_head="HEAD" in item["refs"],
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 第零遍：拓扑校正
# --------------------------------------------------------------------------- #


def _topo_order(items: list[dict]) -> list[dict]:
    """把「父提交排在子提交之前」的输入校正为「子提交先于父提交」。

    只使用**列表内存在**的父子关系：被 ``limit`` 截断掉的父提交视为根提交，
    不参与约束（否则会把整段历史误判成有环）。

    行为约定：

    * 输入已经满足拓扑序时**原样返回**（不改变调用方给的顺序，零成本）；
    * 需要校正时使用稳定 Kahn 算法，候选提交按 ``timestamp`` 降序、
      再按原输入下标升序出队，因此结果确定且尽量贴近原顺序；
    * 异常输入（如自引用造成的环）不会丢数据，剩余提交按原序追加在末尾。

    Args:
        items: 归一化后的提交字典列表（含 ``sha`` / ``parents`` / ``timestamp``）。

    Returns:
        拓扑校正后的新列表（元素仍是原来的字典对象）。
    """
    count = len(items)
    if count <= 1:
        return items
    index_of = {item["sha"]: index for index, item in enumerate(items)}
    # parents[i]：第 i 个提交在列表内的父提交下标（去重、去自引用）
    parents: list[list[int]] = []
    for index, item in enumerate(items):
        found: list[int] = []
        for parent in item["parents"]:
            target = index_of.get(parent)
            if target is not None and target != index and target not in found:
                found.append(target)
        parents.append(found)

    already_ordered = True
    for index, targets in enumerate(parents):
        if any(target < index for target in targets):
            already_ordered = False
            break
    if already_ordered:
        return items  # 已拓扑有序，保持原序

    # 子 → 父的约束：indegree[父] 记录「还有多少个子提交没输出」
    indegree = [0] * count
    children: list[list[int]] = [[] for _ in range(count)]
    for index in range(count):
        for target in parents[index]:
            indegree[target] += 1
            children[index].append(target)

    heap = [(-_timestamp(items[index]), index) for index in range(count) if indegree[index] == 0]
    heapq.heapify(heap)
    ordered: list[dict] = []
    emitted = [False] * count
    while heap:
        _neg_time, index = heapq.heappop(heap)
        ordered.append(items[index])
        emitted[index] = True
        for target in children[index]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(heap, (-_timestamp(items[target]), target))

    if len(ordered) < count:  # 理论上 git 历史无环，这里只做兜底
        ordered.extend(items[index] for index in range(count) if not emitted[index])
    return ordered


def _timestamp(item: dict) -> int:
    """取提交时间戳（缺失或非法时按 0 处理），用于拓扑校正的出队优先级。"""
    try:
        return int(item.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0

# --------------------------------------------------------------------------- #
# 第一遍：分配泳道
# --------------------------------------------------------------------------- #


def _assign_lanes(
    items: list[dict], max_lanes: int
) -> tuple[list[int], list[int | None], list[list[int]], list[list[int]], list[list[str | None]]]:
    """为每个提交分配本行泳道，并记录其后各父提交占用的泳道。

    同一条父提交若已被多条泳道等待，保留**最左侧**的泳道作为主线（这样主线
    不会因为共享祖先先被分支泳道认领而向右换道），其余泳道释放并记入 ``joins``，
    由连线在祖先圆点处补一段汇入曲线。

    Returns:
        ``(lanes, carry, forks, joins, links, snapshots)``：

        * ``lanes``：每行的圆点泳道；
        * ``carry``：每行第一个父提交所在泳道（无父提交时为 ``None``）；
        * ``forks``：每行除第一个父提交外，其它父提交**新占用**的泳道；
        * ``joins``：本行释放、并在后续圆点处汇入主线的泳道；
        * ``links``：其它父提交已被现有泳道等待时，直接连过去的泳道（criss-cross）；
        * ``snapshots``：处理完该行之后的泳道占用快照。
    """
    active: list[str | None] = []
    lanes: list[int] = []
    carry: list[int | None] = []
    forks: list[list[int]] = []
    joins: list[list[int]] = []
    links: list[list[int]] = []
    snapshots: list[list[str | None]] = []
    # 被 limit 截断 / 浅克隆时，父提交可能不在列表里：这类父提交当作根处理，
    # 不占泳道、不画线，避免出现「弯向空白泳道」的悬空连线
    known = {item["sha"] for item in items}

    for item in items:
        sha = item["sha"]
        incoming = [i for i, waiting in enumerate(active) if waiting == sha]
        if incoming:
            lane = incoming[0]
        else:
            lane = _first_free(active)
            if lane is None:
                lane = _grow(active, max_lanes)
        # 其余泳道也等待同一个提交：它们已经汇入 lane，这里释放即可
        for index in incoming[1:]:
            active[index] = None

        parents: list[str] = item["parents"]
        first = parents[0] if parents else None
        first_known = first is not None and first in known
        fork_lanes: list[int] = []
        join_lanes: list[int] = []
        link_lanes: list[int] = []
        carry_lane: int | None = None
        if first_known:
            existing = [
                i
                for i, waiting in enumerate(active)
                if waiting == first and i != lane
            ]
            # 父提交可能已经被别的泳道等待：保留**最左侧**的泳道作为主线，
            # 其余泳道释放并（在下一行圆点处）汇入主线，避免主线来回换道
            survivor = min([lane, *existing])
            for index in [lane, *existing]:
                if index == survivor:
                    continue
                if index != lane:
                    join_lanes.append(index)
                active[index] = None
            active[survivor] = first
            carry_lane = survivor
            for parent in parents[1:]:
                if parent not in known:
                    continue  # 父提交不在输出范围内，画线只会指向空白泳道
                # 其它父提交同样可能已有泳道在等待（criss-cross 互为父提交）：
                # 这种情况下不能再新占泳道，否则该父提交到达时会落在最左侧泳道，
                # 先前画出的分叉线就会指向一条空泳道，父子路径断裂。
                waiting = [i for i, value in enumerate(active) if value == parent]
                if waiting:
                    link_lanes.append(waiting[0])
                    continue
                slot = _first_free(active)
                if slot is None:
                    slot = _grow(active, max_lanes)
                active[slot] = parent
                fork_lanes.append(slot)
        else:
            # 根提交 / 父提交不在本次输出范围内：本泳道到此结束
            active[lane] = None

        lanes.append(lane)
        carry.append(carry_lane)
        forks.append(fork_lanes)
        joins.append(join_lanes)
        links.append(link_lanes)
        snapshots.append(list(active))

    return lanes, carry, forks, joins, links, snapshots


def _first_free(active: list[str | None]) -> int | None:
    """返回第一条空闲泳道的下标；没有空闲泳道时返回 ``None``。"""
    for index, waiting in enumerate(active):
        if waiting is None:
            return index
    return None


def _grow(active: list[str | None], max_lanes: int) -> int:
    """新增一条泳道；已达上限时复用最右侧泳道并清空它。"""
    if len(active) < max_lanes:
        active.append(None)
        return len(active) - 1
    index = max(0, len(active) - 1)
    active[index] = None
    return index


# --------------------------------------------------------------------------- #
# 第二遍：生成连线
# --------------------------------------------------------------------------- #


def _build_connectors(
    *,
    lane: int,
    next_lane: int,
    next_sha: str,
    carry_lane: int | None,
    fork_lanes: list[int],
    join_lanes: list[int],
    link_lanes: list[int],
    snapshot: list[str | None],
) -> list[Connector]:
    """生成「本行 → 下一行」的连线集合（顺序稳定）。

    Args:
        lane: 本行圆点泳道。
        next_lane: 下一行圆点泳道。
        next_sha: 下一行提交的 sha（用于识别汇入）。
        carry_lane: 本行第一个父提交所在泳道，``None`` 表示本行没有父提交。
        fork_lanes: 本行其它父提交新占用的泳道。
        join_lanes: 本行释放、需要汇入 ``carry_lane`` 的泳道。
        link_lanes: 本行其它父提交已被现有泳道等待时，直接连过去的泳道。
        snapshot: 处理完本行之后的泳道占用情况。
    """
    connectors: list[Connector] = []

    # 本行结束后，哪些泳道会在下一行被「汇入」并释放
    released_next = {
        index
        for index, waiting in enumerate(snapshot)
        if waiting == next_sha and index != next_lane
    }

    # 1) 主线：第一个父提交（同泳道直下；父提交已在别的泳道等待时，本泳道汇入它）
    #    若本泳道本身会在下一行汇入别处，则交给下面的「汇入」分支处理，避免重复画线
    if carry_lane is not None and not (carry_lane == lane and lane in released_next):
        kind = KIND_STRAIGHT if carry_lane == lane else KIND_MERGE
        connectors.append({"from": lane, "to": carry_lane, "kind": kind})

    # 2) 分叉：其余父提交各引出一条线
    for slot in fork_lanes:
        connectors.append({"from": lane, "to": slot, "kind": KIND_BRANCH})

    # 2.1) 其它父提交已有泳道在等待（criss-cross）：直接连过去，不再新占泳道
    for slot in link_lanes:
        if slot != lane:
            connectors.append({"from": lane, "to": slot, "kind": KIND_MERGE})

    # 3) 汇入主线：本行释放的泳道在下一行圆点处并入主线（保留左侧主线不换道）
    main_lane = carry_lane if carry_lane is not None else lane
    for slot in join_lanes:
        if slot != main_lane:
            connectors.append({"from": slot, "to": main_lane, "kind": KIND_MERGE})

    # 4) 贯穿本行但不参与分叉 / 汇入的泳道：上下贯通
    for index, waiting in enumerate(snapshot):
        if index == lane or waiting is None:
            continue
        if index in fork_lanes or index in join_lanes:
            continue
        if waiting == next_sha and index != next_lane:
            continue  # 其它泳道会在下一行收拢，交给下面的「汇入」处理
        connectors.append({"from": index, "to": index, "kind": KIND_STRAIGHT})

    # 5) 汇入：其它等待下一行提交的泳道收拢到下一行圆点
    for index, waiting in enumerate(snapshot):
        if index != next_lane and waiting == next_sha:
            connectors.append({"from": index, "to": next_lane, "kind": KIND_MERGE})

    # 去重（泳道数受限时可能出现同一对泳道的重复连线）后稳定排序：
    # 不依赖字典插入顺序，同一输入必得同一输出
    unique: dict[tuple[int, int, str], Connector] = {}
    for connector in connectors:
        key = (int(connector["from"]), int(connector["to"]), str(connector["kind"]))
        unique.setdefault(key, connector)
    return [unique[key] for key in sorted(unique)]


# --------------------------------------------------------------------------- #
# 输入归一化
# --------------------------------------------------------------------------- #


def _normalize(commits: Iterable[dict] | None) -> list[dict]:
    """把外部提交字典归一化为布局算法内部使用的结构（按 sha 去重）。"""
    if commits is None:
        return []
    if isinstance(commits, (str, bytes)):
        raise TypeError("commits 必须是提交字典组成的序列")
    result: list[dict] = []
    seen: set[str] = set()
    for raw in commits:
        if not isinstance(raw, dict):
            continue
        sha = _text(raw.get("sha") or raw.get("id") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        parents = [_text(parent) for parent in (raw.get("parents") or []) if parent]
        refs = [_text(ref) for ref in (raw.get("refs") or []) if ref]
        result.append(
            {
                "sha": sha,
                "short": _text(raw.get("short") or sha[:8]),
                "subject": _text(raw.get("subject") or ""),
                "author": _text(raw.get("author") or ""),
                "date": _text(raw.get("date") or ""),
                "timestamp": _timestamp(raw),
                "parents": parents,
                "refs": refs,
            }
        )
    return result


def _text(value: Any) -> str:
    """把任意值安全地转成字符串（bytes 按 UTF-8 解码）。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
