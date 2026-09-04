from __future__ import annotations

from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


@dataclass
class AuthContext:
    """
    当前请求的鉴权上下文。demo 阶段恒为未鉴权直通（user_id=None）。
    企微 OAuth SSO 接入时，只替换 AuthMiddleware.dispatch 内部的解析逻辑，
    调用方（路由处理函数）读取 request.state.auth 的方式不变。
    """

    user_id: str | None
    authenticated: bool


# 决策人未知时写进 human_review.reviewer 的显式标记。
#
# 鉴权是空壳（部署约束 3），AuthContext.user_id 恒为 None。留痕必须写一个
# "决策人"，这时唯一诚实的写法是显式标注"身份未知"：
#   ⛔ 不写 NULL —— 分不清"没有决策人"和"这条漏写了"，而这两者的处置相反
#   ⛔ 不编一个人名 —— 那是伪造留痕，比不留痕更糟
#
# ⚠️ 部署约束 5：M2 起处理真实简历前，必须具备可识别到人的登录 + 访问留痕。
# 这个值出现在留痕里，就是"这条痕还追不到人"的诚实标记，已登记在
# docs/tech-debt.md。SSO 落地后 user_id 变成真实企微 userid，本常量自动不再
# 被取用，human_review 表结构与所有调用方一行不改。
UNKNOWN_REVIEWER = "unknown:web-session"


def reviewer_of(request: Request) -> str:
    """当前请求的决策人标识。SSO 落地后本函数不用改。

    ⛔ 绝不返回空串：human_review.reviewer 上有 CHECK，空串会让整条人工决策
    连同业务写一起回滚（同一事务），业务经理点确认会当场看到失败。
    """
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user_id", None) or UNKNOWN_REVIEWER


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件空壳接入点（部署约束 3）。demo 阶段不校验、不拒绝任何请求，
    无条件放行；user_id 恒为 None。签名对齐未来企微 OAuth SSO：
    真实实现落地时只替换 dispatch 内部逻辑，路由处理函数读取
    request.state.auth 的方式保持不变。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.auth = AuthContext(user_id=None, authenticated=False)
        return await call_next(request)
