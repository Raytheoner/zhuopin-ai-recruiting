"""4.3·口径点台账的合规红线机器闸：

1. `criteria.py` 不 import `storage.db`、不 import `sqlite3`（spec「子命令不碰库」）
2. argparse 没有按时间/超期做批量操作的选项（spec「无任何超期自动签认路径」）
3. `compute_criteria_transition` 的判断逻辑里不出现 datetime/time 相关标识符
   （同一条 spec 要求的"源码 AST 无按时间改写状态为已签认的分支"）

⛔ 每条扫描器都配一条"证伪"用例：证明它改错了会读不出违规，而不是永远绿灯。
这三条断言本身不解析业务台账内容，因此哪怕 Task 1 的真实台账文件被删除、
移动，本文件的用例都不受影响。
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CRITERIA_MODULE = REPO_ROOT / "tools" / "liaison" / "unpack" / "criteria.py"


# ── ① 不碰数据库 ──────────────────────────────────────────────────────────


def _db_import_offenders(source: str, label: str) -> list[str]:
    tree = ast.parse(source, filename=label)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3" or alias.name.startswith(
                    "tools.liaison.storage"
                ):
                    offenders.append(f"{label}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (
                module == "sqlite3"
                or module.startswith("tools.liaison.storage")
            ):
                offenders.append(f"{label}: from {module} import ...")
    return offenders


def test_the_db_import_scanner_would_catch_a_violation():
    bad_source = (
        "from tools.liaison.storage import db\n"
        "import sqlite3\n"
    )
    offenders = _db_import_offenders(bad_source, "fake_criteria.py")
    assert len(offenders) == 2, offenders


def test_criteria_module_does_not_import_the_database():
    source = CRITERIA_MODULE.read_text(encoding="utf-8")
    offenders = _db_import_offenders(source, str(CRITERIA_MODULE))
    assert offenders == [], (
        "criteria.py 碰了值守数据库，违反 spec「转态命令不打开值守数据库」：\n"
        + "\n".join(offenders)
    )


# ── ② argparse 无按时间/超期的批量选项 ────────────────────────────────────


_FORBIDDEN_OPTIONS = ("--auto", "--expire", "--before", "--older-than")


def test_argparse_has_no_time_based_batch_options():
    from tools.liaison.unpack.criteria import build_parser

    option_strings = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
    }
    hits = option_strings & set(_FORBIDDEN_OPTIONS)
    assert hits == set(), f"argparse 出现了按时间批量操作的选项：{hits}"


# ── ③ 状态判断里不出现时间标识符 ──────────────────────────────────────────


_FORBIDDEN_TIME_TOKENS = ("datetime", "time")


def _time_based_decision_offenders(source: str, function_name: str) -> list[str]:
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, (ast.If, ast.Compare, ast.BoolOp)):
                continue
            for name_node in ast.walk(sub):
                token = None
                if isinstance(name_node, ast.Name):
                    token = name_node.id
                elif isinstance(name_node, ast.Attribute):
                    token = name_node.attr
                if token and any(
                    forbidden in token.lower() for forbidden in _FORBIDDEN_TIME_TOKENS
                ):
                    offenders.append(f"{function_name}: 条件里出现标识符 {token}")
    return offenders


def test_the_time_decision_scanner_would_catch_a_violation():
    bad_source = """
def compute_criteria_transition(text, *, id, to, evidence, today):
    if datetime.datetime.now() > today:
        to = "已签认"
    return text, True
"""
    offenders = _time_based_decision_offenders(bad_source, "compute_criteria_transition")
    assert offenders, "扫描器应该报告 datetime.datetime.now() 参与的判断"


def test_compute_criteria_transition_has_no_time_based_state_decision():
    source = CRITERIA_MODULE.read_text(encoding="utf-8")
    offenders = _time_based_decision_offenders(source, "compute_criteria_transition")
    assert offenders == [], (
        "compute_criteria_transition 里出现了按时间判断的分支，"
        "违反 spec「无任何超期自动签认路径」：\n" + "\n".join(offenders)
    )
