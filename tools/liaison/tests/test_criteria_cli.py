"""4.2·`criteria` 子命令的 CLI 层：参数校验、退出码、原子写。

⛔ 全程用 tmp_path 造台账文件，不碰仓库里的真实 `docs/跟进信/口径点台账.md`
——测试要能在任何机器、任何顺序下重复跑，不能依赖也不能污染那份真实台账。
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from tools.liaison.unpack import criteria

TODAY = datetime.date(2026, 9, 16)

LEDGER_TEXT = """# 口径点台账

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |
"""


@pytest.fixture()
def ledger(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "口径点台账.md"
    path.write_text(LEDGER_TEXT, encoding="utf-8")
    return path


def test_add_appends_new_row_and_returns_ok(ledger, capsys):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "新口径点"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "| `HR-G-02` | 人事部#2 | 新口径点 | 待专员 |  | 2026-09-16 |" in text
    assert "HR-G-02" in capsys.readouterr().out


def test_add_without_desc_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_transition_with_evidence_updates_row(ledger):
    code = criteria.criteria_main(
        [
            "--id", "HR-G-01",
            "--to", "已签认",
            "--evidence", "docs/跟进信/回件/人事部#1-2026-09-16.md#a",
        ],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "已签认" in text
    assert "docs/跟进信/回件/人事部#1-2026-09-16.md#a" in text


def test_transition_to_已签认_without_evidence_exits_3_and_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已签认"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_MISSING_EVIDENCE
    assert ledger.read_bytes() == before


def test_transition_unknown_id_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--id", "HR-G-99", "--to", "已回复"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_add_and_id_together_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "x", "--id", "HR-G-01", "--to", "已回复"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_neither_add_nor_id_exits_bad_args(ledger):
    code = criteria.criteria_main([], ledger_path=ledger, today=TODAY)
    assert code == criteria.EXIT_BAD_ARGS


def test_missing_ledger_file_exits_bad_args(tmp_path):
    missing = tmp_path / "不存在.md"
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已回复"], ledger_path=missing, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


# ── final-review Fix 3：`|` 会打乱表格列结构，CLI 层必须拦 ────────────────


def test_add_with_pipe_in_from_exits_bad_args_and_leaves_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2 | 已签认", "--desc", "x"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS
    assert ledger.read_bytes() == before


def test_add_with_pipe_in_desc_exits_bad_args_and_leaves_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "随时在组里说 | 每周一次"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS
    assert ledger.read_bytes() == before


def test_transition_with_pipe_in_evidence_exits_bad_args_and_leaves_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已签认", "--evidence", "文件A#节 | 文件B#节"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS
    assert ledger.read_bytes() == before


# ── final-review Fix 4：criteria 子命令确实接线进 __main__.py 且可达 ───────


def test_criteria_branch_short_circuits_before_credentials_are_required():
    """结构断言：`criteria` 分支必须排在 `raise SystemExit(main())` 之前。

    `main()` 首先做的是 `load_credentials()`（企微凭据）。`criteria` 只读写
    一个 markdown 文件、不需要凭据——分支排到后面去会让一台还没配企微凭据
    的机器永远跑不了这条子命令，且报错说的是"缺凭据"，与真实原因无关。
    """
    import pathlib as _pathlib

    main_module = _pathlib.Path(criteria.__file__).resolve().parents[1] / "__main__.py"
    lines = main_module.read_text(encoding="utf-8").splitlines()
    branch = next(
        i for i, ln in enumerate(lines) if 'sys.argv[1] == "criteria"' in ln
    )
    entry = next(i for i, ln in enumerate(lines) if ln.strip() == "raise SystemExit(main())")
    assert branch < entry, "criteria 分支必须排在 `raise SystemExit(main())` 之前"


def test_criteria_subcommand_reachable_via_subprocess_without_credentials():
    """子进程级可达性：`python -m tools.liaison criteria ...` 不需要任何
    企微凭据环境变量就能跑到 `criteria_main` 内部的业务校验（台账里没有这个
    ID）——证明它真的短路在 `main()` 的 `load_credentials()` 之前，不是
    只在单元测试里 import 得到。
    """
    import subprocess
    import sys

    repo_root = pathlib.Path(criteria.__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "tools.liaison", "criteria", "--id", "HR-G-99", "--to", "已回复"],
        cwd=repo_root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == criteria.EXIT_BAD_ARGS, (result.stdout, result.stderr)
    assert "台账里没有 HR-G-99 这一行" in (result.stdout + result.stderr)
