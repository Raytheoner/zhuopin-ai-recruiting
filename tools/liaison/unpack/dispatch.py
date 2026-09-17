"""并发守卫 ＋ 非阻塞起活（design D4/D12，spec「并发守卫按 pid 判活」「拆件会话
以受限权限启动」「起活失败只审计不上抛」）。

⛔ 本模块 ⛔ 不 import `tools.liaison.storage.db`（spec「子命令不碰库」，Task 6
的 AST 测试守着整条 `unpack-dispatch` 子命令的 import 面，含本模块）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tools.liaison.unpack.dispatch")

#: 环境变量名。⛔ 只在这里写死一次——`.env.example`（Task 7）与
#: `build_headless_argv` 的调用方都从这里 import，⛔ 不许各处重写字面量。
CLAUDE_BIN_ENV = "HR_LIAISON_CLAUDE_BIN"
BUDGET_ENV = "HR_LIAISON_UNPACK_BUDGET_USD"

#: 预算默认值（design D16，Shao Peishen 2026-09-10 答 2a）。取字符串——它只会被
#:拼进 argv，⛔ 不参与任何数值运算，存成 `str` 免得调用方还要 `str(int(...))`。
DEFAULT_BUDGET_USD = "5"


def compute_is_alive(pid: int, *, _kill: Callable[[int, int], None] = os.kill) -> bool:
    """`pid` 对应的进程是否仍在运行。**查询失败一律按不存活处理**（spec 明写）。

    `_kill` 是测试注入缝（⛔ 不是配置项）：`os.kill(pid, 0)` 不发信号只探测，
    `ProcessLookupError`（进程不存在）/`PermissionError`（进程存在但探测不到，
    如属于别的用户）/**任何其它异常**统一判「不存活」——design D12 逐字要求这三者
    同一处置，⛔ 不许把 `PermissionError` 特殊化成"存活但探测不到"。
    """
    try:
        _kill(pid, 0)
    except Exception:
        return False
    return True


def compute_is_busy(lock_text: str | None, is_alive: Callable[[int], bool]) -> bool:
    """锁文本 + 判活函数 → 是否忙。四种形状全部落在「不忙」这一侧，除了
    「锁存在、pid 合法、`is_alive` 返回 True」这一种。

    `is_alive` 是调用方注入的判活函数（生产传 `compute_is_alive`，单测传 fake）。
    ⚠️ 本函数**自己也**包一层 `try/except`——不因为不信任 `compute_is_alive`
    （它已经不会抛），而是「查询失败归不存活」是 spec 对"判忙"这整条判据的要求，
    不该只在 `compute_is_alive` 一处兜底，未来换一个判活实现时这条防线不能丢。
    """
    if not lock_text:
        return False
    try:
        payload: Any = json.loads(lock_text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        return bool(is_alive(pid))
    except Exception:
        return False


def resolve_claude_bin(env: Mapping[str, str]) -> str | None:
    """`claude` 二进制路径解析（design D12 三级顺序）。均找不到 ⇒ `None`，
    调用方（Task 4）据此转 `failed(reason=binary_not_found)`。

    ⛔ 不在这里抛异常——"找不到"是一个**正常、被 spec 预期到**的结果，抛异常
    会强迫调用方用 `try/except` 来处理一个其实只是"返回值是 None"的情形。
    """
    override = env.get(CLAUDE_BIN_ENV)
    if override and override.strip():
        return override.strip()
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.is_file():
        return str(fallback)
    return None


#: 章程 §三 允许会话写入并 `git add` 的路径（TD-48，0917O）。⛔ 必须与
#: 章程正本（`charter.CHARTER_RELATIVE_PATH`）§三 「只 `git add` 本轮明确写入的路径」
#: 逐字一致——`test_unpack_path_guard.py` 从章程正本解析出清单来核对，两边任一漂移
#: 即红。目录以 `/` 结尾，文件写全名。
CHARTER_WRITABLE_PATHS: tuple[str, ...] = (
    "docs/跟进信/回件/",
    "docs/跟进信/README-跟进信清单.md",
    "docs/跟进信/口径点台账.md",
    "docs/session接力.md",
)

#: 红线 ②④⑤⑧ 涉及的目录与文件，外加 opener 目录与 `data/`（信号只经 CLI）。
#: 对 `Edit` 显式 deny——`acceptEdits` 会自动接受项目内任何编辑，只收窄 allow 拦不住
#: （2026-09-17 一次性仓实验：写法 甲 下改 `tools/x.py` 仍放行；加 deny 后报
#: 「File is in a directory that is denied by your permission settings」）。
#: TD-50（0917Q）：⛔ 不再写 `Write(...)` 规则——CLI 逐条告警「Permission … rule:
#: Write(…) is not matched by file permission checks — only Edit(path) rules are.
#: … (Edit rules cover all file-editing tools)」，一次性仓实测无 Write 规则时
#: `Write` 越界文件同样被 `Edit(<dir>/**)` deny 拦下。
HEADLESS_DENY_EDIT_PATTERNS: tuple[str, ...] = (
    "tools/**", "app/**", "scripts/**", "tests/**", "openspec/**", ".claude/**",
    "CLAUDE.md", "data/**", ".env*", "docs/openers/**",
)

#: 整体性 git 动作，显式 deny（CLAUDE.md 并行四条 ＋ 红线 ⑦）。deny 规则优先级高于
#: allow 与权限模式，`git add -A` 实测报「Permission to use Bash with command
#: git add -A has been denied」。
HEADLESS_DENY_BASH_PREFIXES: tuple[str, ...] = (
    "git add -A", "git add .", "git commit -a", "git stash", "git push",
)

#: TD-50：`git add` 命令行里**任何位置**出现红线目录都 deny（通配 `*` 匹配含空格的
#: 任意串，见 `_git_add_deny_rules`）。放行规则只认打头的路径（`Bash(X:*)` 与
#: `Bash(<dir>*)` 都允许尾随参数），`git add docs/session接力.md tools/x.py` 会把并行
#: 泳道在 `tools/` 下的未提交改动一起卷走——这一条堵的是尾随越界。
HEADLESS_DENY_GIT_ADD_SUBSTRINGS: tuple[str, ...] = (
    "tools/", "app/", "scripts/", "tests/", "openspec/", ".claude/",
    "CLAUDE.md", "data/", ".env", "docs/openers/",
)


def _path_rule(tool: str, path: str) -> str:
    """章程路径 → 权限规则：目录 `docs/x/` ⇒ `Edit(docs/x/**)`，文件 ⇒ `Edit(docs/x.md)`。
    路径相对子进程 cwd（＝仓库根，见 `dispatch_headless_unpack` 的 `cwd=str(REPO_ROOT)`），
    实验证实相对 pattern 能匹配工具收到的绝对路径。"""
    return f"{tool}({path}**)" if path.endswith("/") else f"{tool}({path})"


def _git_add_allow_rules(path: str) -> tuple[str, ...]:
    """章程路径 → `git add` 放行规则（TD-50，2026-09-17 一次性仓实测 claude 2.1.263）。

    `Bash(X:*)` 是**词边界**前缀——只命中 `X` 本身或 `X ` ＋任意后续，`X` 后面紧跟
    非空格字符不算：`Bash(git add docs/跟进信/回件/:*)` 命中不了
    `git add docs/跟进信/回件/a.md`（TD-48 的目录规则从未生效过，首次真实回件即卡
    「This command requires approval」）。目录改用通配 `Bash(git add <dir>*)`
    （`*` 匹配任意串、含空格，故两个路径的写法也放行）；文件保留 `:*` 前缀。
    引号不做归一化、`--` 是另一个前缀，各自要一条变体——会话实测三种写法都会写。
    """
    if path.endswith("/"):
        heads = (f"{path}", f'"{path}', f"-- {path}", f'-- "{path}')
        return tuple(f"Bash(git add {h}*)" for h in heads)
    heads = (f"{path}", f'"{path}"', f"-- {path}", f'-- "{path}"')
    return tuple(f"Bash(git add {h}:*)" for h in heads)


def _git_add_deny_rules(fragment: str) -> tuple[str, ...]:
    """红线目录片段 → `git add` deny 通配规则：`Bash(git add *tools/*)` 命中
    `git add tools/x.py`、`git add docs/x.md tools/x.py`、`git add docs/x.md "tools/x.py"`。"""
    return (f"Bash(git add *{fragment}*)",)


#: 受限权限 argv 模板（design D4，TD-48 后为写法 乙，TD-50 修 `git add` 规则写法）。⛔ **唯一真源**——白名单是否
#: 放行某条命令、预算参数是否存在，全部由 `subagent-driven-development` 的 reviewer
#: 对着*这个常量*核对，⛔ 不许在别处再拼一份 argv 字面量。
HEADLESS_ARGV_FIXED_PART: tuple[str, ...] = (
    "-p",
    "--output-format", "text",
    "--permission-mode", "acceptEdits",
    "--allowedTools",
    "Read", "Glob", "Grep",
    # TD-48：`Edit` 不再裸放行，只放行章程 §三 列出的路径（`Edit(path)` 规则覆盖
    # 全部编辑工具，含 Write；TD-50 起不再另写 `Write(...)`）。
    *(_path_rule("Edit", path) for path in CHARTER_WRITABLE_PATHS),
    # TD-48/TD-50：`git add` 逐路径放行（目录通配、文件词边界前缀、各带引号与 `--`
    # 变体，见 `_git_add_allow_rules`）；`git commit` 只放行 `-m` 开头的形式。
    *(rule for path in CHARTER_WRITABLE_PATHS for rule in _git_add_allow_rules(path)),
    "Bash(git commit -m:*)", "Bash(git status:*)",
    "Bash(git diff:*)", "Bash(git log:*)",
    # I4（2026-09-16 修）：⛔ 裸 "python -m tools.liaison unpack-signal:*" 匹配不上
    # 本仓库的真实调法——`tools.liaison` 不是装进 site-packages 的包，必须
    # `PYTHONPATH=.` 才能被解析到，且必须用 `tools/liaison/.venv/bin/python`
    # （唯一装了 `tools/liaison/requirements.txt` 的解释器），不是 PATH 上随便
    # 一个 `python`。这是本仓库文档里反复出现的唯一canonical 调用形式（见
    # `docs/archive/tech-debt-已还.md:353` 等处），子进程 cwd 已固定为 REPO_ROOT
    # （见下面 `dispatch_headless_unpack` 的 `cwd=str(REPO_ROOT)`），相对路径可解析。
    # 旧的裸写法会让拆件会话在它唯一需要的自我轮询命令上被 permission-denied。
    "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal:*)",
    "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria:*)",
    "--disallowedTools",
    *(f"Edit({pattern})" for pattern in HEADLESS_DENY_EDIT_PATTERNS),
    *(f"Bash({prefix}:*)" for prefix in HEADLESS_DENY_BASH_PREFIXES),
    *(rule for fragment in HEADLESS_DENY_GIT_ADD_SUBSTRINGS for rule in _git_add_deny_rules(fragment)),
)


#: I6（2026-09-16 修）：子进程持有 `Write` + `Bash(git commit:*)` 权限，⛔ 不把父
#: 进程整份 `os.environ`（含 `HR_LIAISON_BOT_SECRET`/`HR_LIAISON_GROUP_WEBHOOK`）
#: 透传下去——凭据边界应当由这份显式白名单可审计地保证，而不是靠"权限模式凑巧
#: 没用上"这种偶然性撑着。
#: TD-47（2026-09-17 修）：补 `USER`——macOS 上 `claude` 的登录态存在系统 Keychain
#: 条目「Claude Code-credentials」里，查询该条目按 `USER` 环境变量的值做 account
#: 匹配（实测：`env -i PATH HOME PYTHONPATH` 复现 `Not logged in`；同样四键
#: 再加正确的 `USER` 值即可登录成功且只需 `PATH`+`USER` 两键；`USER` 给错值
#: 仍报未登录——证实是 account 匹配而非巧合）。`USER` 是非秘密系统变量，放行
#: 不影响凭据边界。
_CHILD_ENV_ALLOWLIST: tuple[str, ...] = (
    CLAUDE_BIN_ENV,
    "PATH",
    "HOME",
    "PYTHONPATH",
    "USER",
)


def _filter_child_env(env: Mapping[str, str]) -> dict[str, str]:
    """把父进程环境收窄成子进程需要的白名单键，只保留源环境里实际存在的——
    ⛔ 不为不存在的键编造值。"""
    return {key: env[key] for key in _CHILD_ENV_ALLOWLIST if key in env}


def build_headless_argv(claude_bin: str, budget: str) -> list[str]:
    """拼出完整 argv（含二进制路径与预算值）。**纯函数**，⛔ 不读环境、不起进程。

    白名单里 ⛔ **不出现** `send-followup`（合规红线：对外通道不可代）、
    ⛔ 不出现 `git push`（design D7），且权限模式固定 `acceptEdits`——
    ⛔ 绝不使用跳过全部确认的模式（合规红线 + design D4）。
    """
    return [claude_bin, *HEADLESS_ARGV_FIXED_PART, "--max-budget-usd", budget]


# tools/liaison/unpack/dispatch.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DispatchOutcome:
    """一次起活尝试的结果。`status` 三态对应 spec「起活审计三态」。"""

    status: str  # "started" | "skipped_busy" | "failed"
    reason: str | None = None
    pid: int | None = None
    log_path: str | None = None


class _ClaudeBinaryNotFound(Exception):
    """内部哨兵：区分「二进制解析失败」与「popen 本身抛异常」，两者的审计
    `reason` 不同（`binary_not_found` vs `process_create_failed`），⛔ 不让
    调用方看到——只在本函数体内捕获。"""


def _utc_log_stamp(now: datetime) -> str:
    """日志文件名用 UTC 戳（design D12，⚠️ 与台账的 CST 口径故意不同——
    台账是给人看的，日志戳只用于排序与去重，用 UTC 免去夏令时/时区换算的坑）。
    """
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_lock_atomic(path: Path, *, pid: int, started_at: datetime, log_path: Path) -> None:
    payload = {
        "pid": pid,
        "started_at": started_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        "log": str(log_path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def dispatch_headless_unpack(
    *,
    charter_text: str | None,
    prompt: str,
    log_dir: Path,
    lock_path: Path,
    env: Mapping[str, str],
    now: datetime,
    popen: Callable[..., Any] = subprocess.Popen,
) -> DispatchOutcome:
    """非阻塞起一个拆件会话。⛔ **本函数不读写信号文件**（spec 明写）——信号由
    `bridge.run_bridge` 追加、由 `unpack-signal --clear` 清除，两头都不是这里。

    四类失败——`charter_missing` / `log_file_failed` /
    `binary_not_found`+`process_create_failed`（同属「进程创建失败」一类，
    两个具体原因） / `unexpected_error`——**全部**转成 `DispatchOutcome(status="failed")`
    返回，⛔ 一个 `raise` 都不许漏到调用方。
    """
    if charter_text is None:
        return DispatchOutcome(status="failed", reason="charter_missing")

    try:
        lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else None
    except OSError:
        # 锁文件读不出来，按 spec「查询失败归不存活」同一精神处理——不存活即不忙。
        lock_text = None
    if compute_is_busy(lock_text, compute_is_alive):
        return DispatchOutcome(status="skipped_busy")

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{_utc_log_stamp(now)}.log"
        log_file = open(log_path, "wb")
    except OSError as exc:
        logger.error("拆件会话日志文件建不了：%s", log_path if 'log_path' in dir() else log_dir, exc_info=True)
        return DispatchOutcome(status="failed", reason="log_file_failed")

    try:
        try:
            claude_bin = resolve_claude_bin(env)
            if claude_bin is None:
                raise _ClaudeBinaryNotFound()
            budget = (env.get(BUDGET_ENV) or DEFAULT_BUDGET_USD).strip()
            argv = build_headless_argv(claude_bin, budget)
            process = popen(
                argv,
                cwd=str(REPO_ROOT),
                stdin=subprocess.PIPE,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=_filter_child_env(env),
            )
        except _ClaudeBinaryNotFound:
            return DispatchOutcome(status="failed", reason="binary_not_found")
        except Exception as exc:
            logger.error("拆件会话进程创建失败：%s", exc, exc_info=True)
            return DispatchOutcome(status="failed", reason="process_create_failed")
    finally:
        # Popen 已经把 log_file 的 fd 复制给子进程（POSIX 语义）；父进程这边
        # 关闭它不影响子进程继续写——⛔ 不许因为"看起来该等"就调 process.wait()，
        # 那会把值守线程拖进拆件会话的整个生命周期，违反「不阻塞」。
        log_file.close()

    try:
        if process.stdin is not None:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        _write_lock_atomic(lock_path, pid=process.pid, started_at=now, log_path=log_path)
    except Exception as exc:
        logger.error(
            "拆件会话已起（pid=%s）但收尾步骤失败（写 prompt / 写锁）：%s",
            getattr(process, "pid", None), exc, exc_info=True,
        )
        # I5（2026-09-16 修）：这里失败时子进程已经真的跑起来了，但锁文件没写
        # （或写了一半）——不 kill 掉它就会留下一个「活着、却没人知道它活着」的
        # 会话：下一次 `compute_is_busy` 读不到锁，会当成"不忙"再起一个,两个
        # headless 会话同时改同一份工作区。kill 本身也可能失败（进程已经自己退出、
        # 权限问题），⛔ 不让这个次生失败掩盖掉原始异常，只记日志。
        try:
            process.kill()
        except Exception:
            logger.error(
                "kill 已起的子进程（pid=%s）本身也失败，可能留下无人监管的会话",
                getattr(process, "pid", None), exc_info=True,
            )
        return DispatchOutcome(status="failed", reason="unexpected_error")

    return DispatchOutcome(status="started", pid=process.pid, log_path=str(log_path))
