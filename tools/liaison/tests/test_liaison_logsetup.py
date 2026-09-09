"""8.4 · 日志装配：轮转有界、脱敏落盘、不可写时降级、幂等。

Task 3 范围说明：`LoggingStatus` / `setup_logging` / `teardown_logging` /
`logging_status` 在 `tools/liaison/logsetup.py` 里已经落地（Task 2 提前实现，
且经过 review 修了三处缺陷：`_resolve_level` 容错、`teardown_logging` 复位
level、`RedactingFormatter.formatException` 源头脱敏）。本文件只负责测试，
⛔ 不重复定义那些符号。
"""

from __future__ import annotations

import ast
import logging
import logging.handlers
import pathlib

from tools.liaison import logsetup

LOGSETUP_SOURCE = pathlib.Path(logsetup.__file__)


# ---------------------------------------------------------------------------
# 1. 轮转有界：文件数 ≤ backupCount + 1。
#
# 这条断言本身证明力有限——一个总是通过的断言和一个总是失败的断言都不能
# 证明它在测真实的东西。判定力的证明是**变异测试**：手工把 backupCount 的
# 约束废掉，确认同一条断言在同样的写入量下会转红，再确认恢复约束后转绿。
# 该变异实验用一次性脚本跑在本文件之外（不落进仓库、不改 logsetup.py），
# 实测记录在 task-3-report.md，这里摘要结论：
#
#   [BOUNDED]   backupCount=2 → 文件数 = 3，断言 PASSED（绿）
#   [UNBOUNDED] 用已构造好的 handler，把 backupCount 事后改成 1000
#               （即"废掉约束"，不改 logsetup.py 源码）→ 文件数 = 60，
#               断言 FAILED（红）：「文件数超过 backupCount + 1：[... 60 个 ...]」
#
# 两次用的是完全相同的写入量与相同的断言，唯一变量是约束存不存在——
# 证明这条断言确实在检验轮转有界这件事，不是摆设。
# ---------------------------------------------------------------------------


def test_rotation_keeps_at_most_backup_count_plus_one_file(tmp_path):
    """轮转触发后文件数 ≤ backupCount + 1（本条已用变异测试证伪，见上方注释）。

    写入量 = 300 行 × ~140 字节 ≈ 42 KiB，远超
    `maxBytes × (backupCount + 2)` = 1024 × 4 = 4096 字节，确保多轮轮转真的
    发生（而不是侥幸只轮转了一次）。这同时是 `runtime-observability`
    「日志量超过配置上限」的可执行形式：上界必须是**配置值**决定的，
    ⛔ 不能随运行时间无限增长。
    """
    logsetup.setup_logging(log_dir=tmp_path, max_bytes=1024, backup_count=2)
    logger = logging.getLogger("tools.liaison.rotation_probe")
    for index in range(300):
        logger.info("填充日志行 %03d %s", index, "x" * 120)

    files = sorted(path.name for path in tmp_path.glob("liaison.log*"))
    assert len(files) >= 2, f"1 KiB 上限下 300 行必须已经轮转过，实际只有 {files}"
    assert len(files) <= 3, f"文件数超过 backupCount + 1：{files}"


# ---------------------------------------------------------------------------
# 2. 脱敏落盘：走真实 RotatingFileHandler，手机号/邮箱/凭据一个不许留在磁盘上，
#    thread_id 原样保留（opener 约束 2 逐字：⛔ 不脱敏 msgid、thread_id）。
#    纯函数层（compute_redacted_text）已经在 test_liaison_log_redaction.py
#    覆盖过；这里是必须过一遍文件 handler + Formatter 的落盘版本。
# ---------------------------------------------------------------------------


def test_redaction_survives_the_real_file_handler(tmp_path):
    """一条记录里同时塞手机号、邮箱、凭据取值、thread_id，落盘后逐一核对。

    ⚠️ 凭据写成 `HR_LIAISON_BOT_SECRET=xxxx` 且**不在源码行首**（前面还有手机号/
    邮箱字样），避免被 `test_liaison_no_secrets_in_vcs.py` 的行首赋值扫描器
    误判成真凭据入库——`xxxx` 本身也不是真值。
    """
    status = logsetup.setup_logging(log_dir=tmp_path)
    logger = logging.getLogger("tools.liaison.disk_probe")
    logger.warning(
        "候选人手机 13800001234 邮箱 someone@example.com 配置 HR_LIAISON_BOT_SECRET=xxxx"
        " thread_id=probe-001"
    )
    for handler in logging.getLogger(logsetup.PACKAGE_LOGGER_NAME).handlers:
        handler.flush()

    assert status.log_file is not None
    contents = pathlib.Path(status.log_file).read_text(encoding="utf-8")

    assert "13800001234" not in contents
    assert "someone@example.com" not in contents
    assert "xxxx" not in contents
    assert logsetup.PHONE_MASK in contents
    assert logsetup.EMAIL_MASK in contents
    assert logsetup.SECRET_MASK in contents
    assert "HR_LIAISON_BOT_SECRET" in contents, "键名要留着，排障得知道是哪一项没配好"
    assert "thread_id=probe-001" in contents, "⛔ thread_id 是显式豁免的排障字段"


# ---------------------------------------------------------------------------
# 3. 幂等：调两次不重复挂 handler / filter。
# ---------------------------------------------------------------------------


def test_setup_logging_is_idempotent(tmp_path):
    """opener 约束 4 逐字：调两次不重复挂 handler。"""
    first = logsetup.setup_logging(log_dir=tmp_path)
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    handler_count = len(logger.handlers)

    second = logsetup.setup_logging(log_dir=tmp_path)

    assert len(logger.handlers) == handler_count
    assert first.handlers == second.handlers == ["stderr", "file"]
    filters = [f for f in logger.filters if isinstance(f, logsetup.RedactionFilter)]
    assert len(filters) == 1, f"包级 logger 上的脱敏 Filter 重复挂了：{filters}"


def test_setup_logging_keeps_a_stderr_copy(tmp_path, capsys):
    """launchd 会收 stderr（部署约束 4 + design D12）——文件之外必须留一份。"""
    logsetup.setup_logging(log_dir=tmp_path)
    logging.getLogger("tools.liaison.stderr_probe").warning("值守通道已启动")
    assert "值守通道已启动" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 4. 降级：目录不可写时不许崩，退回仅 stderr，且降级事实两条路都暴露
#    （ERROR 日志 + LoggingStatus）。
# ---------------------------------------------------------------------------


def test_setup_logging_degrades_to_stderr_when_the_directory_is_unusable(tmp_path, capsys):
    """`runtime-observability`「日志写入位置不可用」：⛔ 不崩溃、⛔ 不静默。"""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    status = logsetup.setup_logging(log_dir=blocker)

    assert status.configured is True
    assert status.degraded is True
    assert status.handlers == ["stderr"]
    assert status.log_file is None
    assert status.reason
    assert logsetup.logging_status().degraded is True
    err = capsys.readouterr().err
    assert "日志文件通道不可用" in err, "⛔ 不许静默降级为「什么都不记录」"
    assert "not-a-directory" in err


def test_business_logging_still_works_while_degraded(tmp_path, capsys):
    """降级之后业务功能不受影响——记录动作本身不许抛，脱敏也不许一起失效。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("", encoding="utf-8")
    logsetup.setup_logging(log_dir=blocker)
    logging.getLogger("tools.liaison.degraded_probe").warning("手机 13812345678")
    err = capsys.readouterr().err
    assert "13812345678" not in err, "降级路径上脱敏 ⛔ 不许一起失效"
    assert logsetup.PHONE_MASK in err


# ---------------------------------------------------------------------------
# 5. 配置来源：环境变量覆盖、手抖的环境变量值不许打死进程。
# ---------------------------------------------------------------------------


def test_log_dir_comes_from_the_process_environment(tmp_path, monkeypatch):
    """可由环境变量覆盖（opener 约束 1）。⚠️ 读的是**进程环境**，不是 .env。"""
    target = tmp_path / "from-env"
    monkeypatch.setenv(logsetup.LOG_DIR_ENV, str(target))
    status = logsetup.setup_logging()
    assert status.log_file == str(target / logsetup.LOG_FILENAME)


def test_bad_env_values_fall_back_to_defaults_instead_of_crashing(tmp_path, monkeypatch):
    """手抖的环境变量 ⛔ 不许把进程的第一个动作打死。"""
    monkeypatch.setenv(logsetup.LOG_MAX_BYTES_ENV, "不是数字")
    monkeypatch.setenv(logsetup.LOG_BACKUP_COUNT_ENV, "-3")
    status = logsetup.setup_logging(log_dir=tmp_path)
    assert status.degraded is False
    handler = [
        h
        for h in logging.getLogger(logsetup.PACKAGE_LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ][0]
    assert handler.maxBytes == logsetup.DEFAULT_MAX_BYTES
    assert handler.backupCount == logsetup.DEFAULT_BACKUP_COUNT


def test_good_env_values_are_actually_read_not_just_not_crashed_on(tmp_path, monkeypatch):
    """fix round 1 · Important 1 的回归：只测非法取值回落默认值，测不出「环境变量
    根本没被读」或「读到了但被曲解」——两者在那条测试下都表现为"用了默认值"或
    "凑巧看起来正常"。这里显式设置合法取值，断言构造出来的
    `RotatingFileHandler` 真的带着这两个值，而不是默认值或被曲解的值。

    已用变异实验验证判定力（见 task-3-report.md 的 fix round 1 一节）：
    `_env_int()` 提前返回 `default`（环境变量根本没读）、以及
    `_env_int()` 把合法取值 ×10 曲解，这两种变异在本测试新增之前都能让
    整个 497 条套件保持全绿；新增本测试后两者均转红。
    """
    monkeypatch.setenv(logsetup.LOG_MAX_BYTES_ENV, "2048")
    monkeypatch.setenv(logsetup.LOG_BACKUP_COUNT_ENV, "3")
    status = logsetup.setup_logging(log_dir=tmp_path)
    assert status.degraded is False
    handler = [
        h
        for h in logging.getLogger(logsetup.PACKAGE_LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ][0]
    assert handler.maxBytes == 2048, f"实际 {handler.maxBytes}——环境变量没被真的读到"
    assert handler.backupCount == 3, f"实际 {handler.backupCount}——环境变量没被真的读到"


def test_teardown_only_removes_handlers_it_owns(tmp_path):
    """⛔ 不许写成 handlers.clear()——别人挂的 handler 不归本模块管。"""
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        logsetup.setup_logging(log_dir=tmp_path)
        logsetup.teardown_logging()
        assert foreign in logger.handlers
    finally:
        logger.removeHandler(foreign)


# fix round 1 · Minor 3：`teardown_logging` 复位 level 的回归已存在于
# test_liaison_log_redaction.py::test_teardown_resets_the_logger_level_to_notset
# （同样的 level="ERROR" 设置、同样两条断言），本文件不再重复一份。

# ---------------------------------------------------------------------------
# 6. 结构守卫：logsetup.py 里不许出现 with（TD-18 的事务扫描器判据）。
# ---------------------------------------------------------------------------


def test_logsetup_module_never_uses_a_with_statement():
    """⛔ tools/liaison 非测试代码里不许出现 with（见模块 docstring 第 2 段）。"""
    tree = ast.parse(LOGSETUP_SOURCE.read_text(encoding="utf-8"), filename=str(LOGSETUP_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]
