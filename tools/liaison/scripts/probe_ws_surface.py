"""`aibot.WSClient` 的方法与事件名实测探针（WBS 7.6）。

第 1 章的探针只验了"建连能力齐备"（类与四个 options 字段在）。**接线还需要知道
两件它没验的事**：跑起来的那个方法叫什么、连接/断开事件叫什么。这两个名字猜错
的后果是静默的——服务起得来、日志正常，但断线事件永远到不了 LiaisonSession。

⛔ 本探针**只做内省**：不建连、不发消息、不需要任何真实凭据。

用法（在装了 SDK 的那个 venv 里）：

    PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_ws_surface
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys


def probe() -> dict:
    result: dict = {
        "python_version": sys.version.split()[0],
        "importable": False,
        "public_methods": [],
        "connect_like_signatures": {},
        "event_name_candidates": [],
        "base_classes": [],
        "import_error": None,
    }
    try:
        module = importlib.import_module("aibot")
    except Exception as exc:  # noqa: BLE001 —— 探针要如实记录任何 import 失败
        result["import_error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["importable"] = True
    client_cls = getattr(module, "WSClient", None)
    if client_cls is None:
        result["import_error"] = "aibot 里没有 WSClient"
        return result

    result["base_classes"] = [base.__module__ + "." + base.__qualname__ for base in client_cls.__mro__[1:]]
    result["public_methods"] = sorted(
        name for name in dir(client_cls) if not name.startswith("_")
    )
    for name in ("connect", "run", "start", "run_forever", "listen", "on", "add_listener"):
        member = getattr(client_cls, name, None)
        if member is None:
            continue
        try:
            result["connect_like_signatures"][name] = str(inspect.signature(member))
        except (TypeError, ValueError):
            result["connect_like_signatures"][name] = "<签名不可读>"

    # 事件名常量：SDK 常把它们放在模块级常量或枚举里。全量列出来由人核对，
    # ⛔ 探针不做"看起来像连接事件"这种猜测。
    result["event_name_candidates"] = sorted(
        name for name in dir(module)
        if name.isupper() or "EVENT" in name.upper()
    )
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
