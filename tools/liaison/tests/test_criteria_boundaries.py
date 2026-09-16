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


def _forbidden_option_hits(option_strings: set[str]) -> set[str]:
    """子串匹配：`--auto-signoff`、`--older-than-days` 这类"同类"变体
    与精确的 `--auto` 一样都要命中——spec 说的是"这一类"选项，不是这四个
    字面量本身。"""
    return {
        option
        for option in option_strings
        if any(forbidden in option for forbidden in _FORBIDDEN_OPTIONS)
    }


def test_the_argparse_scanner_would_catch_a_violation():
    import argparse

    bad_parser = argparse.ArgumentParser()
    bad_parser.add_argument("--auto", action="store_true")
    bad_parser.add_argument("--auto-signoff", action="store_true")
    bad_parser.add_argument("--older-than-days", type=int)

    option_strings = {
        option
        for action in bad_parser._actions
        for option in action.option_strings
    }
    hits = _forbidden_option_hits(option_strings)
    # 精确字面量 `--auto` 与相邻变体 `--auto-signoff`/`--older-than-days`
    # 都必须被报告——证明子串匹配确实比精确匹配更宽。
    assert "--auto" in hits, "扫描器应该报告精确的 --auto"
    assert "--auto-signoff" in hits, "扫描器应该报告 --auto 的相邻变体 --auto-signoff"
    assert "--older-than-days" in hits, "扫描器应该报告 --older-than 的相邻变体 --older-than-days"


def test_argparse_has_no_time_based_batch_options():
    from tools.liaison.unpack.criteria import build_parser

    option_strings = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
    }
    hits = _forbidden_option_hits(option_strings)
    assert hits == set(), f"argparse 出现了按时间批量操作的选项：{hits}"


# ── ③ 状态判断里不出现时间标识符 ──────────────────────────────────────────


#: ⚠️ `"today"` 是 `compute_criteria_transition` 自己的时钟参数名——2026-09-17
#: 补：只查 "datetime"/"time" 子串抓不住 `if (today - row_date).days > 30:`
#: 这种真实的自动超期改写形状（既不含 "datetime" 也不含 "time" 子串），
#: 于是这道闸对着这个最现实的违规形状永远绿灯。加入字面量 "today" 堵上这个洞；
#: 它只在**决策上下文节点**（If/Compare/BoolOp/BinOp）内部命中，`today.isoformat()`
#: 这种写进"更新"列的合法用法是裸 `Assign`，不在扫描范围内，不受影响。
_FORBIDDEN_TIME_TOKENS = ("datetime", "time", "today")


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
            if not isinstance(sub, (ast.If, ast.Compare, ast.BoolOp, ast.BinOp)):
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


def test_the_time_decision_scanner_catches_the_today_clock_param_shape():
    """证伪用例（补漏）：真实的自动超期改写形状不含 "datetime"/"time" 子串，
    只用调用方注入的时钟参数 `today` 做减法比较——不加固之前的扫描器对这个
    形状永远绿灯（见本文件顶部模块 docstring 与 `_FORBIDDEN_TIME_TOKENS`
    旁的注释）。"""
    bad_source = """
def compute_criteria_transition(text, *, id, to, evidence, today):
    if (today - row_date).days > 30:
        to = "已签认"
    return text, True
"""
    offenders = _time_based_decision_offenders(bad_source, "compute_criteria_transition")
    assert offenders, "扫描器应该报告 (today - row_date).days > 30 这种按时钟参数超期改写的分支"


def test_compute_criteria_transition_has_no_time_based_state_decision():
    source = CRITERIA_MODULE.read_text(encoding="utf-8")
    offenders = _time_based_decision_offenders(source, "compute_criteria_transition")
    assert offenders == [], (
        "compute_criteria_transition 里出现了按时间判断的分支，"
        "违反 spec「无任何超期自动签认路径」：\n" + "\n".join(offenders)
    )
