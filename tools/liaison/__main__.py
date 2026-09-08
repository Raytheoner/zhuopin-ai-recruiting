"""HR 值守通道服务的入口：`python -m tools.liaison`。

**第 1 章的入口只做一件事**：把 .env 填进进程环境 → 校验凭据 → 缺就退出码 2 退出。
建连、心跳、断线告警在第 7 章，⛔ 本章不实现、⛔ 不留一个假装在跑的空循环。

⛔ 模块层只 import 标准库与本服务自己的模块。任何 SDK import 都必须在凭据校验
**之后**、且写在函数体里——理由见 config.py 的模块 docstring。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from tools.liaison.config import load_credentials
from tools.liaison.errors import MissingCredentialsError

#: 缺凭据的退出码。选 2 而不是 1：1 太容易和"脚本里随便哪一步炸了"混在一起，
#: 2 让 launchd / 人工排障能一眼分辨出"这是配置没配好，不是程序崩了"。
EXIT_MISSING_CREDENTIALS = 2

#: tools/liaison/__main__.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 测试专用逃生口：让用例指一个不存在的 .env，免得开发机上真实的 .env 把
#: "凭据缺失"这条用例喂绿。⛔ 不写进 .env.example——那会把它暗示成生产用法。
DOTENV_PATH_ENV = "HR_LIAISON_DOTENV_PATH"


def resolve_dotenv_path() -> Path:
    override = os.environ.get(DOTENV_PATH_ENV)
    return Path(override) if override else REPO_ROOT / ".env"


def load_dotenv_into_environ(path: Path) -> None:
    """把 .env 的键值填进 os.environ，**已存在的环境变量不覆盖**。

    优先级口径与 app/config.py 用的 pydantic-settings 一致：进程环境 > .env 文件。
    ⛔ 不引入 python-dotenv：本服务依赖清单独立，标准库能解决的不加依赖，
    每多一个依赖就多一份"这东西会不会跟着被推到 .51"的疑问。

    文件不存在是正常情况（凭据也可以直接从进程环境给），静默返回。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # 允许 `export KEY=value` 这种 shell 习惯写法：与本文件的扫描器
        # test_liaison_no_secrets_in_vcs.py 的 _ASSIGNMENT 正则口径保持一致，
        # 否则真实的 export 行会被当成变量名叫 "export XXX" 的键，诊断信息误导人。
        key = re.sub(r"^export[ \t]+", "", key)
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    load_dotenv_into_environ(resolve_dotenv_path())

    try:
        credentials = load_credentials()
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。进程立刻退，⛔ 不进任何等待/重试循环。
        print(str(exc), file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    print(
        f"HR 值守通道：凭据校验通过（bot_id={credentials.bot_id}）。"
        f"第 1 章到此为止——建连与消息处理在第 7 章，本章刻意不驻留。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
