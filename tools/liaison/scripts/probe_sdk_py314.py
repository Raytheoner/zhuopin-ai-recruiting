"""`wecom-aibot-python-sdk` 在 Python 3.14 上的兼容性探针（WBS 1.2）。

**为什么需要一个脚本而不是手敲几条命令**：这个结论要落进 findings 文档、要成为
路线 ①／② 的判定依据，而"我在终端里试过、能跑"是不可复核的。脚本把三条判据
（可安装 / 可 import / 建连能力齐备）固定下来并吐一份机器可读的 JSON，任何人
重跑都能拿到同一份结论。

用法（在**已经装好 SDK 的那个 venv 的解释器**里跑）：

    tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_sdk_py314

⛔ 本脚本不自己装包、不自己建 venv——安装本身是判据 A，必须在脚本外面跑，
它的成败要由 pip 的退出码直接说了算，不能被脚本的 try/except 吞掉。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys

DISTRIBUTION_NAME = "wecom-aibot-python-sdk"

# ⚠️ 发行名与顶层模块名**不一致**：PyPI 上叫 wecom-aibot-python-sdk，import 进来叫
# aibot。两个候选都试，避免"import 名猜错"被误判成"SDK 不可用"而错走路线 ②。
CANDIDATE_MODULE_NAMES = ("aibot", "wecom_aibot_python_sdk")

# 判据 C：建连、心跳、重连三样都要能配，缺一说明这份 SDK 撑不起 spec
# 「断线后自动恢复接收」那条要求，只能走路线 ②。
REQUIRED_ATTRS = ("WSClient", "WSClientOptions")
REQUIRED_OPTION_FIELDS = ("bot_id", "secret", "heartbeat_interval", "max_reconnect_attempts")


def probe() -> dict:
    result: dict = {
        "python_version": sys.version.split()[0],
        "distribution_name": DISTRIBUTION_NAME,
        "distribution_version": None,
        "module_name": None,
        "module_version_attr": None,
        "importable": False,
        "has_ws_client": False,
        "missing_attrs": [],
        "missing_option_fields": [],
        "import_error": None,
    }

    try:
        result["distribution_version"] = importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        result["import_error"] = f"发行包未安装: {DISTRIBUTION_NAME}"
        return result

    module = None
    errors = []
    for name in CANDIDATE_MODULE_NAMES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 —— 探针要如实记录任何 import 失败
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        result["module_name"] = name
        result["importable"] = True
        break

    if module is None:
        result["import_error"] = " | ".join(errors)
        return result

    # ⚠️ 模块的 __version__ 与发行版本可能对不上（实测 1.0.2 的包里写着 1.0.0）。
    # 两个都记下来，findings 里以**发行版本**为准——requirements.txt 钉的是发行版本。
    result["module_version_attr"] = getattr(module, "__version__", None)

    result["missing_attrs"] = [a for a in REQUIRED_ATTRS if not hasattr(module, a)]
    if not result["missing_attrs"]:
        options_cls = getattr(module, "WSClientOptions")
        annotations = getattr(options_cls, "__annotations__", {})
        result["missing_option_fields"] = [
            f for f in REQUIRED_OPTION_FIELDS if f not in annotations
        ]
    else:
        result["missing_option_fields"] = list(REQUIRED_OPTION_FIELDS)

    result["has_ws_client"] = (
        not result["missing_attrs"] and not result["missing_option_fields"]
    )
    return result


def main() -> int:
    result = probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 退出码即结论：0 = 判据 B+C 通过（判据 A 由外层 pip 的退出码负责）。
    return 0 if (result["importable"] and result["has_ws_client"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
