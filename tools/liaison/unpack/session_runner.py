"""拆件会话 wrapper（1001I）：起引擎、原样转发 stdin、等退出、代提交＋清信号、给本人一条私信。

`dispatch_headless_unpack` 起的**不再是引擎二进制本身**，而是本模块：

    <python> -m tools.liaison.unpack.session_runner \
        --msgid <M> --log-path <L> --checkpoint <C> --letter-number <N> -- <引擎 argv 逐字>

本模块只做四件事，按顺序：

1. `Popen(<引擎 argv>)` 并**继承** stdin/stdout/stderr——prompt 由 dispatch 写进
   本进程的 stdin 后关掉，原样（同一个 fd，不经过本模块的读写）到达引擎；引擎的
   输出照旧落进 dispatch 已经开好的日志文件。⛔ 本模块不缓冲、不改写、不读 prompt。
2. `wait()` 拿退出码，量一次耗时。
3. **代提交＋清信号**（1001L）：仅当引擎退出码为 0 时，对章程 §三 的四条白名单路径
   做 `git status --porcelain` →（有改动才）`git add -- <路径>` → `git commit -m …`，
   成功后再按 §一.5 清检查点之前的信号（`python -m tools.liaison unpack-signal
   --clear --before <检查点>`，cwd＝仓库根、`PYTHONPATH=.`）。任一步失败一律
   **fail-closed**：记 ERROR、备注原因、**不往下走**（⛔ 绝不在没提交的情况下清信号）。
4. 往本人通知发件箱写一行（`dedupe_key = "unpack-session-exit:{msgid}"`），正文只含
   msgid／退出码／日志路径／耗时／时刻；写库失败只记日志、不上抛。

⛔ **只做"等待＋收口＋通知"**：不读会话输出内容、不判定拆件结论（结论仍按章程落 `docs/`）。
⛔ **不是拆件会话本身**：本模块跑在值守侧（它与值守服务同一个仓库根、同一条通道，
自己开一条库连接、用完就关），拆件会话的权限一个字节都不加——起活失败／会话结束
两条通知、以及本次的代提交与清信号，都由值守侧进程做，正是这条纪律。

由来（1001I）：09-24 那次起活死于「订阅被禁」，**没有任何人被告知**，信号挂在
`unpack-signal.json` 里直到 10-08 才被人工发现。会话结束通知就是堵这个洞的——
谁起、起没起来、退了没有、退在几号，一律先经值守通道私信本人，零轮询。

由来（1001L，2026-10-08 Shao Peishen 答 `2a`）：10-08 `人事部#3` 的 codex 拆件首跑把
回灌结论／台账转态／接力登记**全写完了**，但它的沙箱对 `.git` 只读（`git add` 三次
`Operation not permitted`），提交请求通道又被拆件守卫按章程 deny ⇒ 文档已写、无法提交、
信号清不掉，每轮都要主会话人工补提交。修法＝照 0930D「执行器代提交」先例，把这件收口
搬到**跑在 launchd、对 `.git` 有写权限的值守侧**——⛔ 拆件会话自身的权限与章程红线一个字不加。
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.liaison import owner_notify
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_enqueue_owner_notify
from tools.liaison.unpack.dispatch import (
    CHARTER_WRITABLE_PATHS,
    NO_LETTER_NUMBER_PLACEHOLDER,
)

logger = logging.getLogger("tools.liaison.unpack.session_runner")

# tools/liaison/unpack/session_runner.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 会话结束本人通知的幂等键前缀（1001I）。完整键＝`{前缀}:{msgid}`。
#: ⛔ 只在这里写死一次——别处（含测试）一律 import 这个常量。
SESSION_EXIT_DEDUPE_PREFIX = "unpack-session-exit"

#: 连引擎进程都没能创建时，通知正文里记的哨兵"退出码"——此刻没有真实退出码可取。
#: ⛔ 不是 POSIX 退出码，只为让正文那一行不空着；真实情况写在「备注」里。
SPAWN_FAILED_EXIT_CODE = -1

#: 进程创建失败时本进程返回给外层的退出码（非零即异常）。
SPAWN_FAILED_RC = 1

#: 代提交／清信号调用外部命令的上限秒数（1001L）。超时算失败（fail-closed：不清信号），
#: ⛔ 不许让一条挂住的 git 把「会话结束通知」也一起拖住——通知是本包装器存在的理由。
COMMAND_TIMEOUT_SECONDS = 60

#: 清信号用的子命令（1001L）。⚠️ 这是**值守侧**替拆件会话补做章程 §一.5 的收口，
#: ⛔ 不是给拆件会话开的新权限（那边仍只有 argv 白名单里那一条 canonical 调法）。
LIAISON_MODULE = "tools.liaison"
UNPACK_SIGNAL_SUBCOMMAND = "unpack-signal"

#: 引擎非零退出时的备注（opener 逐字给定措辞）：此时**不碰 git、不清信号**——
#: 会话中途死掉时白名单路径可能只写了一半，提交半份结论比不提交更危险。
NONZERO_EXIT_NOTE = "会话非零退出，未代提交、未清信号"

#: 没拿到检查点时刻时的备注：清信号是按「只清检查点之前的项」做的，⛔ 没有检查点就
#: 无从判断边界，宁可整条收口留步（代提交也一并跳过，免得留下「提交了但信号还在」
#: 的半吊子态——那正是 1001L 要堵的洞）。
NO_CHECKPOINT_NOTE = "未拿到检查点时刻，未代提交、未清信号"


def build_session_exit_notice(
    *,
    msgid: str,
    exit_code: int,
    log_path: str | Path,
    elapsed_seconds: float,
    now: datetime,
    note: str | None = None,
) -> tuple[str, str]:
    """会话结束 ⇒ `(dedupe_key, 正文)`。**纯函数**，⛔ 不读环境、不写库。

    正文**只由这几个入参拼出**：msgid、退出码、日志路径、耗时、时刻（＋可选备注）。
    ⛔ 不含任何归档件内容或候选人个人信息。
    """
    dedupe_key = f"{SESSION_EXIT_DEDUPE_PREFIX}:{msgid}"
    lines = [
        "【HR·拆件会话结束】",
        f"- 消息标识（msgid）：{msgid}",
        f"- 退出码：{exit_code}",
        f"- 耗时：{elapsed_seconds:.1f} 秒",
        f"- 日志路径：{log_path}",
        f"- 时刻：{now.isoformat()}",
    ]
    if note:
        lines.append(f"- 备注：{note}")
    return dedupe_key, "\n".join(lines)


def enqueue_owner_notify(
    *,
    dedupe_key: str,
    body: str,
    db_path: str | Path | None = None,
) -> bool:
    """往本人通知发件箱写一行（只入队、不发送——发送是值守线程按既有通道的事）。

    ⛔ **任何失败只记日志、不上抛**：一条发不出去的提示不配有让起活链路变形的
    破坏力（与起活审计同一基调）。返回是否真的新入队了一条（幂等命中 ⇒ False）。

    ⛔ 收件人不是参数、也不在这里解析——`owner_notify` 的消费者按名单里
    `OWNER_NAME` 那一条发，本函数只负责把行放进发件箱。
    """
    try:
        conn = liaison_db.get_connection(db_path)
        try:
            liaison_db.init_schema(conn)
            result = effect_enqueue_owner_notify(
                conn,
                thread_id=owner_notify.OWNER_NOTIFY_THREAD_ID,
                business_key=dedupe_key,
                body=body,
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 见 docstring
        logger.error(
            "本人通知入队失败（dedupe_key=%s），只记日志、不上抛", dedupe_key, exc_info=True
        )
        return False
    return result is not None


def default_run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """真实执行一条外部命令（1001L 代提交／清信号的默认调用缝；测试注入替身）。

    `capture_output=True, text=True, check=False`：⛔ 本函数**从不**因非零退出码
    抛异常——代提交链路对不同失败的分支处置各不相同（fail-closed、留着信号），
    那层判断属于调用方，不该被这里抢走；只有命令本身跑不起来（如 `FileNotFoundError`）
    或超时才会抛出，调用方同样按失败处理。
    """
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def build_unpack_commit_message(*, letter_number: str | None, msgid: str) -> str:
    """代提交的 commit message（1001L，opener 逐字给定形状）。**纯函数**。

    `chore(unpack): {信件编号} 回灌落档（{msgid 前 8}）`——信件编号缺失／为空时
    用与起活失败通知同一个占位（`NO_LETTER_NUMBER_PLACEHOLDER`，⛔ 不另造一份）。
    """
    letter = letter_number or NO_LETTER_NUMBER_PLACEHOLDER
    return f"chore(unpack): {letter} 回灌落档（{msgid[:8]}）"


def _resolve_addable_paths(
    paths: Sequence[str],
    *,
    cwd: Path,
    run_command: Callable[..., Any],
) -> list[str]:
    """把四条白名单路径收窄成 `git add` **真能接受**的一组路径。

    ⚠️ **为什么必须有这一步**（2026-10-08 在 `/tmp` 临时仓实测）：`git add` 对
    「匹配不到任何文件」的 pathspec 是**致命错误**（`fatal: pathspec … did not match
    any files`，退出码 128，且整条命令**什么都不暂存**）。而章程 §三 那四条路径
    并非天生都存在——`docs/跟进信/口径点台账.md` 只在首次口径点转态后才出现、
    `docs/跟进信/回件/` 目录在首封回件之前不存在。不做这一步，每一次代提交都会在
    第一步 128 打回、整条收口永远停在原地（比这次的原始故障更难查：日志里只有一行
    `git add 退出码 128`）。原故障（`Operation not permitted`）是**权限**问题，
    这个是**paths 存在性**问题，两码事。

    判据：**工作区里存在**（含目录）**或在索引里已跟踪**（`git ls-files`——覆盖
    「白名单文件被删」这种改动，被删的路径 git 仍能用 pathspec 匹配到索引里的它）。
    ⛔ 只在这四条之内做减法，⛔ 绝不引入清单外的路径。
    """
    probe = run_command(["git", "ls-files", "--", *paths], cwd=cwd)
    if probe.returncode != 0:
        raise RuntimeError(f"git ls-files 退出码 {probe.returncode}")
    tracked = {
        line.strip() for line in (probe.stdout or "").splitlines() if line.strip()
    }
    return [
        path
        for path in paths
        if (Path(cwd) / path).exists()
        or any(entry == path or entry.startswith(path) for entry in tracked)
    ]


def clear_unpack_signal(
    *,
    checkpoint: str,
    cwd: Path = REPO_ROOT,
    run_command: Callable[..., Any] = default_run_command,
) -> str:
    """按章程 §一.5 清信号（只清检查点之前的项）。返回写进会话结束通知备注的一句话。

    唯一 canonical 的调法：`<本进程解释器> -m tools.liaison unpack-signal --clear
    --before <检查点>`，cwd＝仓库根、`PYTHONPATH=.`（`tools.liaison` 不是装进
    site-packages 的包，缺 `PYTHONPATH` 就 import 不到）。用 `sys.executable`
    而不是写死路径：本模块就是被这个解释器起起来的，它必然能 import 本包。

    ⛔ 任何失败都不上抛——返回的备注会把原因写进本人私信；信号原样留着，
    下一轮拆件会话会再探到（章程 §四：不会丢）。
    """
    argv = [
        sys.executable or "python3",
        "-m",
        LIAISON_MODULE,
        UNPACK_SIGNAL_SUBCOMMAND,
        "--clear",
        "--before",
        checkpoint,
    ]
    env = {**os.environ, "PYTHONPATH": "."}
    try:
        result = run_command(argv, cwd=cwd, env=env)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.error("清信号命令未能执行（before=%s）：%s", checkpoint, exc, exc_info=True)
        return f"清信号失败（{type(exc).__name__}），信号保留待下一轮"
    if result.returncode != 0:
        logger.error("清信号命令退出码 %s（before=%s）", result.returncode, checkpoint)
        return f"清信号失败（退出码 {result.returncode}），信号保留待下一轮"
    return f"信号已清（before={checkpoint}）"


def collect_changes_and_clear_signal(
    *,
    exit_code: int,
    checkpoint: str | None,
    letter_number: str | None,
    msgid: str,
    cwd: Path = REPO_ROOT,
    run_command: Callable[..., Any] = default_run_command,
) -> str:
    """代提交白名单路径 ＋ 清信号（1001L）。返回写进会话结束通知备注的一句话。

    顺序严格按 opener：**先提交、后清信号**（章程 §一.5：清信号只在回灌结论已落档
    并 commit 完成之后）。任一步失败一律 fail-closed：记 ERROR、返回带原因的备注、
    **不往下走**，信号原样保留给下一轮。

    - `exit_code != 0` ⇒ 不碰 git、不清信号（半份结论不提交）；
    - 没拿到检查点 ⇒ 同样整条留步（无从判断清到哪儿为止）；
    - 白名单（`dispatch.CHARTER_WRITABLE_PATHS`，⛔ 不抄第二份）之外一个路径都不 add：
      `git status` 只看这四条，`git add` 只带这四条，`git status` 里别人的改动原样留着。
    """
    if exit_code != 0:
        logger.info("会话非零退出（%s），未代提交、未清信号", exit_code)
        return NONZERO_EXIT_NOTE
    if not checkpoint:
        logger.error("未拿到检查点时刻：无从判断清信号边界，未代提交、未清信号")
        return NO_CHECKPOINT_NOTE

    paths = list(CHARTER_WRITABLE_PATHS)
    try:
        # `--untracked-files=all` 显式带上：本判据不能取决于用户配置里
        # `status.showUntrackedFiles` 恰好是 normal（新落档的整目录会被折叠成一个
        # 目录项，逐个文件的判据会失真）。
        status = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
            cwd=cwd,
        )
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.error("代提交前 git status 未能执行：%s", exc, exc_info=True)
        return f"代提交失败（git status 未执行：{type(exc).__name__}），未代提交、未清信号"
    if status.returncode != 0:
        logger.error("代提交前 git status 退出码 %s", status.returncode)
        return f"代提交失败（git status 退出码 {status.returncode}），未代提交、未清信号"

    changed = [line for line in (status.stdout or "").splitlines() if line.strip()]
    if changed:
        try:
            add_paths = _resolve_addable_paths(paths, cwd=cwd, run_command=run_command)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.error("过滤可暂存白名单路径失败：%s", exc, exc_info=True)
            return f"代提交失败（git ls-files 未执行：{type(exc).__name__}），未代提交、未清信号"
        if not add_paths:
            logger.error("白名单四条路径既不在工作区也不在索引里，无可暂存内容")
            return "代提交失败（白名单路径无可暂存内容），未代提交、未清信号"
        try:
            add = run_command(["git", "add", "--", *add_paths], cwd=cwd)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.error("git add 未能执行：%s", exc, exc_info=True)
            return f"代提交失败（git add 未执行：{type(exc).__name__}），未代提交、未清信号"
        if add.returncode != 0:
            logger.error("git add 退出码 %s", add.returncode)
            return f"代提交失败（git add 退出码 {add.returncode}），未代提交、未清信号"
        message = build_unpack_commit_message(letter_number=letter_number, msgid=msgid)
        try:
            commit = run_command(["git", "commit", "-m", message], cwd=cwd)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.error("git commit 未能执行：%s", exc, exc_info=True)
            return f"代提交失败（git commit 未执行：{type(exc).__name__}），未代提交、未清信号"
        if commit.returncode != 0:
            logger.error("git commit 退出码 %s（%s）", commit.returncode, message)
            return f"代提交失败（git commit 退出码 {commit.returncode}），未代提交、未清信号"
        collect_note = f"代提交 {len(changed)} 文件"
    else:
        logger.info("白名单路径无改动（视为已落档），不代提交")
        collect_note = "无改动（视为已落档）"

    return f"{collect_note}｜{clear_unpack_signal(checkpoint=checkpoint, cwd=cwd, run_command=run_command)}"


def run_session(
    engine_argv: Sequence[str],
    *,
    msgid: str,
    log_path: str | Path,
    popen: Callable[..., Any] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] | None = None,
    cwd: Path = REPO_ROOT,
    db_path: str | Path | None = None,
    checkpoint: str | None = None,
    letter_number: str | None = None,
    run_command: Callable[..., Any] = default_run_command,
) -> int:
    """起引擎 → 等退出 → 代提交＋清信号 → 通知。返回**引擎的退出码**（进程没起得来时
    返回 `SPAWN_FAILED_RC`）。⛔ 永不因为收口或通知写不进去而改变返回值/上抛。

    `popen`/`monotonic`/`now`/`db_path`/`run_command` 是测试注入缝（生产走默认值）。
    stdin 与 stdout/stderr **全部继承**：prompt 原样到达引擎，引擎输出原样落进日志文件。

    `checkpoint`／`letter_number` 由 dispatch 从**本次 prompt 用的那一份取值**带来
    （见 `dispatch.build_session_runner_argv`）；收口的结论（代提交/清信号或失败原因）
    只进本人私信的「备注」一行，⛔ 不进日志文件、⛔ 不含归档件内容。

    ⚠️ **引擎没起来时不做收口**（`note` 保持「引擎进程未能创建」）：这一轮没有任何
    东西被写进白名单路径，也就没有可提交的内容——⛔ 不拿一条「未代提交、未清信号」
    的噪音盖住真正的原因（起活失败的通知由 dispatch 那一侧另发）。
    """
    started = monotonic()
    note: str | None = None
    try:
        process = popen(list(engine_argv), cwd=str(cwd))
    except Exception as exc:  # noqa: BLE001 —— 引擎起不来也是一种"会话结束"，照常通知
        exit_code = SPAWN_FAILED_EXIT_CODE
        rc = SPAWN_FAILED_RC
        note = f"引擎进程未能创建：{type(exc).__name__}"
        logger.error("会话 wrapper 起不了引擎进程：%s", exc, exc_info=True)
    else:
        exit_code = int(process.wait())
        rc = exit_code
        # 1001L：代提交白名单路径 ＋ 按章程清信号。⛔ 只记日志/带备注，绝不上抛、
        # 绝不改变本进程返回的引擎退出码（它是调用方唯一的判据）。
        try:
            note = collect_changes_and_clear_signal(
                exit_code=exit_code,
                checkpoint=checkpoint,
                letter_number=letter_number,
                msgid=msgid,
                cwd=cwd,
                run_command=run_command,
            )
        except Exception:  # noqa: BLE001 —— 双保险，同下面的通知
            logger.error("会话收口（代提交/清信号）意外抛异常", exc_info=True)
            note = "代提交/清信号意外失败，未代提交、未清信号"

    elapsed_seconds = monotonic() - started
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    dedupe_key, body = build_session_exit_notice(
        msgid=msgid,
        exit_code=exit_code,
        log_path=log_path,
        elapsed_seconds=elapsed_seconds,
        now=moment,
        note=note,
    )
    try:
        # `enqueue_owner_notify` 自己已经吞掉一切异常；这里再包一层是**双保险**——
        # 通知链路的任何变化都不该让本进程的退出码跟着变（它是引擎退出码的唯一出处）。
        enqueue_owner_notify(dedupe_key=dedupe_key, body=body, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 见注释
        logger.error("本人通知入队意外失败（dedupe_key=%s）", dedupe_key, exc_info=True)
    return rc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison.unpack.session_runner",
        description=(
            "拆件会话 wrapper：起引擎、转发 stdin、等退出，再给本人一条会话结束私信。"
        ),
    )
    parser.add_argument("--msgid", required=True, help="本次触发的消息标识（通知的幂等键分量）")
    parser.add_argument("--log-path", required=True, help="dispatch 开好的本次日志文件路径")
    # 1001L：收口（代提交＋清信号）要用的两个字段。默认空 ⇒ 整条收口留步
    # （见 `collect_changes_and_clear_signal` 的两条 fail-closed 分支），
    # ⛔ 不给默认值就等于"没带就当成没有检查点"，不做任何猜。
    parser.add_argument(
        "--checkpoint",
        default="",
        help="本次 prompt 前言里用的检查点时刻（ISO8601，与清信号同一份取值）",
    )
    parser.add_argument(
        "--letter-number",
        default="",
        help="本次触发的信件编号（代提交的 commit message 用；空则写占位）",
    )
    # `--` 之后的引擎 argv 原样透传，⛔ 本模块不解释、不重排、不补默认值。
    parser.add_argument("engine_argv", nargs=argparse.REMAINDER)
    return parser


def main(
    argv: Sequence[str],
    *,
    popen: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
    run_command: Callable[..., Any] | None = None,
    cwd: Path = REPO_ROOT,
    db_path: str | Path | None = None,
) -> int:
    """`python -m tools.liaison.unpack.session_runner …` 的实现。"""
    args = _build_parser().parse_args(list(argv))
    engine_argv = list(args.engine_argv)
    if engine_argv and engine_argv[0] == "--":
        # argparse 的 REMAINDER 会把 `--` 自己也收进来（3.9 与 3.14 实测都是），剔掉它。
        engine_argv = engine_argv[1:]
    if not engine_argv:
        print("⛔ 没给引擎 argv（`--` 之后要逐字带上引擎命令行）", file=sys.stderr)
        return SPAWN_FAILED_RC
    kwargs: dict[str, Any] = {"cwd": cwd, "db_path": db_path}
    if popen is not None:
        kwargs["popen"] = popen
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if now is not None:
        kwargs["now"] = now
    if run_command is not None:
        kwargs["run_command"] = run_command
    return run_session(
        engine_argv,
        msgid=args.msgid,
        log_path=args.log_path,
        checkpoint=args.checkpoint,
        letter_number=args.letter_number,
        **kwargs,
    )


if __name__ == "__main__":  # pragma: no cover —— 真实入口，测试直接调 main()/run_session()
    raise SystemExit(main(sys.argv[1:]))
