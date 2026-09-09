"""把 `netguard/sitecustomize.py` 那道闸门装到**进程内**与**子进程**里。

⛔ 单一真源：闸门本体只有 `netguard/sitecustomize.py` 一份，这里 ⛔ 不重写一遍判据。
本模块只做两件事——按路径把它 import 进来（进程内），以及把它所在目录塞进子进程的
`PYTHONPATH`（`site` 会在子进程启动时自动 import 名为 `sitecustomize` 的顶层模块）。

⛔ 文件名不以 `test_` 开头，pytest 不会把它当用例文件收集。
"""

from __future__ import annotations

import importlib.util
import os
import pathlib

GUARD_DIR = pathlib.Path(__file__).resolve().parent / "netguard"
GUARD_SOURCE = GUARD_DIR / "sitecustomize.py"


def _load():
    spec = importlib.util.spec_from_file_location("_liaison_netguard", GUARD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # import 时即 install()
    return module


_module = _load()

NetworkAccessInTestError = _module.NetworkAccessInTestError
install = _module.install
uninstall = _module.uninstall

# import 本模块时闸门已被装上一次（`_load()` 执行了模块体里的 `install()`）。
# 进程内的开关交给 conftest 的 autouse fixture，这里先摘掉，⛔ 不留全局副作用。
uninstall()


def subprocess_env(env: dict) -> dict:
    """把闸门目录**前置**进 `PYTHONPATH`，返回同一个 dict（就地改）。

    ⚠️ 必须前置而不是追加：真源仓库根若哪天出现同名 `sitecustomize.py`，追加会让
    闸门被顶掉，而那种失效 ⛔ 不报错——测试照常绿，只是又开始触网了。
    """
    existing = env.get("PYTHONPATH", "")
    parts = [str(GUARD_DIR)] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # ⚠️ 代理变量在这里就摘掉一遍（子进程里 sitecustomize 还会再摘一次）：
    # 开发机上 `HTTPS_PROXY=http://127.0.0.1:<port>` 会让建连打到回环、被闸门放行、
    # 再由代理转发出去——闸门形同虚设。⛔ 不能只靠回环判据。理由见 sitecustomize.py。
    for name in _module._PROXY_ENV_NAMES:
        env.pop(name, None)
    return env
