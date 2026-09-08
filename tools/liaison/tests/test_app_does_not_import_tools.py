"""结构性单向依赖断言：`app/` ⛔ 不得 import `tools/`（design D5）。

用 AST 而不是 grep：grep 会把注释、docstring、字符串字面量里的 "import tools"
一起算进来（本仓库的中文注释里就有大量提到 tools 的句子），产生假阳性；
也会漏掉 `importlib.import_module("tools.x")` 这类写法——后者由本文件的
第二条断言单独覆盖。

⛔ 断言失败时不要把违规模块加进豁免名单（本文件刻意没有豁免名单）。
`app/` 会被 sync-to-server.sh 推到 .51，`tools/` 不会。这条依赖在 .51 上必然是
ModuleNotFoundError，而且只在执行到那一行时才炸——测试环境全绿、现网崩。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

# tools/liaison/tests/test_x.py → parents[0]=tests, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "app"


def _app_modules() -> list[pathlib.Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _static_import_offenders(modules: list[pathlib.Path]) -> list[str]:
    """对给定的模块列表做静态 AST 扫描，返回违反 D5 单向依赖的 import 记录。

    抽成独立函数、以模块列表为入参（而不是内部直接调用 `_app_modules()`），
    是为了让 `test_no_app_module_imports_tools`（真实 app/ 模块）与下面的
    monkeypatch 证伪测试共享同一份扫描逻辑，而不是各写一份、扫描逻辑本身
    出了 bug 时两边同时不报。
    """
    offenders: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tools" or alias.name.startswith("tools."):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # node.level > 0 是相对 import（from . import x），
                # 相对 import 出不了 app/ 包，不可能指到 tools，跳过。
                if node.level == 0 and (module == "tools" or module.startswith("tools.")):
                    offenders.append(f"{path}: from {module} import ...")
    return offenders


def test_app_has_modules_to_scan():
    """先证明扫描范围非空——空目录会让下面两条断言变成永远为真的摆设。"""
    modules = _app_modules()
    assert len(modules) >= 40, f"app/ 下只扫到 {len(modules)} 个模块，扫描范围可能不对"


def test_no_app_module_imports_tools():
    """静态 import：`import tools.x` 与 `from tools.x import y` 都要抓。"""
    offenders = _static_import_offenders(_app_modules())
    assert offenders == [], (
        "app/ 下出现了对 tools/ 的 import，违反 design D5 的单向依赖：\n"
        + "\n".join(offenders)
    )


def test_no_app_module_imports_tools_dynamically():
    """动态 import：`importlib.import_module("tools.x")` / `__import__("tools.x")`。

    静态扫描抓不到它们，但它们在 .51 上一样炸。
    """
    offenders: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name not in ("import_module", "__import__"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == "tools" or arg.value.startswith("tools."):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}: {name}({arg.value!r})"
                        )
    assert offenders == [], "app/ 下动态 import 了 tools/：\n" + "\n".join(offenders)


def test_the_scanner_would_catch_a_violation(tmp_path):
    """对扫描器本身的证伪：给它一个真的违规文件，它必须报出来。

    ⛔ 不要删这条。上面三条在"扫描器写错了"的情况下会永远绿，
    而"永远绿的结构断言"比没有断言更危险——它会让人以为约束被守住了。
    """
    bad = tmp_path / "bad_module.py"
    bad.write_text("from tools.liaison.storage import db\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    hits = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and (node.module or "").startswith("tools.")
    ]
    assert hits == ["tools.liaison.storage"]


def test_scanner_catches_a_synthetic_violation_via_monkeypatched_app_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """常驻证伪：monkeypatch `_app_modules()`，验证 `test_no_app_module_imports_tools`
    实际依赖的扫描函数在遇到真违规时真的会报出来、且带得出模块路径。

    这条替代的是 brief 原 Step 3（在 `app/` 下手工造 `_tmp_violation.py` 再删除的
    一次性证伪步骤）——本 Task 的 opener 明令：结构断言要能抓到故意在 `app/`
    里加一行 `import tools.liaison` 的情形，要求用「测试进程内 monkeypatch」，
    ⛔ 不改 `app/` 下任何文件（哪怕随后删除）。opener 优先于 brief，这里改成
    monkeypatch `_app_modules()` 让它返回一个落在 tmp_path 下的合成违规模块，
    全程不落一个字节到 `app/`。

    ⛔ 不要删这条：它是常驻回归测试，不是一次性手工验证步骤，对齐
    `test_liaison_effects.py::test_identity_scaffold_actually_catches_a_break`
    的写法——"能被证伪的断言"本身也要被测试覆盖，否则扫描逻辑写错时
    上面几条会永远绿，比没有断言更危险。
    """
    synthetic_violation = tmp_path / "synthetic_violation.py"
    synthetic_violation.write_text(
        "import tools.liaison.storage.db\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "tools.liaison.tests.test_app_does_not_import_tools._app_modules",
        lambda: [synthetic_violation],
    )

    # 直接调用 test_no_app_module_imports_tools 依赖的那份扫描逻辑（通过
    # 已被 monkeypatch 的 _app_modules() 间接拿到合成违规），而不是重新
    # 发明一份独立的断言逻辑——这样才是在验证"真正被用的那条路径"。
    offenders = _static_import_offenders(_app_modules())
    assert offenders == [f"{synthetic_violation}: import tools.liaison.storage.db"]

    # 再从被测函数本身的角度确认一遍：把同一次 monkeypatch 套在
    # test_no_app_module_imports_tools() 上直接跑，必须真的红，
    # 且违规信息里带得出合成模块的路径。
    with pytest.raises(AssertionError, match=re.escape(str(synthetic_violation))):
        test_no_app_module_imports_tools()
