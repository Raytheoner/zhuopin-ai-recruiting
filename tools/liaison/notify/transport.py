"""HTTP 传输层——**本服务唯一真发网络的地方**。

⛔ **本目录禁止写 `with X:`。** 第 2 章的事务扫描器把 `with <名字|属性>:` 无条件
判为隐式提交违规；`with <调用>:` 只对一份正面白名单（`open` / `os.fdopen` /
`io.open` / `suppress` / `NamedTemporaryFile` / `TemporaryDirectory` 与
`contextlib.*` / `tempfile.*` 两个模块族）放行，**陌生被调用者一律判违规**——
`urllib.request.urlopen` 正是陌生的那种。响应因此用 `resp = ...` +
`try/finally: resp.close()`，⛔ 不写 `with urllib.request.urlopen(...) as resp:`，
也 ⛔ 不写"先赋值再 `with 变量`"——`ast.Name` 同样在判违规之列。

⛔ **报错文本里绝不许出现 URL。** 群 webhook 的 URL 的 `?key=` 那一段**本身就是
凭据**，而报错会被贴进聊天、日志与 issue——排障路径恰恰是最不设防的那条。
`urllib` 的 `HTTPError` 把 URL 挂在自己身上，所以异常一律 `raise ... from None`
（⛔ 不是 `from exc`），只把类型名与 reason 转述出来。
tests/test_notify_transport.py::test_transport_errors_never_leak_the_webhook_url 守着这条。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Protocol

#: 出站超时。⛔ 不许不带超时地调 `urlopen`——没有超时的 HTTP 调用会让整条值守
#: 通道挂在一个永远不回的连接上，而"卡住"不报错、没有症状。
WEBHOOK_TIMEOUT_SECONDS = 5.0

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class WebhookTransportError(RuntimeError):
    """网络层或协议层失败：连不上、超时、非 200、响应不是合法 JSON、没有 errcode。

    ⛔ 构造消息时不许把 URL 拼进去（见模块 docstring）。
    """


@dataclass(frozen=True)
class WebhookResponse:
    """一次 webhook 调用的结果。**`errcode` 是唯一的成败判据**。"""

    errcode: int
    errmsg: str
    payload: dict

    @property
    def ok(self) -> bool:
        return self.errcode == 0


class Transport(Protocol):
    """传输层的形状。**以参数注入**，单测全部用 fake（opener 约束 2）。"""

    def post_json(
        self, url: str, payload: dict, *, timeout: float = WEBHOOK_TIMEOUT_SECONDS
    ) -> WebhookResponse: ...

    def post_multipart(
        self,
        url: str,
        *,
        filename: str,
        content: str | bytes,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> WebhookResponse: ...


def parse_webhook_response(raw: bytes, *, status: int) -> WebhookResponse:
    """把原始响应变成 `WebhookResponse`。**纯函数**（铁律 2）。

    ⛔ **HTTP 200 但没有 `errcode` 的响应绝不许算成功。** 企微的错误是靠 body 里的
    `errcode` 表达的，只看 HTTP 状态码等于把所有业务错误当成功——spec 的「非限流
    错误不被当作成功」在这一层就是这一条。
    """
    if status != 200:
        raise WebhookTransportError(f"群通知 webhook 返回 HTTP {status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookTransportError(
            f"群通知 webhook 的响应不是合法 JSON（{exc.__class__.__name__}），⛔ 不当作成功"
        ) from None
    if not isinstance(payload, dict) or "errcode" not in payload:
        raise WebhookTransportError("群通知 webhook 的响应里没有 errcode 字段，⛔ 不当作成功")
    errcode = payload["errcode"]
    # bool 是 int 的子类：`{"errcode": true}` 会被 isinstance(_, int) 放行，而那
    # 显然不是一个错误码。两条分开写，⛔ 不要合并成一个表达式。
    if isinstance(errcode, bool) or not isinstance(errcode, int):
        raise WebhookTransportError(
            f"群通知 webhook 的 errcode 不是整数（{type(errcode).__name__}），⛔ 不当作成功"
        )
    return WebhookResponse(
        errcode=errcode, errmsg=str(payload.get("errmsg", "")), payload=payload
    )


#: 文件名里一出现就无法安全表达的字符。multipart 的头部靠 CRLF 分段、靠引号界定
#: 文件名，所以这三个字符能凭空插进一个新头部、甚至提前闭合 boundary。
#: ⛔ 不做"清洗后照发"——被清洗过的文件名收信人拿到手是另一个名字，而那正是
#: 攻击者想要的那个名字。没有正确解释的输入只能拒。
_FILENAME_FORBIDDEN = ('"', "\r", "\n")


def compute_content_disposition(filename: str) -> str:
    """单个文件字段的 `Content-Disposition` 头。**纯函数**。

    两种形态一起给，因为它们各有各的读者：
    - `filename="…"` 里放**原始 UTF-8 字节**——浏览器与 curl 实际就这么发，企微
      认的也是这个；
    - `filename*=UTF-8\'\'<percent>` 是 RFC 2231/5987 的规范形态，纯 ASCII，
      按规范解析的一方读它。

    两者解码后是同一个名字，所以谁赢都一样安全。
    ⛔ **绝不许 latin-1**：`"人事部.docx".encode("latin-1")` 直接
    UnicodeEncodeError，而它会在**真发那一刻**才炸——那时正文已经发出去了，
    群里留下一条"完整正文见附件"却没有附件的通知。本项目的跟进信文件名全是中文，
    所以这不是边角情况，是**唯一**的情况。
    """
    for bad in _FILENAME_FORBIDDEN:
        if bad in filename:
            raise ValueError(
                f"文件名里含有会破坏 multipart 头部的字符 {bad!r}，⛔ 不清洗、不猜、直接拒"
            )
    quoted = urllib.parse.quote(filename, safe="", encoding="utf-8")
    return (
        'form-data; name="media"; '
        f'filename="{filename}"; '
        f"filename*=UTF-8\'\'{quoted}"
    )


def compute_multipart_body(*, boundary: str, filename: str, content: str | bytes) -> bytes:
    """拼一个只含单个文件字段的 multipart/form-data body。**纯函数**。

    `content` 收 `str` 与 `bytes` 两种：`str` 按 UTF-8 编码后**汇进同一条路**，
    `bytes` 原样落进 body。
    🔴 bytes 那条不许有任何 decode/encode 往返——docx 是 zip，往返一次就毁了，
    而毁掉的 zip 在企微那头只会得到一个语焉不详的错误码。

    ⛔ 不引入 `requests` / `urllib3` 之类的第三方库：design D10 的依赖隔离要求
    `tools/liaison/` 的依赖清单自己承担约束，而这段拼装只有二十行。
    """
    payload = content.encode("utf-8") if isinstance(content, str) else content
    lines = (
        f"--{boundary}\r\n"
        f"Content-Disposition: {compute_content_disposition(filename)}\r\n"
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    )
    tail = f"\r\n--{boundary}--\r\n"
    return lines.encode("utf-8") + payload + tail.encode("utf-8")


class UrllibTransport:
    """标准库实现。**本服务唯一真发 HTTP 的类。**"""

    def post_json(
        self, url: str, payload: dict, *, timeout: float = WEBHOOK_TIMEOUT_SECONDS
    ) -> WebhookResponse:
        # ensure_ascii=False：中文原样进 body，⛔ 不转义成 \uXXXX。
        # 不是审美问题——转义后字节数翻倍，而字节数正是长度守卫判定的对象。
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": _JSON_CONTENT_TYPE}
        )
        return self._read(request, timeout=timeout)

    def post_multipart(
        self,
        url: str,
        *,
        filename: str,
        content: str | bytes,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> WebhookResponse:
        boundary = f"----liaison{uuid.uuid4().hex}"
        body = compute_multipart_body(boundary=boundary, filename=filename, content=content)
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._read(request, timeout=timeout)

    def _read(self, request: urllib.request.Request, *, timeout: float) -> WebhookResponse:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # ⛔ from None：HTTPError 把 URL 挂在 .url / .filename 上，
            # 而 URL 里的 key 就是凭据。只转述状态码。
            raise WebhookTransportError(f"群通知 webhook 返回 HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise WebhookTransportError(f"群通知 webhook 连接失败：{exc.reason}") from None
        except TimeoutError:
            raise WebhookTransportError(f"群通知 webhook 超时（{timeout} 秒）") from None
        try:
            status = response.status
            raw = response.read()
        finally:
            # ⛔ 不写 `with`（见模块 docstring）。这个 finally 就是它的替代品，
            # 由 test_response_is_always_closed_even_when_parsing_fails 守着。
            response.close()
        return parse_webhook_response(raw, status=status)
