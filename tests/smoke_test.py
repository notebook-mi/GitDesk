"""GitDesk 功能冒烟测试。

用本机 git 仅作为测试夹具（创建裸仓库/产生提交），被测对象是 GitDesk 自身的
dulwich 引擎与界面层，确保在没有 GIT 环境时同样可用。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORK = tempfile.mkdtemp(prefix="gitdesk-test-")
os.environ["GITDESK_DATA_DIR"] = os.path.join(WORK, "appdata")

from gitdesk.core.gitengine import Credential, GitEngine, GitError  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} {extra}")


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_remote() -> str:
    """创建裸仓库并填充首个提交。"""
    remote = os.path.join(WORK, "remote.git")
    git("init", "--bare", "--initial-branch=main", remote)
    seed = os.path.join(WORK, "seed")
    git("clone", remote, seed)
    git("config", "user.name", "Tester", cwd=seed)
    git("config", "user.email", "tester@example.com", cwd=seed)
    write(os.path.join(seed, "README.md"), "# Demo\n\nhello\n")
    write(os.path.join(seed, "src/app.py"), "print('v1')\n")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "初始提交", cwd=seed)
    git("push", "origin", "main", cwd=seed)
    return remote


def main() -> int:
    print(f"测试工作目录: {WORK}")
    remote = make_remote()

    # ---------------------------------------------------------------- #
    section("克隆")
    clone_dir = os.path.join(WORK, "clone1")
    GitEngine.clone(remote, clone_dir)
    check("克隆后目录存在", os.path.isdir(clone_dir))
    check("克隆后是 Git 仓库", GitEngine.is_git_repo(clone_dir))
    files = os.listdir(clone_dir)
    check("工作区文件已检出", "README.md" in files, str(files))

    info_data = GitEngine.repo_info(clone_dir)
    check("识别当前分支为 main", info_data.get("branch") == "main", str(info_data.get("branch")))
    check("读取到远程配置", len(info_data.get("remotes", [])) >= 1)
    check("提交历史可读取", len(GitEngine.history(clone_dir)) == 1)

    # ---------------------------------------------------------------- #
    section("状态与提交")
    write(os.path.join(clone_dir, "src/app.py"), "print('v2')\n")
    write(os.path.join(clone_dir, "new.txt"), "brand new\n")
    status = GitEngine.status(clone_dir)
    check("检测到已修改文件", "src/app.py" in status["unstaged"], str(status["unstaged"]))
    check("检测到未跟踪文件", "new.txt" in status["untracked"], str(status["untracked"]))
    check("工作区标记为不干净", status["dirty"] is True)

    GitEngine.stage(clone_dir, ["src/app.py", "new.txt"])
    status = GitEngine.status(clone_dir)
    check("暂存后 staged 非空", len(status["staged"]) == 2, str(status["staged"]))
    check("暂存后 unstaged 为空", status["unstaged"] == [], str(status["unstaged"]))

    diff = GitEngine.diff_text(clone_dir, file_path="src/app.py", staged=True)
    check("暂存差异包含新增行", "+print('v2')" in diff, diff[:200])

    GitEngine.unstage(clone_dir, ["new.txt"])
    status = GitEngine.status(clone_dir)
    check("取消暂存生效", "new.txt" in status["untracked"], str(status["untracked"]))
    os.remove(os.path.join(clone_dir, "new.txt"))

    GitEngine.stage(clone_dir, ["src/app.py"])
    sha = GitEngine.commit(clone_dir, "更新版本输出", "Tester", "tester@example.com")
    check("提交返回 SHA", len(sha) == 40, sha)
    history = GitEngine.history(clone_dir)
    check("历史记录增加", len(history) == 2, str(len(history)))
    check("最新提交说明正确", history[0]["subject"] == "更新版本输出", history[0]["subject"])

    detail = GitEngine.commit_detail(clone_dir, sha)
    check("提交详情含文件清单", any(f["path"] == "src/app.py" for f in detail["files"]), str(detail["files"]))

    # ---------------------------------------------------------------- #
    section("暂存区（stash）")
    write(os.path.join(clone_dir, "scratch.txt"), "temp\n")
    GitEngine.stage(clone_dir, ["scratch.txt"])
    status = GitEngine.status(clone_dir)
    check("stash 前存在暂存内容", len(status["staged"]) == 1)
    GitEngine.stash_push(clone_dir)
    status = GitEngine.status(clone_dir)
    check("stash 后工作区干净", status["clean"] is True, str(status))
    check("stash 列表非空", len(GitEngine.stash_list(clone_dir)) >= 1)
    GitEngine.stash_pop(clone_dir)
    status = GitEngine.status(clone_dir)
    check("stash 恢复成功", len(status["staged"]) == 1, str(status))
    GitEngine.discard(clone_dir, [], ["scratch.txt"])
    GitEngine.unstage(clone_dir, ["scratch.txt"])

    # ---------------------------------------------------------------- #
    section("分支管理")
    GitEngine.create_branch(clone_dir, "feature/demo")
    check("新建分支并切换", GitEngine.status(clone_dir)["branch"] == "feature/demo")
    branches = GitEngine.branches(clone_dir)
    names = [b["name"] for b in branches["local"]]
    check("分支列表含新分支", "feature/demo" in names, str(names))
    GitEngine.checkout(clone_dir, "main")
    check("切换回 main", GitEngine.status(clone_dir)["branch"] == "main")
    GitEngine.delete_branch(clone_dir, "feature/demo")
    names = [b["name"] for b in GitEngine.branches(clone_dir)["local"]]
    check("删除分支成功", "feature/demo" not in names, str(names))

    # ---------------------------------------------------------------- #
    section("推送")
    GitEngine.push(clone_dir, "origin", "main", Credential(), "github")
    log_output = git("log", "--oneline", "-1", "main", cwd=remote).stdout.strip()
    check("裸仓库收到新提交", "更新版本输出" in log_output, log_output)

    # ---------------------------------------------------------------- #
    section("拉取（快进）")
    other = os.path.join(WORK, "clone2")
    git("clone", remote, other)
    git("config", "user.name", "Other", cwd=other)
    git("config", "user.email", "other@example.com", cwd=other)
    write(os.path.join(other, "docs/guide.md"), "# Guide\n")
    git("add", "-A", cwd=other)
    git("commit", "-m", "补充文档", cwd=other)
    git("push", "origin", "main", cwd=other)

    GitEngine.fetch(clone_dir, "origin", Credential(), "github")
    pre = GitEngine.status(clone_dir)
    check("拉取前工作区干净", pre["clean"], str(pre))
    state = GitEngine.sync_state(clone_dir)
    check("fetch 后识别落后 1 个提交", state.behind == 1 and state.ahead == 0, str(state))
    result = GitEngine.pull(clone_dir, "origin", Credential(), "github")
    check("快进拉取成功", result["result"] == "fast_forward", str(result))
    check("拉取后文件已更新", os.path.isfile(os.path.join(clone_dir, "docs/guide.md")))
    state = GitEngine.sync_state(clone_dir)
    check("拉取后与远程一致", state.ahead == 0 and state.behind == 0, str(state))

    # ---------------------------------------------------------------- #
    section("冲突保护")
    # 制造分叉：本地与远程同时修改同一文件
    write(os.path.join(clone_dir, "README.md"), "# Demo\n\nlocal change\n")
    GitEngine.stage(clone_dir, ["README.md"])
    GitEngine.commit(clone_dir, "本地修改说明", "Tester", "tester@example.com")

    write(os.path.join(other, "README.md"), "# Demo\n\nremote change\n")
    git("add", "-A", cwd=other)
    git("commit", "-m", "远程修改说明", cwd=other)
    git("push", "origin", "main", cwd=other)

    GitEngine.fetch(clone_dir, "origin", Credential(), "github")
    state = GitEngine.sync_state(clone_dir)
    check("识别到分支分叉", state.diverged, str(state))

    local_readme_before = open(os.path.join(clone_dir, "README.md"), encoding="utf-8").read()
    caught = None
    try:
        GitEngine.pull(clone_dir, "origin", Credential(), "github", strategy="auto")
    except GitError as exc:
        caught = exc
    check("冲突时抛出可读错误", caught is not None and "冲突" in str(caught), str(caught))
    local_readme_after = open(os.path.join(clone_dir, "README.md"), encoding="utf-8").read()
    check(
        "冲突后本地文件未被破坏",
        local_readme_after == local_readme_before,
        f"{local_readme_before!r} != {local_readme_after!r}",
    )
    check("冲突回滚后本地提交仍在", GitEngine.history(clone_dir)[0]["subject"] == "本地修改说明")

    # ---------------------------------------------------------------- #
    section("脏工作区拦截")
    write(os.path.join(clone_dir, "src/app.py"), "print('uncommitted')\n")
    caught = None
    try:
        GitEngine.pull(clone_dir, "origin", Credential(), "github")
    except GitError as exc:
        caught = exc
    check("未提交更改时阻止拉取", caught is not None and "未提交" in str(caught), str(caught))
    check(
        "拦截后内容未被覆盖",
        open(os.path.join(clone_dir, "src/app.py"), encoding="utf-8").read() == "print('uncommitted')\n",
    )
    GitEngine.discard(clone_dir, ["src/app.py"], [])

    # ---------------------------------------------------------------- #
    section("远程管理")
    GitEngine.add_remote(clone_dir, "upstream", remote)
    remotes = {r["name"]: r["url"] for r in GitEngine.remotes(clone_dir)}
    check("添加远程成功", "upstream" in remotes, str(remotes))
    GitEngine.set_remote_url(clone_dir, "upstream", remote + "_x")
    remotes = {r["name"]: r["url"] for r in GitEngine.remotes(clone_dir)}
    check("修改远程地址成功", remotes["upstream"].endswith("_x"), str(remotes))
    GitEngine.remove_remote(clone_dir, "upstream")
    remotes = {r["name"]: r["url"] for r in GitEngine.remotes(clone_dir)}
    check("删除远程成功", "upstream" not in remotes, str(remotes))

    # ---------------------------------------------------------------- #
    section("错误处理")
    try:
        GitEngine.status(WORK)
        check("非仓库目录应报错", False, "未抛出异常")
    except GitError as exc:
        check("非仓库目录给出友好提示", "Git 仓库" in str(exc), str(exc))

    try:
        GitEngine.clone("https://github.com/definitely/not-exist-xyz.git", os.path.join(WORK, "bad"))
        check("无效地址克隆应报错", False, "未抛出异常")
    except GitError as exc:
        check("无效地址克隆给出友好提示", bool(str(exc)), str(exc))

    # ---------------------------------------------------------------- #
    section("配置与加密")
    from gitdesk.core.config import Config, RepoStore

    cfg = Config()
    cfg.save_account("github", token="ghp_test_token_123", username="tester", verified=True)
    check("令牌不以明文落盘", "ghp_test_token_123" not in cfg.path.read_text(encoding="utf-8"))
    check("令牌可正确解密", cfg.token("github") == "ghp_test_token_123", cfg.token("github"))
    check("连接状态识别", cfg.is_connected("github"))
    store = RepoStore()
    store.add(clone_dir, platform="github")
    check("仓库清单可读写", store.get(clone_dir) is not None)
    store.remove(clone_dir)
    check("仓库清单可移除", store.get(clone_dir) is None)

    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 2
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  ✗", item)
    print(f"测试目录: {WORK}")
    sys.exit(0 if not FAIL else 1)
