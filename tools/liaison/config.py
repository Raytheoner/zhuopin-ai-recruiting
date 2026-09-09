"""启动期凭据读取与校验（fail-closed）。

spec liaison-channel-session「凭据缺失时拒绝启动」要求：任一项缺失、为空或只含
空白字符时，服务必须拒绝启动并指明缺哪一项，⛔ 不得以"已启动但收不到消息"的
状态继续运行。

**只用标准库。** 启动路径上的模块不许 import 任何第三方包——跑全量 pytest 的根
venv 里没有本服务的依赖（design D10 的依赖隔离），一旦依赖它们，这条 fail-closed
行为在根 venv 里就测不出来了。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from tools.liaison.errors import MissingCredentialsError

BOT_ID_ENV = "HR_LIAISON_BOT_ID"
BOT_SECRET_ENV = "HR_LIAISON_BOT_SECRET"

#: 启动前必须齐备的凭据。顺序即报错里的列举顺序，保持稳定便于比对。
REQUIRED_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (BOT_ID_ENV, BOT_SECRET_ENV)


@dataclass(frozen=True, repr=False)
class LiaisonCredentials:
    """一组已校验过的凭据。

    `repr=False` + 自写 `__repr__` 是刻意的：dataclass 默认生成的 `__repr__` 会把
    secret 原样打出来，而 repr 会出现在日志、traceback 与 pytest 断言里。
    `bot_id` 不是秘密（排障要看得见），`bot_secret` 一律遮蔽。
    """

    bot_id: str
    bot_secret: str

    def __repr__(self) -> str:
        return f"LiaisonCredentials(bot_id={self.bot_id!r}, bot_secret=<redacted>)"


def _is_blank(env: Mapping[str, str], name: str) -> bool:
    """缺失／空串／纯空白三种形态一律算"没配"。

    ⛔ 不要放宽成 `name not in env`：企微后台复制粘贴带上尾随空格、或 .env 里写成
    `NAME=` 都会产出一个"存在但没用"的取值。放行它等于让服务带着一个必然失败的
    凭据启动——那正是 spec 要禁止的"已启动但收不到消息"。
    """
    value = env.get(name)
    return value is None or not value.strip()


def load_credentials(env: Mapping[str, str] | None = None) -> LiaisonCredentials:
    """读并校验凭据。任一项没配就 raise，⛔ 不返回半份、⛔ 不返回 None。"""
    source: Mapping[str, str] = os.environ if env is None else env
    missing = [name for name in REQUIRED_CREDENTIAL_ENV_NAMES if _is_blank(source, name)]
    if missing:
        raise MissingCredentialsError(missing)
    return LiaisonCredentials(
        bot_id=source[BOT_ID_ENV].strip(),
        bot_secret=source[BOT_SECRET_ENV].strip(),
    )


#: 群通知的发送地址（第 6 章）。**URL 本身就是凭据**——`?key=` 那一段即身份。
GROUP_WEBHOOK_ENV = "HR_LIAISON_GROUP_WEBHOOK"


def load_group_webhook(env: Mapping[str, str] | None = None) -> str:
    """读并校验群通知的发送地址。缺失／空串／纯空白一律 raise。

    ⛔ **刻意不加进 `REQUIRED_CREDENTIAL_ENV_NAMES`。** 那个元组是**启动期**
    fail-closed 的清单；收消息与发群通知是两件独立的事，把它加进去等于
    "没配群通知就整个值守通道起不来"——那不是 fail-closed，那是连坐。
    本函数在**发送时**校验：6.10 逐字要求"未配置 → 拒发并报告缺失的变量名，
    ⛔ 不静默跳过后报成功"，拒发的前提是先真的走到发送这一步。

    复用 `MissingCredentialsError`：它的消息里 ⛔ 只出现变量名、不出现取值。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    if _is_blank(source, GROUP_WEBHOOK_ENV):
        raise MissingCredentialsError([GROUP_WEBHOOK_ENV])
    return source[GROUP_WEBHOOK_ENV].strip()
