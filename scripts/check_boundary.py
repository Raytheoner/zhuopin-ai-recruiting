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
import re
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

# 依赖声明文件。两个都查内容，只有 requirements.txt 查登记——理由见
# `check_registered_dependencies()` 的 docstring。
DEPENDENCY_FILES: tuple[str, ...] = ("requirements.txt", "pyproject.toml")
REGISTRY_GUARDED_FILE = "requirements.txt"

# ── 依赖登记表（2026-09-08 起）─────────────────────────────────────────────
#
# **判据从「相对立项 commit 的 diff 必须为空」改成了「每一条依赖都必须登记在
# 这里」。** 这是 TD-10 预告的触发点到期后的落地，⛔ 不是把检查放松了。
#
# 旧判据把基线钉死在立项 commit `e65f685`（2026-08-14），含义是"本变更包不许
# 加依赖"。变更包 2026-09-04 归档后这句话没有了指涉对象，而 CI step 对此后每
# 一次 push 仍然生效 ⇒ **任何一次正当新增依赖都会让所有分支永久变红**。
# 2026-09-08 加 `tzdata` 时它第一次红，与 TD-10 的预测逐字吻合。
#
# TD-10 给了两条改法，取第二条（显式登记制）：滚动基线仍要挂在某个历史 commit
# 上，且依赖本项目没有的发版 tag 纪律。⛔ TD-10 同时明令**不许图省事删掉这道
# 检查**——那会连同它已经在守护的边界一起丢掉，所以这里是换判据、不是退休。
#
# 新判据顺带修掉旧判据的两个毛病：① 不再需要 git，浅克隆的 `test` job 上不必
# 再 skip（旧的 skip 是真实存在的覆盖缺口）；② 覆盖**全部**依赖而不只是"新增
# 的那些"，既有依赖被人悄悄换名也会红。
#
# 🔴 **加依赖的正确姿势＝改两个文件**：`requirements.txt` 加行，这里补一条带
# 理由的登记。两处都动才是一次**刻意**的决定——这正是本判据要守的东西。
REGISTERED_DEPENDENCIES: dict[str, str] = {
    "fastapi": "Web 框架",
    "uvicorn": "ASGI 服务器；`.51` 计划任务直接起它",
    "pydantic": "schema 校验",
    "pydantic-settings": "配置加载",
    "langgraph": "L4 编排层；版本下限见 CLAUDE.md 工程铁律 7（GHSA-g48c-2wqr-h844）",
    "langgraph-checkpoint-sqlite": "M1 的 checkpointer；M2 迁 Postgres 时替换",
    "openai": "境内 LLM 的 OpenAI 兼容客户端（合规红线：模型全部走境内）",
    "python-dotenv": "读 `.env`",
    "tzdata": (
        "Windows 没有系统 IANA 时区库，stdlib zoneinfo 的唯一兜底。"
        "⛔ 别因为「没有任何代码 import 它」就删——删掉 `.51` 当场起不来，"
        "见 docs/findings/2026-09-08-51四次发版回滚.md"
    ),
    "pytest": "测试",
    "httpx": "测试用 HTTP 客户端（FastAPI TestClient 依赖）",
}

# `uvicorn[standard]==0.34.0` → `uvicorn`；`pytest>=8` → `pytest`。
# ⛔ 不要放宽成"取到第一个非字母就停"：`git+https://…` 这类行会被切成 `git`，
# 一条跨仓库直连依赖就此变成登记表里的合法名字。这里只认 PEP 508 的合法包名
# 起头，取不出名字的行按**原样**当作未登记名报出来（fail-closed）。
_REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[|[<>=!~;]|$)")

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


def parse_requirement_names(text: str) -> list[tuple[int, str]]:
    """把 requirements.txt 正文解析成 [(行号, 依赖名)]。

    跳过空行与整行注释；行尾注释按 PEP 508 只在 ` #` 处截断。取不出合法包名的
    非空行**原样返回**当作名字——让它去撞登记表并报红，⛔ 不要静默跳过：
    静默跳过等于给"看不懂的行"发免检通行证，而看不懂的行正是最该看的。
    """
    names: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT_NAME_RE.match(line)
        names.append((lineno, match.group(1).lower() if match else line))
    return names


def check_registered_dependencies(root: Path) -> list[Violation]:
    """7.2 后半：`requirements.txt` 里的每一条依赖都必须在登记表里。

    **判据只锁 `requirements.txt`，这是实测后的刻意收缩，不是漏写。**
    `pyproject.toml` 的依赖侧改由 `_scan_pyproject_dependency_tables()` 做结构性
    检查（"不得声明任何依赖表"），那条不会被无关的配置编辑打扰。

    读不到文件时**判违例**而不是通过——文件缺失由 `7.2-missing` 报，但这里
    自己也不能把"没读到"当成"没问题"（与 `assert_every_decision_has_human_review`
    的表不存在分支同一个方向：验不了不算守住了）。
    """
    path = root / REGISTRY_GUARDED_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            Violation(
                rule="7.2-unregistered",
                path=REGISTRY_GUARDED_FILE,
                line=0,
                message=f"读不到依赖声明文件：{exc}。⛔ 读不到不等于没问题。",
            )
        ]

    violations: list[Violation] = []
    for lineno, name in parse_requirement_names(text):
        if name in REGISTERED_DEPENDENCIES:
            continue
        violations.append(
            Violation(
                rule="7.2-unregistered",
                path=REGISTRY_GUARDED_FILE,
                line=lineno,
                message=(
                    f"依赖 `{name}` 未登记。新增依赖必须同时在 "
                    f"scripts/check_boundary.py 的 REGISTERED_DEPENDENCIES 里补一条"
                    f"带理由的登记——两处都动才是一次刻意的决定（TD-10）。"
                ),
            )
        )
    return violations


def run_all(root: Path) -> list[Violation]:
    """全部判据。**不再需要 git**——依赖判据自 2026-09-08 起只读工作区文件，
    所以浅克隆的 CI runner 上也能跑全套，旧的 `skip_diff` 开关随之取消。
    """
    return scan_app_tree(root) + scan_dependency_files(root) + check_registered_dependencies(root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="边界守护：禁止 zhuopin_platform 依赖与跨仓库注入（tasks.md 7.1/7.2）"
    )
    parser.add_argument("--root", default=".", help="仓库根目录，默认当前目录")
    ns = parser.parse_args(argv)

    root = Path(ns.root).resolve()
    violations = run_all(root)

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

    print(
        f"边界守护：通过（root={root}，"
        f"已登记依赖 {len(REGISTERED_DEPENDENCIES)} 条）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
