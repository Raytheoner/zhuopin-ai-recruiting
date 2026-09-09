"""HR 值守通道服务的异常类型。"""

from __future__ import annotations

from collections.abc import Iterable


class MissingCredentialsError(RuntimeError):
    """凭据缺失／为空／只含空白时抛出，服务据此拒绝启动。

    ⛔ 消息里**只出现变量名，绝不出现取值**。这类报错会被贴进聊天、日志与 issue，
    把取值打出来等于让凭据顺着排障路径散出去——而排障路径恰恰是最不设防的那条。
    """

    def __init__(self, missing_names: Iterable[str]) -> None:
        self.missing_names: tuple[str, ...] = tuple(missing_names)
        joined = "、".join(self.missing_names)
        super().__init__(
            f"HR 值守通道拒绝启动：以下凭据缺失或只含空白字符 → {joined}。"
            f"请在 tools/liaison/.env 里补齐（真实值只落 .env，⛔ 不入版本管理）。"
        )
