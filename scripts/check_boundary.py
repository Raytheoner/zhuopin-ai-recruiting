#!/usr/bin/env python3
"""边界守护 —— `tasks.md` 7.1 / 7.2 的机器化。

本包（`ai-audit-trail-and-outbound-gate`）的三条硬边界写在
`delivery-units.md` §4 约定 7：**不新增 `zhuopin_platform` 依赖、不跨仓库
import、不拷贝参考文件**。`06-企业AI转型资产借鉴清单.md` §10.3 在 2026-08-28
用两条命令人工核验过一次，但那只是**那一刻**的结论——本脚本把同样的两条判据
变成 CI 每次都跑的机器检查。

⚠️ 判据刻意比 tasks.md 的字面更严，**三处**（token 扫全文含非 `.py` 文件、
`app/` 下任何 `sys.path` 访问、三个姊妹仓库 marker 独立成判据），另有若干条
**完整性**判据（`7.1-missing` / `7.1-symlink` / `7.2-missing` / 非 UTF-8 兜底
解码）防止检查恒真。理由都写在各自的函数 docstring 里，放宽之前先读那段。

用法（退出码 0 = 全过，1 = 有违例，2 = 用法错误）：

    python scripts/check_boundary.py
    python scripts/check_boundary.py --root /path/to/repo --skip-diff
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

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


def _walk(base: Path) -> tuple[list[Path], list[Path]]:
    """遍历 `base`，返回 `(普通文件, symlink 条目)`。

    ⚠️ **两处都是为了不产生静默的扫描盲区：**

    1. `SKIP_DIR_NAMES` 只对 **`base` 以内**的目录名生效。旧实现用
       `SKIP_DIR_NAMES & set(path.parts)` 匹配的是**绝对路径**的全部路径段——
       把仓库放在一个名叫 `.mypy_cache` 的祖先目录下（CI 缓存目录里 clone
       就会这样），扫描产出零文件，带着真实违例全绿。
    2. ⛔ **不跟随 symlink**（`os.walk(followlinks=False)`）。跟随会让一个
       指回姊妹仓库的软链把整个外部仓库拖进扫描范围，且可能成环；这里的做法
       是**不跟随、但把 symlink 本身报成违例**，见 `scan_app_tree()` 第 4 条。
       Python 3.13+ 的 `Path.rglob` 默认也不跟随，且 symlink 目录过不了
       `is_file()` —— 于是旧实现对目录软链**完全不可见**。
    """
    files: list[Path] = []
    links: list[Path] = []
    if not base.is_dir():
        return files, links

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES)
        for name in dirnames:
            path = here / name
            if path.is_symlink():
                links.append(path)
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink():
                links.append(path)
            # symlink 指向的普通文件照旧读内容（多一条判据不吃亏）；
            # 指向目录或断链的 `is_file()` 为假，只留 symlink 违例。
            if path.is_file():
                files.append(path)
    return files, sorted(links)


def _decode(raw: bytes) -> str:
    """尽力把字节解成文本；**⛔ 不许在解不动时静默返回空**。

    ⚠️ 旧实现把 `UnicodeDecodeError` 吞成 `None`、调用方直接 `continue`，
    于是 `app/` 下任何非 UTF-8 文件对这条边界**永久失明且不留痕迹**。这不是
    假想的触发条件：部署目标 `.51` 是 Windows，PowerShell 的 `Out-File` / `>`
    默认写 **UTF-16LE**，在服务器上顺手改一个 `app/` 下的文件即可复现
    （GBK 代码页的旧账见 `ci.yml` 顶部注释）。

    ⚠️ **两个坑，踩中任何一个这条修法都是白修：**

    1. **只加 `errors="replace"` 修不好 UTF-16。** UTF-16LE 的 ASCII 文本按
       UTF-8 解，每个字符之间夹着 `\\x00`，`"zhuopin_platform" in line`
       匹配不上。
    2. **无 BOM 的 UTF-16 根本不会抛 `UnicodeDecodeError`。** 纯 ASCII 正文
       编成 UTF-16 后只有 ASCII 字节和 NUL，而 NUL 是**合法的 UTF-8**——
       `raw.decode("utf-8")` 直接成功，返回一串夹着 NUL 的字符串。所以
       "解码失败才兜底"是不够的，**出口统一剔 NUL** 才拦得住。

    ⚠️ **已知限制（⛔ 不要假装它全覆盖）**：latin-1 兜底只保证 **ASCII
    token**（`zhuopin_platform` / `OneDrive` / `zhuopin-ai-transformation`）
    能匹配；`FORBIDDEN_PATH_MARKERS` 里的中文 marker `企业AI转型` 在兜底路径
    （无 BOM 的 UTF-16 中文、以及既非 UTF-8 也非 GBK 的编码）上匹配不到。
    UTF-8、带 BOM 的 UTF-16、GBK 三种走正解路径，中文 marker 正常生效。
    """
    return _decode_bytes(raw).replace("\x00", "")


def _decode_bytes(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        # UTF-16 带 BOM（Windows PowerShell `Out-File` 的默认形态）：BOM 定字节序，
        # 能正解，中文 marker 在这条路径上照常生效。
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            pass
    try:
        # 无 BOM 的 UTF-16（大端小端都有可能，⛔ 不能靠 `decode("utf-16")` 猜——
        # 猜反了得到的是一串看似成功的 CJK 乱码）在这里就"成功"了，靠 `_decode()`
        # 出口那步剔 NUL 还原成可匹配的 ASCII。
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if b"\x00" not in raw:
        # 中文 Windows 的老代码页。GBK 正解得到的中文能匹配中文 marker。
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            pass
    # 兜底：latin-1 不会抛异常，ASCII token 仍可匹配（中文 marker 不保证）。
    return raw.decode("latin-1", errors="replace")


def _read_text(path: Path) -> str | None:
    """返回文本；只有**读不到**（OSError）才返回 None，编码问题一律兜底解。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return _decode(raw)


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

    3. **三个姊妹仓库 marker（`FORBIDDEN_PATH_MARKERS`）独立成判据
       （`7.1-path`），不与 `sys.path` 合取。** tasks.md 7.1 的字面只禁
       "`sys.path` **指向 OneDrive 路径**的注入"，本实现把 marker 拆出来单算：
       `app/` 下任何文件任何一行出现这三个词即违例。跨仓库耦合不止 `sys.path`
       一条路（一行 `open("…/OneDrive/…/企业AI转型/…")` 同样是耦合）。
       ⚠️ **这是全脚本误报风险最高的一条**：CLAUDE.md 自己就写着"本项目本来
       就是**企业AI转型**的部门模块之一"，谁在 `app/` 的 docstring 里照抄这句
       交代出身，CI 就会指控他跨仓库耦合。真出现时的正确处置是**把那句话搬去
       `docs/`**，⛔ 不是给这条加白名单。

    上面 1 顺带**扫全部文件不只 `.py`**——`index.html` 里的一行 fetch 也能
    跨仓库；`sys.path` 那条走 AST，只对 `.py` 生效。

    另有两条**完整性**判据，不是"从严"而是防止本检查恒真：

    4. **`app/` 下出现任何 symlink 即违例（`7.1-symlink`）。** 一个软链进姊妹
       仓库（`app/sister -> …/企业AI转型`）是"不跨仓库引用、不拷贝参考文件"
       最字面的违反形态，而 `Path.rglob` 对**目录**软链完全不可见。今天 `app/`
       下零存量 symlink（已实测），从严的误报成本是零。
    5. **`app/` 目录不存在即违例（`7.1-missing`）**，与 `7.2-missing` 对称。
       仓库重构或 CLI 从错误 cwd 起跑时，"没有文件可扫"会被读成"没有违例"。
    """
    violations: list[Violation] = []
    app_dir = root / "app"

    if not app_dir.is_dir():
        return [
            Violation(
                rule="7.1-missing",
                path="app",
                line=0,
                message=(
                    "app/ 目录不存在，7.1 无从核验（仓库重构、改名，"
                    "或 CLI 从错误的 cwd 起跑？）。⛔ 空扫描不等于没有违例"
                ),
            )
        ]

    files, links = _walk(app_dir)

    for link in links:
        violations.append(
            Violation(
                rule="7.1-symlink",
                path=link.relative_to(root).as_posix(),
                line=0,
                message=(
                    f"app/ 下出现 symlink（指向 {os.readlink(link)!r}）。"
                    "⛔ 禁止任何软链——软链进姊妹仓库既绕过内容扫描"
                    "（不跟随 symlink），本身也是跨仓库引用"
                ),
            )
        )

    for path in files:
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


def scan_dependency_files(root: Path) -> list[Violation]:
    """7.2 前半：`requirements.txt` 与 `pyproject.toml` 不含 `zhuopin_platform`。

    另加一条 `pyproject.toml` 的**结构性**检查：不得声明任何运行时依赖表。
    理由见 `check_dependency_diff()` —— diff 判据只锁 `requirements.txt`，
    若不补这条，往 `[project] dependencies` 里加一行依赖就能整条溜过去。

    `FORBIDDEN_PATH_MARKERS` 也在依赖文件上跑一遍（`7.2-path`）：一行
    `git+https://github.com/Raytheoner/zhuopin-ai-transformation.git@master`
    不含 `zhuopin_platform` 这个 token，只靠 diff 判据拦——而 diff 判据是有
    保质期的（钉死基线），marker 判据没有。
    """
    violations: list[Violation] = []

    for name in DEPENDENCY_FILES:
        path = root / name
        if not path.exists():
            violations.append(
                Violation(
                    rule="7.2-missing",
                    path=name,
                    line=0,
                    message="依赖声明文件不存在，无法核验边界（被删除或改名了？）",
                )
            )
            continue
        text = _read_text(path)
        if text is None:
            # ⛔ 旧写法 `_read_text(path) or ""` 会把读不到的依赖文件当成空文件，
            # 于是"读不出来"＝"全绿"。读不到本身就是违例。
            violations.append(
                Violation(
                    rule="7.2-unreadable",
                    path=name,
                    line=0,
                    message="依赖声明文件读不出内容，无法核验边界（权限？是目录？）",
                )
            )
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_MODULE in line:
                violations.append(
                    Violation(
                        rule="7.2-module",
                        path=name,
                        line=lineno,
                        message=f"依赖声明里出现 {FORBIDDEN_MODULE!r}",
                    )
                )
            for marker in FORBIDDEN_PATH_MARKERS:
                if marker in line:
                    violations.append(
                        Violation(
                            rule="7.2-path",
                            path=name,
                            line=lineno,
                            message=(
                                f"依赖声明里出现姊妹仓库路径特征 {marker!r}"
                                "（VCS 直装 / 本地路径依赖？）。"
                                "⛔ 不跨仓库引用，读取参考 + 本仓库自建实现"
                            ),
                        )
                    )

    violations.extend(_scan_pyproject_dependency_tables(root))
    return violations


def _scan_pyproject_dependency_tables(root: Path) -> list[Violation]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    text = _read_text(path)
    if text is None:
        return [
            Violation(
                rule="7.2-pyproject",
                path="pyproject.toml",
                line=0,
                message="读不出内容，无法核验依赖表",
            )
        ]
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return [
            Violation(
                rule="7.2-pyproject",
                path="pyproject.toml",
                line=0,
                message=f"解析失败，无法核验依赖表：{exc}",
            )
        ]

    project = data.get("project", {})
    tables: list[tuple[str, object]] = [
        ("project.dependencies", project.get("dependencies")),
        ("project.optional-dependencies", project.get("optional-dependencies")),
        ("dependency-groups", data.get("dependency-groups")),
        (
            "tool.poetry.dependencies",
            data.get("tool", {}).get("poetry", {}).get("dependencies"),
        ),
    ]

    violations: list[Violation] = []
    for label, value in tables:
        if value:
            violations.append(
                Violation(
                    rule="7.2-pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=(
                        f"{label} 非空。本仓库的依赖真源是 requirements.txt，"
                        "pyproject.toml ⛔ 不声明依赖——否则 diff 判据只锁 "
                        "requirements.txt 就留下一个绕过口"
                    ),
                )
            )
    return violations


def _default_runner(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def check_dependency_diff(
    root: Path,
    baseline: str = BASELINE_COMMIT,
    runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _default_runner,
) -> list[Violation]:
    """7.2 后半：本变更的依赖文件 diff 必须为空。

    **判据只锁 `requirements.txt`，这是实测后的刻意收缩，不是漏写。**
    `pyproject.toml` 自立项以来确有 6 行 diff——U6 往 `[tool.pytest.ini_options]`
    加了 `markers` 段（合规断言的 pytest 标记）。那是测试配置，不是依赖。把
    `pyproject.toml` 一起纳入 diff 判据，这条检查从上线第一天就是红的，
    ⇒ 必然被加白名单或整条注释掉。**恒假的检查和恒真的检查一样没用。**
    `pyproject.toml` 的依赖侧改由 `_scan_pyproject_dependency_tables()`
    做结构性检查，那条不会被无关的配置编辑打扰。

    `runner` 参数是为了测试能注入非空 diff 做反证——真实仓库里这条永远是绿的，
    只靠真实仓库测不出它会不会红。
    """
    args = ["git", "diff", "--stat", f"{baseline}..HEAD", "--", *DIFF_GUARDED_FILES]
    result = runner(args, root)

    if result.returncode != 0:
        return [
            Violation(
                rule="7.2-diff",
                path=" ".join(DIFF_GUARDED_FILES),
                line=0,
                message=(
                    f"`{' '.join(args)}` 退出码 {result.returncode}："
                    f"{result.stderr.strip() or '无 stderr'}。"
                    "CI 上最常见的原因是 checkout 深度不足取不到基线 commit，"
                    "该 job 需要 fetch-depth: 0"
                ),
            )
        ]

    if result.stdout.strip():
        return [
            Violation(
                rule="7.2-diff",
                path=" ".join(DIFF_GUARDED_FILES),
                line=0,
                message=(
                    f"自基线 {baseline[:7]} 起依赖文件有改动，本变更不得新增依赖：\n"
                    + result.stdout.rstrip()
                ),
            )
        ]

    return []


def run_all(
    root: Path,
    baseline: str = BASELINE_COMMIT,
    skip_diff: bool = False,
    runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _default_runner,
) -> list[Violation]:
    violations = scan_app_tree(root) + scan_dependency_files(root)
    if not skip_diff:
        violations += check_dependency_diff(root, baseline=baseline, runner=runner)
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="边界守护：禁止 zhuopin_platform 依赖与跨仓库注入（tasks.md 7.1/7.2）"
    )
    parser.add_argument("--root", default=".", help="仓库根目录，默认当前目录")
    parser.add_argument(
        "--baseline", default=BASELINE_COMMIT, help="依赖 diff 的基线 commit"
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="跳过 git diff 判据（无 git 历史的临时目录里用）",
    )
    ns = parser.parse_args(argv)

    root = Path(ns.root).resolve()
    violations = run_all(root, baseline=ns.baseline, skip_diff=ns.skip_diff)

    if violations:
        print(f"边界守护：{len(violations)} 条违例", file=sys.stderr)
        for v in violations:
            print("  " + v.render(), file=sys.stderr)
        print(
            "\n本包硬边界见 delivery-units.md §4 约定 7 与 CLAUDE.md"
            "「已迁 GitHub，只读参考」：读取参考 + 在本仓库自建实现，"
            "⛔ 不 clone、不跨仓库引用、不拷贝文件。",
            file=sys.stderr,
        )
        return 1

    print(f"边界守护：通过（root={root}，基线={ns.baseline[:7]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
