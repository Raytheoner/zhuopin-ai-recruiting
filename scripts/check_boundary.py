#!/usr/bin/env python3
"""边界守护 —— `tasks.md` 7.1 / 7.2 的机器化。

本包（`ai-audit-trail-and-outbound-gate`）的三条硬边界写在
`delivery-units.md` §4 约定 7：**不新增 `zhuopin_platform` 依赖、不跨仓库
import、不拷贝参考文件**。`06-企业AI转型资产借鉴清单.md` §10.3 在 2026-08-28
用两条命令人工核验过一次，但那只是**那一刻**的结论——本脚本把同样的两条判据
变成 CI 每次都跑的机器检查。

⚠️ 判据刻意比 tasks.md 的字面更严，三处，都在下面各自的函数 docstring 里
说明了理由。放宽任何一处之前先读那段。

用法（退出码 0 = 全过，1 = 有违例，2 = 用法错误）：

    python scripts/check_boundary.py
    python scripts/check_boundary.py --root /path/to/repo --skip-diff
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

# ── 判据常量（改这里，⛔ 不要把字面量散到各个函数里）────────────────────────

FORBIDDEN_MODULE = "zhuopin_platform"

# 姊妹项目 `企业AI转型` 的路径特征。2026-08-26 它已迁 GitHub，旧的 OneDrive
# 本地路径作废（CLAUDE.md「已迁 GitHub，只读参考」），任何指回去的路径都是
# 死链 + 跨仓库耦合，两条都不许有。
FORBIDDEN_PATH_MARKERS: tuple[str, ...] = (
    "OneDrive",
    "企业AI转型",
    "zhuopin-ai-transformation",
)

# 依赖声明文件。两个都查内容，只有 requirements.txt 查 diff——理由见
# `check_dependency_diff()` 的 docstring。
DEPENDENCY_FILES: tuple[str, ...] = ("requirements.txt", "pyproject.toml")
DIFF_GUARDED_FILES: tuple[str, ...] = ("requirements.txt",)

# 本变更包的起点＝立项 commit `e65f685 docs: AI 评分留痕与外发人工确认门禁——立项`
# （2026-08-14）。写死是刻意的：写成 `origin/main` 会随 main 前进而漂移，
# 「本变更没加依赖」这句话就失去了固定的参照物。
BASELINE_COMMIT = "e65f6857fe255634d49a3e8696b1dba0f5facbec"

SKIP_DIR_NAMES = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache"})


@dataclass(frozen=True)
class Violation:
    """一条违例。`line` 为 0 表示该规则不针对具体行。"""

    rule: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.rule}] {where}: {self.message}"


def _iter_files(base: Path) -> Iterable[Path]:
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if SKIP_DIR_NAMES & set(path.parts):
            continue
        yield path


def _read_text(path: Path) -> str | None:
    """读不成 UTF-8 文本的（图片等二进制）返回 None，由调用方跳过。"""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _sys_path_mutation_lines(source: str, filename: str = "<app>") -> list[int]:
    """AST 找 `sys.path` 的写入点。

    ⚠️ 用 AST 不用 grep：`app/` 里出现一句解释这条规则的注释或 docstring
    是完全合理的，文本扫描会把它判成违例，于是这条检查第一次误报之后就会被
    人放宽——**误报是检查被拆掉的最常见死因**。AST 只看真实的属性访问。

    ⚠️ `catch_warnings` 不是可有可无的：`app/outbound/delivery.py:12` 的
    docstring 里有一条 Windows 路径 `C:\\apps\\...\\data\\...`，重新 parse
    会抛 `SyntaxWarning: "\\z" is an invalid escape sequence`。那是被扫文件
    自己的事，⛔ 不该由这条边界检查在 CI 日志里再喊一遍——检查的输出里混进
    与检查无关的噪音，下一个人就学会了忽略它的输出。
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines: list[int] = []
    for node in ast.walk(tree):
        # import sys; sys.path.insert(...) / sys.path += [...] / sys.path = [...]
        if isinstance(node, ast.Attribute) and node.attr == "path":
            value = node.value
            if isinstance(value, ast.Name) and value.id == "sys":
                lines.append(node.lineno)
        # from sys import path
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            if any(alias.name == "path" for alias in node.names):
                lines.append(node.lineno)
    return sorted(set(lines))


def scan_app_tree(root: Path) -> list[Violation]:
    """7.1：`app/` 下禁止 `zhuopin_platform`，禁止 `sys.path` 注入。

    三处刻意从严，都不是笔误：

    1. **`zhuopin_platform` 按裸 token 扫全文，不只扫 import 语句。** 判据与
       `06-企业AI转型资产借鉴清单.md` §10.3 判据二逐字同源
       （`grep -rn "zhuopin_platform" app/` 退出码必须是 1）。副作用是
       `app/` 里连提一句这个名字的注释都不许写——接受，注释该写在
       `docs/` 或本脚本里。

    2. **`app/` 下任何 `sys.path` 写入都算违例，不只是"指向 OneDrive 的"。**
       tasks.md 7.1 的字面是二者的合取，但合取可以被一个变量绕开
       （`p = os.environ["SISTER_REPO"]; sys.path.insert(0, p)` —— 没有任何
       OneDrive 字样，照样把姊妹仓库挂进来）。`app/` 今天 `sys.path` 零命中
       （已实测），从严不会产生任何存量误报，收紧的成本是零。

    3. **扫全部文件不只 `.py`。** `index.html` 里的一行 fetch 也能跨仓库。
    """
    violations: list[Violation] = []
    app_dir = root / "app"

    for path in _iter_files(app_dir):
        text = _read_text(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()

        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_MODULE in line:
                violations.append(
                    Violation(
                        rule="7.1-module",
                        path=rel,
                        line=lineno,
                        message=(
                            f"出现 {FORBIDDEN_MODULE!r}。本包硬边界：不新增该依赖、"
                            "不跨仓库 import（delivery-units.md §4 约定 7）"
                        ),
                    )
                )
            for marker in FORBIDDEN_PATH_MARKERS:
                if marker in line:
                    violations.append(
                        Violation(
                            rule="7.1-path",
                            path=rel,
                            line=lineno,
                            message=(
                                f"出现姊妹仓库路径特征 {marker!r}。"
                                "企业AI转型已迁 GitHub 只读参考，⛔ 不跨仓库引用"
                            ),
                        )
                    )

        if path.suffix == ".py":
            for lineno in _sys_path_mutation_lines(text, filename=rel):
                violations.append(
                    Violation(
                        rule="7.1-syspath",
                        path=rel,
                        line=lineno,
                        message=(
                            "app/ 下出现 sys.path 访问。⛔ 禁止任何 sys.path 注入"
                            "——合取式判据（仅禁 OneDrive 字样）可被一个变量绕开"
                        ),
                    )
                )

    return violations
