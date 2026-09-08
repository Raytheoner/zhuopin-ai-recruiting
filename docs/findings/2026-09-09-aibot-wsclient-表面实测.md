# aibot WSClient 表面实测（第 7 章 Task 5，7.6）

**目的**：Task 1 只验了"建连能力齐备"（类与 `WSClientOptions` 四个字段在）。接线还需要
知道两件它没验的事——跑起来的那个方法叫什么、连接/断开事件叫什么。这两个名字猜错的
后果是静默的：服务起得来、日志正常，但断线事件永远到不了 `LiaisonSession`。

**环境**：`tools/liaison/.venv`（Python 3.14.6），`pip install -r tools/liaison/requirements.txt`
成功（`wecom-aibot-python-sdk==1.0.2`，网络可达 pypi.org，无需真实凭据）。

## 命令

```bash
[ -d tools/liaison/.venv ] || /opt/homebrew/bin/python3.14 -m venv tools/liaison/.venv
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_ws_surface
```

## 原始 JSON 输出

```json
{
  "python_version": "3.14.6",
  "importable": true,
  "public_methods": [
    "add_listener",
    "api",
    "cancel",
    "complete",
    "connect",
    "disconnect",
    "download_file",
    "emit",
    "event_names",
    "is_connected",
    "listeners",
    "listens_to",
    "on",
    "once",
    "remove_all_listeners",
    "remove_listener",
    "reply",
    "reply_stream",
    "reply_stream_with_card",
    "reply_template_card",
    "reply_welcome",
    "run",
    "send_message",
    "update_template_card",
    "wait_for_complete"
  ],
  "connect_like_signatures": {
    "connect": "(self) -> 'WSClient'",
    "run": "(self) -> None",
    "on": "(self: Any, event: str, f: ~Handler | None = None) -> ~Handler | Callable[[~Handler], ~Handler]",
    "add_listener": "(self: Any, event: str, f: ~Handler) -> ~Handler"
  },
  "event_name_candidates": [
    "EventType"
  ],
  "base_classes": [
    "pyee.asyncio.AsyncIOEventEmitter",
    "pyee.base.EventEmitter",
    "builtins.object"
  ],
  "import_error": null
}
```

## 结论：本模块的既有假设是否成立

**`REQUIRED_CLIENT_ATTRS = ("on", "connect")`** —— 成立，两个方法都真实存在于
`WSClient` 上。⛔ **不改**（结构存在性检查本身没错，见下方"遗留发现"里为什么这不等于
"用法也对"）。

**`EVENT_CONNECTED = "connected"` / `EVENT_DISCONNECTED = "disconnected"`** —— 成立。
读源码 `tools/liaison/.venv/lib/python3.14/site-packages/aibot/client.py`
`_setup_ws_events()`（第 60-76 行）：

```python
self._ws_manager.on_connected = lambda: self.emit("connected")
...
self._ws_manager.on_disconnected = lambda reason: self.emit("disconnected", reason)
self._ws_manager.on_reconnecting = lambda attempt: self.emit("reconnecting", attempt)
self._ws_manager.on_error = lambda error: self.emit("error", error)
```

事件名是硬编码字符串字面量，不来自 `probe()` 报出的 `event_name_candidates`
（`aibot.EventType` 只有 `EnterChat` / `FeedbackEvent` / `TemplateCardEvent`——那是消息
**内容**类型枚举，与连接生命周期事件无关，探针把它报出来是"全量列出、人核对"的设计，
不是判断本身）。因此 `session_client.py` 的默认值与实测一致，⛔ **不改**。

## 遗留发现（记录给 Task 6 / 第 8 章接线用，本任务不处理）

`connect_like_signatures` 显示 `connect` 是 `async def connect(self) -> "WSClient"`
（源码第 81 行），建连成功后**立即返回**，不阻塞到断线为止；真正会阻塞、跑事件循环
直到进程被打断的是 `run(self) -> None`（第 343-360 行，等价于
`asyncio.new_event_loop()` + `loop.run_until_complete(self.connect())` + `loop.run_forever()`）。

`make_sdk_connect` 按 Task 5 brief 原样返回 `client.connect`，本任务的测试只用同步
`FakeClient.connect()` 验证"接线形状"（订阅两个事件、返回一个可调用对象），不触碰真实
`WSClient`——这在 Task 5 范围内成立。

**⚠️ 但把 `client.connect`（真实 SDK 的这一个）原样交给 `run_forever` 的 `connect` 参数会
是错的**：`run_forever` 期望一个"阻塞到断开为止"的同步调用；真实 `WSClient.connect` 是
协程且立即返回，`run_forever` 会把"刚连上"误判为"立刻又断开了"，退避从 1s 开始不停重连
——这正是 brief 里 `test_run_forever_sleeps_even_when_connect_returns_immediately` 那条用例
描述的"连上又立刻掉线"场景，只是触发原因不是真断线而是接线形状不对。

**留给 Task 6（`__main__.py` 接线）的具体建议**：真正应该交给 `run_forever` 的 callable
应该包一层，在其中跑 `client.run()`（同步阻塞）而不是裸的 `client.connect`；或者用
`asyncio.new_event_loop().run_until_complete(client.connect())` 之后再 `run_forever()`。
`make_sdk_connect` 现在返回的 `client.connect` 更适合作为"仅建立连接、不阻塞"的原语，
Task 6 需要在它外面再包一层阻塞适配，⛔ 不能直接把它塞进 `session_client.run_forever`。
本任务不改 `make_sdk_connect` 的返回值——brief 明确该函数签名与实现是 Task 5 的既定范围，
且改变返回语义会牵动测试契约（`test_make_sdk_connect_subscribes_both_events_and_returns_a_blocking_callable`
断言的正是"返回 `client.connect`"这个形状）——这条记录只是把"表面存在"与"能直接拼进
`run_forever` 的 `connect` 参数"这两件事的差异显式留痕，避免 Task 6 凭直觉直接拼接后
在真连接下出现见"服务起来、日志正常但从不重连或立刻自旋重试"的静默故障。
