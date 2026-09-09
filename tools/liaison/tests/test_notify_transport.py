"""第 6 章·传输封装与凭据（6.1 / 6.10）。

**不发任何真网络**：`urlopen` 一律 monkeypatch 掉。真实投递是 8.6，由
Shao Peishen 亲自做（对外通道开关属"不可代"项）。

测试里的地址一律用 `https://example.invalid/...` 占位——`.invalid` 是 RFC 2606
保留的顶级域，就算哪天真被误发也一定解析失败。
"""

from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request

import pytest

from tools.liaison import config
from tools.liaison.errors import MissingCredentialsError
from tools.liaison.notify import transport

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"


class FakeResponse:
    """`urlopen` 的返回值假体：只提供本实现真正用到的三样东西。"""

    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


# ── parse_webhook_response：纯函数，errcode 检查（6.1） ──────────────────


def test_errcode_zero_is_success():
    resp = transport.parse_webhook_response(b'{"errcode":0,"errmsg":"ok"}', status=200)
    assert resp.ok is True
    assert resp.errcode == 0


def test_rate_limit_errcode_is_returned_not_raised():
    """45009 是"要退避重试"的信号，⛔ 不是传输层异常——退避决策在上层。"""
    resp = transport.parse_webhook_response(b'{"errcode":45009,"errmsg":"limit"}', status=200)
    assert resp.ok is False
    assert resp.errcode == 45009


@pytest.mark.parametrize("literal", ["true", "false"])
def test_boolean_errcode_is_rejected_not_treated_as_a_number(literal):
    """`{"errcode": true}` ⛔ 不许被当成 errcode 数值。

    `bool` 是 `int` 的子类，`isinstance(True, int)` 为真——少了
    `transport.py` 里那句 `isinstance(errcode, bool) or ...`，`false` 会被
    当成 `errcode == 0`，也就是**把一个畸形响应判成发送成功**。
    删掉那句 `isinstance(errcode, bool) or` 本条必红。
    """
    with pytest.raises(transport.WebhookTransportError) as exc:
        transport.parse_webhook_response(
            f'{{"errcode":{literal},"errmsg":"x"}}'.encode(), status=200
        )
    assert "bool" in str(exc.value), "报错要指出实际类型，否则排障时看不出是布尔"


@pytest.mark.parametrize("errcode", [93000, 40001, 41001, -1, 1])
def test_any_non_zero_errcode_is_not_ok(errcode):
    """⛔ **只有 errcode == 0 算成功。**

    45009 之外的错误码（如 93000 群机器人不存在、40001 凭据无效）绝不许被
    `ok` 判成成功——那会让"我已经通知过了"这句话变成谎，而这正是本章要消灭的。
    """
    raw = json.dumps({"errcode": errcode, "errmsg": "nope"}).encode("utf-8")
    resp = transport.parse_webhook_response(raw, status=200)
    assert resp.ok is False
    assert resp.errcode == errcode


def test_missing_errcode_is_never_treated_as_success():
    """spec 场景「非限流错误」的底座：HTTP 200 但没有 errcode ⛔ 不许算成功。"""
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b'{"ok":true}', status=200)


def test_non_200_status_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b"oops", status=502)


def test_non_json_body_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b"<html>502</html>", status=200)


def test_non_integer_errcode_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b'{"errcode":"0"}', status=200)


# ── UrllibTransport：唯一真发 HTTP 的类（这里把 urlopen 换掉） ──────────


def test_post_json_sends_utf8_json_with_a_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        captured["content_type"] = request.get_header("Content-type")
        return FakeResponse(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    resp = transport.UrllibTransport().post_json(
        FAKE_WEBHOOK, {"msgtype": "markdown", "markdown": {"content": "中文"}}
    )
    assert resp.ok is True
    assert captured["url"] == FAKE_WEBHOOK
    assert captured["timeout"] == transport.WEBHOOK_TIMEOUT_SECONDS
    assert "application/json" in captured["content_type"]
    # ensure_ascii=False：中文原样进 body，⛔ 不转义成 \uXXXX（省一半字节，
    # 而字节数正是长度守卫判定的对象）
    assert "中文".encode("utf-8") in captured["data"]
    assert json.loads(captured["data"].decode("utf-8"))["msgtype"] == "markdown"


def test_timeout_is_five_seconds_and_never_omitted(monkeypatch):
    """⛔ 不许不带超时地调 `urlopen`（Global Constraints·铁律 6 的出站等价物）。

    `timeout=None` 会让整条值守通道挂在一个永远不回的连接上，而"卡住"不报错、
    没有症状。这里同时钉住取值本身是 5.0。
    """
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(timeout)
        return FakeResponse(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    transport.UrllibTransport().post_multipart(FAKE_WEBHOOK, filename="a.md", content="x")
    assert transport.WEBHOOK_TIMEOUT_SECONDS == 5.0
    assert seen == [5.0, 5.0]


def test_response_is_closed_on_the_success_path_too(monkeypatch):
    """成功路径也必须关闭——`try/finally` 覆盖两条路，不是只兜异常。"""
    holder = {}

    def fake_urlopen(request, timeout=None):
        holder["response"] = FakeResponse(b'{"errcode":0,"errmsg":"ok"}')
        return holder["response"]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert transport.UrllibTransport().post_json(FAKE_WEBHOOK, {}).ok is True
    assert holder["response"].closed is True


def test_response_is_always_closed_even_when_parsing_fails(monkeypatch):
    """解析失败时响应也已关闭。

    ⚠️ **这条 ⛔ 不是 `try/finally` 的判据**——`parse_webhook_response` 是在
    `try/finally` **之外**调用的，解析抛异常时 `close()` 早就跑完了。把
    `transport.py` 的 `try/finally` 改成顺序执行，本条仍然全绿（实测）。
    真正守着那个 finally 的是下面的
    `test_response_is_closed_when_reading_the_body_raises`。
    """
    holder = {}

    def fake_urlopen(request, timeout=None):
        holder["response"] = FakeResponse(b"not json")
        return holder["response"]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transport.WebhookTransportError):
        transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    assert holder["response"].closed is True


class ExplodingReadResponse:
    """`read()` 抛异常的响应假体——**只有 `try/finally` 能救它**。

    `status` 与 `read()` 都在 `_read` 的 `try` 里，异常从这里逃出去时，唯一还能
    执行 `close()` 的就是那个 `finally`。⛔ 不要给它加 `__enter__`/`__exit__`：
    本目录禁止 `with`，加了会误导下一个人去写 `with`。
    """

    def __init__(self) -> None:
        self.status = 200
        self.closed = False

    def read(self) -> bytes:
        raise OSError("连接在读 body 的中途断了")

    def close(self) -> None:
        self.closed = True


def test_response_is_closed_when_reading_the_body_raises(monkeypatch):
    """⛔ 本目录不许写 `with urlopen(...)`，关闭只能靠 `try/finally`——**这条才是它的判据**。

    把 `transport.py::_read` 的 `try/finally` 改成顺序执行（`status`/`read()` 之后
    直接 `response.close()`），本条必红：`read()` 抛出后 `close()` 根本轮不到执行，
    连接就此泄漏。异常类型故意选 `OSError` 而不是 `WebhookTransportError`——
    `finally` 必须对**任何**异常都生效，不是只兜自家的那种。
    """
    holder = {}

    def fake_urlopen(request, timeout=None):
        holder["response"] = ExplodingReadResponse()
        return holder["response"]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OSError):
        transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    assert holder["response"].closed is True, (
        "读 body 抛异常时响应没被关闭 ⇒ _read 的 try/finally 没了或没生效"
    )


@pytest.mark.parametrize(
    "raised",
    [
        urllib.error.HTTPError(FAKE_WEBHOOK, 500, "boom", {}, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_transport_errors_never_leak_the_webhook_url(monkeypatch, raised):
    """群 webhook 的 URL **本身就是凭据**（`?key=` 那一段）。

    报错会被贴进聊天、日志与 issue——排障路径恰恰是最不设防的那条。
    """

    def fake_urlopen(request, timeout=None):
        raise raised

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transport.WebhookTransportError) as excinfo:
        transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    assert "fake-key-for-tests" not in str(excinfo.value)
    assert FAKE_WEBHOOK not in str(excinfo.value)
    # 链上也不许挂着带 URL 的原异常
    assert excinfo.value.__cause__ is None


def test_multipart_errors_never_leak_the_webhook_url_either(monkeypatch):
    """两个方法共用同一条 `_read`，但断言要覆盖两个入口——共用是实现细节。"""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(FAKE_WEBHOOK, 500, "boom", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transport.WebhookTransportError) as excinfo:
        transport.UrllibTransport().post_multipart(FAKE_WEBHOOK, filename="a.md", content="x")
    text = f"{excinfo.value!r} {excinfo.value}"
    assert "fake-key-for-tests" not in text
    assert "example.invalid" not in text
    assert excinfo.value.__cause__ is None


def test_multipart_body_carries_the_filename_and_the_content():
    body = transport.compute_multipart_body(
        boundary="BOUND", filename="liaison-notify-abc.md", content="完整正文"
    )
    text = body.decode("utf-8")
    assert "--BOUND" in text
    assert 'name="media"' in text
    assert 'filename="liaison-notify-abc.md"' in text
    assert "完整正文" in text
    assert text.endswith("--BOUND--\r\n")


def test_post_multipart_uses_the_multipart_content_type(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["content_type"] = request.get_header("Content-type")
        captured["data"] = request.data
        return FakeResponse(b'{"errcode":0,"errmsg":"ok","media_id":"MID"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    resp = transport.UrllibTransport().post_multipart(
        FAKE_WEBHOOK, filename="a.md", content="x"
    )
    assert resp.payload["media_id"] == "MID"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    boundary = captured["content_type"].split("boundary=", 1)[1]
    assert boundary.encode("ascii") in captured["data"]


def test_transport_api_has_no_recipient_parameter():
    """6.7 结构性：**地址即收件对象**，⛔ 签名里不存在"发给谁"。

    有了 `touser` 这样的参数，"只发内部值守群"就退化成一条纪律；没有它，
    这件事在语法层就写不出来。
    """
    import inspect

    forbidden = {
        "touser",
        "to_user",
        "toparty",
        "totag",
        "candidate",
        "candidate_id",
        "recipient",
        "openid",
        "openid_list",
        "userid",
        "external_userid",
        "externaluserid",
    }
    for holder in (transport.Transport, transport.UrllibTransport):
        for name in ("post_json", "post_multipart"):
            params = set(inspect.signature(getattr(holder, name)).parameters)
            assert not (params & forbidden), f"{holder.__name__}.{name} 出现了收件对象参数"


# ── 凭据：只从环境读，缺了就拒发并报变量名（6.10） ──────────────────────


def test_group_webhook_is_read_from_the_environment():
    assert config.load_group_webhook({config.GROUP_WEBHOOK_ENV: f"  {FAKE_WEBHOOK}  "}) == FAKE_WEBHOOK


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_missing_or_blank_webhook_is_refused_by_variable_name(value):
    """6.10 逐字：未配置 → 拒发并报告缺失的变量名，⛔ 不静默跳过后报成功。"""
    env = {} if value is None else {config.GROUP_WEBHOOK_ENV: value}
    with pytest.raises(MissingCredentialsError) as excinfo:
        config.load_group_webhook(env)
    assert config.GROUP_WEBHOOK_ENV in str(excinfo.value)
    assert excinfo.value.missing_names == (config.GROUP_WEBHOOK_ENV,)


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_missing_webhook_never_returns_anything(value):
    """⛔ 反证：缺失形态下**绝不许**走出一个返回值。

    "静默跳过后报成功"正是 6.10 要禁的那种失败——它不报错、没有症状，
    只是通知从来没发出去过。
    """
    env = {} if value is None else {config.GROUP_WEBHOOK_ENV: value}
    returned = object()
    try:
        returned = config.load_group_webhook(env)
    except MissingCredentialsError:
        pass
    assert returned is not None and not isinstance(returned, str), (
        f"缺失形态 {value!r} 竟然返回了 {returned!r}，而不是拒发"
    )


def test_error_message_reports_the_name_never_the_value():
    with pytest.raises(MissingCredentialsError) as excinfo:
        config.load_group_webhook({config.GROUP_WEBHOOK_ENV: "   "})
    assert "   " not in str(excinfo.value).replace(config.GROUP_WEBHOOK_ENV, "")


def test_group_webhook_is_not_part_of_startup_fail_closed():
    """结构性：⛔ 群 webhook 不进启动期必备清单。

    收消息与发群通知是两件独立的事。把它加进 REQUIRED_CREDENTIAL_ENV_NAMES，
    等于"没配群通知就整个值守通道起不来"——那不是 fail-closed，那是连坐。
    """
    assert config.GROUP_WEBHOOK_ENV not in config.REQUIRED_CREDENTIAL_ENV_NAMES


def test_env_example_declares_only_the_variable_name():
    """spec 场景「配置只记变量名」：受版本管理的配置里只有变量名，没有真实地址。"""
    text = (REPO_ROOT / "tools" / "liaison" / ".env.example").read_text(encoding="utf-8")
    assert f"{config.GROUP_WEBHOOK_ENV}=\n" in text or text.rstrip().endswith(
        f"{config.GROUP_WEBHOOK_ENV}="
    )


# ── 8.7·二进制附件与中文文件名（docx 群发的前置） ────────────────────────
#
# 缺口：`compute_multipart_body` 原先只收 `str`，`content.encode("utf-8")` 对
# docx 的字节流无从下手。docx 是 zip，任何"先 decode 再 encode"的往返都会毁掉它。


def test_multipart_body_accepts_bytes_content_verbatim():
    """bytes 原样进 body：⛔ 不许被当成文本做任何编解码往返。

    判据取 docx 的真实特征：PK 魔数 + 一个**不是合法 UTF-8** 的字节（0x89）。
    若实现里出现 `content.decode(...)` 之类的往返，这条当场炸 UnicodeDecodeError。
    """
    blob = b"PK\x03\x04\x89\xff\x00binary"
    body = transport.compute_multipart_body(
        boundary="BOUND", filename="a.docx", content=blob
    )
    assert blob in body
    head, _, tail = body.partition(blob)
    assert head.endswith(b"\r\n\r\n")
    assert tail == b"\r\n--BOUND--\r\n"


def test_str_and_bytes_paths_produce_identical_bodies():
    """`str` 只是"先 UTF-8 编码"的糖：两条路必须逐字节相同。

    ⛔ 不许 str 走一条格式、bytes 走另一条——那样单测覆盖了一条路，真发走的是另一条。
    """
    from_str = transport.compute_multipart_body(
        boundary="B", filename="同名.docx", content="完整正文"
    )
    from_bytes = transport.compute_multipart_body(
        boundary="B", filename="同名.docx", content="完整正文".encode("utf-8")
    )
    assert from_str == from_bytes


def test_chinese_filename_is_utf8_and_rfc2231_never_latin1():
    """中文文件名的**逐字节**断言（本项目的跟进信文件名全是中文）。

    两件事一起断言：
    ① `filename="…"` 里是**原始 UTF-8 字节**（浏览器与 curl 的实际做法，企微认这个）；
    ② 另有 RFC 2231/5987 的 `filename*=UTF-8''<percent>`（规范形态，ASCII-only）。
    ⛔ 不许 latin-1：`"人事部.docx".encode("latin-1")` 直接 UnicodeEncodeError，
    那会在**真发那一刻**才炸，而那时正文已经发出去了。
    """
    name = "人事部-跟进.docx"
    body = transport.compute_multipart_body(boundary="B", filename=name, content=b"x")
    assert f'filename="{name}"'.encode("utf-8") in body
    assert b"filename*=UTF-8''%E4%BA%BA%E4%BA%8B%E9%83%A8-%E8%B7%9F%E8%BF%9B.docx" in body
    # 头部除了那段原始 UTF-8 文件名之外不得夹带别的非 ASCII 编码形态
    header = body.split(b"\r\n\r\n", 1)[0]
    assert b"?" not in header, "文件名被转义成了 ? ⇒ 走了非 UTF-8 的编码路径"


@pytest.mark.parametrize("evil", ['a"b.docx', "a\r\nContent-Type: evil\r\n.docx", "a\nb.docx"])
def test_filename_with_header_breaking_characters_is_refused(evil):
    """引号与 CR/LF 一律拒收，⛔ 不做"清洗后照发"。

    multipart 的头部是靠 CRLF 分段的：一个带换行的文件名能凭空插进一个新头部，
    甚至提前闭合 boundary。这类输入没有"大概想发什么"的正确解释，只能拒。
    """
    with pytest.raises(ValueError):
        transport.compute_multipart_body(boundary="B", filename=evil, content=b"x")


def test_post_multipart_carries_bytes_through_to_the_request(monkeypatch):
    """端到端到 `Request.data`：bytes 一路不失真。"""
    blob = b"PK\x03\x04\x89\xff"
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = request.data
        return FakeResponse(b'{"errcode":0,"errmsg":"ok","media_id":"MID"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport.UrllibTransport().post_multipart(
        FAKE_WEBHOOK, filename="附件.docx", content=blob
    )
    assert blob in captured["data"]
