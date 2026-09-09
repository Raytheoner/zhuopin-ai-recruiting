"""源码扫描测试的公共排除判据——⛔ 单一真源，六个扫描点都引用这里。

**为什么必须有这个文件**：`test_liaison_effects.py` / `test_liaison_logsetup.py` /
`test_whitelist.py` 里有六处 `rglob("*.py")` 的静态扫描，它们的扫描根都是
`tools/liaison`。2026-09-09 在该目录下建起 `tools/liaison/.venv`（design D10 要求
aibot 只装在这里）之后，`site-packages` 里的第三方源码当场落进了这六个扫描根。

后果分两层，第二层才是真正危险的：

1. 直接可见的两处转红——`test_no_second_transaction_manager_in_source` 把
   `_pytest/_py/path.py::read_binary` 的 `with self.open('rb'):` 判成"隐式提交"，
   `test_setup_logging_is_wired_exactly_once_in_the_package` 把 pip vendored 的
   `base_command.py` / `_vendor/cachecontrol/_cmd.py` 数成 `setup_logging` 的调用点。
2. 另外四处**当时哑火**——判据碰巧没被现有依赖命中，但地雷已经埋好，装任何一个新
   依赖都可能踩响。

这两条断言分别守着**工程铁律 1（事务归属）**与**日志接线唯一性**。扫描根被污染后，
它们并没有失效，而是退化成"对第三方库稳定报一堆噪声"——闸门还在，但看闸门的人会
开始习惯性忽略它的报警。**这比闸门直接失效更危险，因为它不报错。**

⚠️ 本模块修的是"要扫哪些文件"（扫描根定义），⛔ **不碰任何一处的判据**
（"扫出来算不算违规"）。六处的严格度一个字都没放宽。

⛔ 文件名以 `_` 开头而非 `test_`，pytest 不会把它当用例文件收集。
"""

from __future__ import annotations

from pathlib import Path

#: 任何一段目录名命中即判定为"非本项目源码"。
#:
#: 同时认 `.venv` 与 `venv`——本项目约定是 `.venv`（`requirements.txt` 注释、design D10、
#: launchd 模板里的解释器路径都写死了它），但把 `venv` 一并挡住的成本为零，而漏挡的
#: 代价是六条闸门一起变噪声。`site-packages` 是兜底：venv 目录被改成别的名字时仍能命中。
_VENDORED_DIR_NAMES = frozenset({".venv", "venv", "site-packages"})


def is_vendored(path: Path) -> bool:
    """该路径是否落在虚拟环境／第三方包目录里。

    ⛔ 不用 `str(path).find(".venv")` 这类子串匹配——那会把
    `tools/liaison/.venv_notes/foo.py` 这种正常源码文件一起误杀。按路径**分段**判定。
    """
    return any(part in _VENDORED_DIR_NAMES for part in path.parts)
