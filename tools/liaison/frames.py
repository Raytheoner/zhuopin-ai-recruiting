"""SDK `message` 帧 → `inbound.handle_inbound_message` 参数 的**纯映射**（tasks 8.5bis ⑤）。

⛔ 本模块不碰库、不碰文件、不读时钟、不记日志——它是工程铁律 2 里 `compute_*` 的那一侧。
帧到达的时刻由**回调侧**取（帧里没有本机时刻），⛔ 不许在这里读时钟。

## 一、已经实测成立的（依据＝钉死版本 `wecom-aibot-python-sdk==1.0.2` 的包内源码）

- 事件名就是字面量 `"message"`，载荷是**整个帧**、且只有一个位置参数：
  `aibot/message_handler.py:55` —— `emitter.emit("message", frame)`。
- 帧的类型是 `Dict[str, Any]`（`aibot/types.py:166` `WsFrame`），形状
  `{cmd?, headers: {req_id, ...}, body?, errcode?, errmsg?}`（`types.py:156-167`）。
- 监听器拿到的帧**一定**有 dict 型 `body` 且 `body["msgtype"]` 非空：不满足的帧在
  `message_handler.py:32-38` 就被 `return` 掉了，根本 emit 不出来。
- `msgtype` 的取值域是 `text` / `image` / `mixed` / `voice` / `file`
  （`types.py:98-114` `MessageType`）。
- 消息回调帧的 `cmd` 是 `"aibot_msg_callback"`（`types.py:88` `WsCmd.CALLBACK`）；
  `aibot_event_callback`（进会话/卡片/反馈）走另一条分支，⛔ 不会 emit `message`
  （`message_handler.py:41-46`）。

## 二、AT-1b 已用真实帧确认（2026-09-16，`[Mac]0910A`，两轮：群 @ 一条＋私信一条）

真实帧结构（键名与类型，取自值守线程的 fail-closed 诊断行；参照 win 端
`5-平台底座/wecom-aibot-service/aibot_service/frame_parsing.py` 逐条核对，路径一致）：

- `msgid` = `body.msgid`（顶层，两种 `chattype` 都有）。
- 发送人 userid = `body.from.userid`（两种 `chattype` 都有）。
- 正文 = `body.text.content`（`msgtype == "text"` 时）。
- **会话标识按 `chattype` 分叉**（design.md D6/`hr-wecom-aibot-liaison` 64 行明写）：
  单聊（`chattype == "single"`）取发送人 `userid`；群聊（`chattype == "group"`）
  取顶层 `body.chatid`。私聊帧里**没有 `chatid` 这个键**（2026-09-16 实测证实），
  ⇒ 单一固定路径表达不了这条分叉，`compute_inbound_frame` 按 `chattype` 分两支取，
  见 `THREAD_ID_PATHS_BY_CHATTYPE`。

## 三、`FIELD_PATHS` 现在的样子

`msgid` / `sender_userid` / `content` 三项是固定路径（不随 `chattype` 变），照旧放在
`FIELD_PATHS` 里；`thread_id` 因为要分叉，单独放 `THREAD_ID_PATHS_BY_CHATTYPE`。
帧里 `chattype` 不是 `"single"`/`"group"` 之一（协议变了，或 SDK 表面变了）⇒ fail-closed，
⛔ 不猜、不用 `userid` 顶替（那会让两个不同群里的人共享同一个 `thread_id`）。

## 四、附件句柄（2026-09-17 `[Mac]0917W`，**尚无真实文件帧依据 ⇒ 表留空，TD-51**）

`msgtype` 为 `image`/`file`/`voice` 的帧带一个媒体项，取回字节要再走一次 SDK 下载
（`aibot/client.py:304` `download_file(url, aes_key)`：先 HTTP GET 再 AES-256-CBC 解密，
`aes_key` 按其 docstring「取自消息中 image.aeskey 或 file.aeskey」）。**句柄落在帧的哪个键**
——`url`/`aeskey`/`filename` 各在哪——本仓库的值守日志与 `liaison_message` 表至今
（2026-09-17）只见过 `msgtype=text` 的帧，⛔ 没有一条真实文件帧可核。win 端参照实现
（`5-平台底座/wecom-aibot-service/aibot_service/frame_parsing.py`）读的是
`body.file.{url,aeskey,filename,md5}`，只是**候选**，⛔ 未经本系统真实帧确认前不填表。

⇒ `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` 与 `FIELD_PATHS` 同一纪律：表空即 fail-closed——
`compute_attachment_ref` 对文件帧抛 `AttachmentFieldsUnverifiedError`，接线层
（`__main__.handle_message_frame`）只记一行**无取值**的帧键结构、正文照常归档、附件不落盘。
销账（TD-51）＝Shao Peishen／汤丽萍私信一个测试文件后，按日志里的帧键结构把三条路径填进表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.liaison.session_client import SdkSurfaceUnverifiedError

#: 消息推送回调的 `cmd`（`aibot/types.py:88`）。⚠️ 只用于诊断文本，⛔ 不做拦截判据——
#: SDK 那边并不校验它，在这里拿它拒绝一条真实消息只会平白丢材料。
MESSAGE_CALLBACK_CMD = "aibot_msg_callback"

#: `msgtype` 的取值路径。这一条**有实测依据**（见模块 docstring 第一节），
#: 所以它写死在代码里，⛔ 不进 `FIELD_PATHS`（那张表的语义是"尚未确认"）。
MSGTYPE_PATH = ("body", "msgtype")

#: 帧里取不到就**拒绝落库**的三个字段。它们都是键：`thread_id` 是归档目录与幂等键的
#: 第一段，`msgid` 是幂等键的 business_key，`sender_userid` 决定准入与否。
#: ⛔ 任何一个取错，错法都是静默的。⚠️ `thread_id` 不在 `FIELD_PATHS` 里
#: （见 `THREAD_ID_PATHS_BY_CHATTYPE`），但仍是必需字段，处理逻辑见
#: `compute_inbound_frame`。
REQUIRED_FIELDS = ("thread_id", "msgid", "sender_userid")

#: 正文即材料本身的那些消息类型：取不到正文就等于丢材料 ⇒ 一并拒绝落库。
#: 其余类型（图片/语音/文件）的正文本来就可以为空，⛔ 不因空正文拒收。
CONTENT_REQUIRED_MSGTYPES = frozenset({"text", "mixed"})

#: 帧内取值路径表（2026-09-16 AT-1b 真实帧确认，见模块 docstring 第二节）。
#: 键 = `handle_inbound_message` 的参数名；值 = 在帧里逐级取 dict 键的路径。
#: ⚠️ 不含 `thread_id`——那一项按 `chattype` 分叉，见 `THREAD_ID_PATHS_BY_CHATTYPE`。
FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "msgid": ("body", "msgid"),
    "sender_userid": ("body", "from", "userid"),
    "content": ("body", "text", "content"),
}

#: `chattype` 的取值路径（`aibot/message_handler.py` 顶层字段，两种消息都有）。
CHATTYPE_PATH = ("body", "chattype")

#: `thread_id` 按 `chattype` 分叉的取值路径（design.md D6 / `hr-wecom-aibot-liaison`
#: 第 64 行：「`thread_id` = 消息来源会话标识（私聊取 `userid`，群聊取 `chatid`）」）。
#: 单聊帧**没有 `chatid` 这个键**（2026-09-16 实测证实），⇒ 不能用一条固定路径
#: 表达这条分叉；`compute_inbound_frame` 先读 `chattype` 再挑对应的路径。
#: 不是 `"single"`/`"group"` 之一 ⇒ fail-closed，⛔ 不猜、不用 `userid` 顶替
#: （那会让两个不同群里的人共享同一个 `thread_id`）。
THREAD_ID_PATHS_BY_CHATTYPE: dict[str, tuple[str, ...]] = {
    "single": ("body", "from", "userid"),
    "group": ("body", "chatid"),
}

#: 带媒体项的消息类型（`aibot/types.py:98-114`）。`mixed`（图文混排）刻意不在内：它的正文
#: 是材料本身（`CONTENT_REQUIRED_MSGTYPES`），图片部分是否单独可下载没有任何依据。
ATTACHMENT_MSGTYPES = frozenset({"image", "file", "voice"})

#: 附件只走私信的口径值（Shao Peishen 2026-09-21 定，`0921E`）：群里发的附件不收，
#: 只在单聊帧上尝试取附件句柄。⚠️ 只影响附件，⛔ 不影响文本帧的既有处理路径
#: （文本消息群聊/单聊一视同仁，见 `compute_inbound_frame`）。
SINGLE_CHAT_CHATTYPE = "single"


def frame_chattype(frame: Any) -> str | None:
    """帧的 `chattype` 取值（`"single"`/`"group"`/其它取值/取不到时为 `None`）。

    ⛔ 不做校验、不 fail-closed——那是 `compute_inbound_frame` 的职责。这里只读一个
    值给接线层判断"附件是否只走私信"（`SINGLE_CHAT_CHATTYPE`），⛔ 不作其它用途。

    纯函数：不读时钟、不记日志、不改入参。
    """
    value = _read_path(frame, CHATTYPE_PATH)
    return value if isinstance(value, str) else None

#: 附件句柄的取值路径表：`msgtype` → {`download_url`（必需）, `aes_key`（可选）,
#: `filename`（可选）} → 帧内路径。**现状为空**（模块 docstring 第四节，TD-51）：
#: 无真实文件帧依据 ⇒ ⛔ 不猜。空表 ⇒ `compute_attachment_ref` 对文件帧 fail-closed。
#: 填表时三条路径都要有真实帧键结构日志作依据，⛔ 不许只凭 SDK docstring 或 win 端代码抄。
ATTACHMENT_FIELD_PATHS_BY_MSGTYPE: dict[str, dict[str, tuple[str, ...]]] = {}

ATTACHMENT_REQUIRED_KEYS = ("download_url",)
ATTACHMENT_OPTIONAL_KEYS = ("aes_key", "filename")


class InboundFrameUnverifiedError(SdkSurfaceUnverifiedError):
    """帧映射对不上：要么表还没填（现状），要么真实帧与表不符（SDK/协议变了）。

    ⛔ 这个错误不许被降级成"取不到就用默认值"——落一条 `thread_id=None` 的归档，
    比不落这条归档难查一个数量级：台账里有行、材料在错的地方、幂等键从此错位。
    继承 `SdkSurfaceUnverifiedError` 是为了让"表面未验就不许跑"这一族用同一个
    `except` 兜住。
    """


@dataclass(frozen=True)
class InboundFrameFields:
    """一条入站消息里**帧自带**的那几个值。⛔ 不含 `received_at`——那是回调那一刻
    的本机时间，由接线层取，不在帧里。"""

    thread_id: str
    msgid: str
    sender_userid: str
    msgtype: str
    content: str


class AttachmentFieldsUnverifiedError(InboundFrameUnverifiedError):
    """附件句柄取不到：表没填（现状，TD-51）或真实帧与表不符。

    继承 `InboundFrameUnverifiedError` 是为了让接线层用同一族 `except` 兜住；但**处置
    不同**：主字段取不到 ⇒ 整条消息不落库；附件句柄取不到 ⇒ 正文照常归档、只是附件
    不落盘（材料没丢在系统里，只是还留在企微侧，待办照样生成、人能去要）。
    """


@dataclass(frozen=True)
class InboundAttachmentRef:
    """帧里的附件**句柄**（⛔ 不是字节）：下载它是一次网络调用，只许在值守线程侧经
    `InboundPorts.download` 发生。`aes_key`/`filename` 帧里没给就是 `None`——`filename`
    可由下载响应头补（SDK `download_file` 会从 `Content-Disposition` 解出来）。"""

    msgtype: str
    download_url: str
    aes_key: str | None
    filename: str | None


def _read_path(frame: Any, path: tuple[str, ...]) -> Any:
    """按路径逐级取值。任何一级不是 dict 或缺键 ⇒ 返回 `None`。

    ⛔ 不抛异常：调用方要按"这个字段是不是必需"来决定处置，而不是被半路打断。
    """
    node = frame
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def verify_message_frame_envelope(frame: Any) -> str:
    """核对**有实测依据**的那部分帧形状，并返回 `msgtype`。对不上就抛。

    这三项 SDK 自己已经保证过（`message_handler.py:32-38`），所以对不上意味着
    SDK 版本或协议变了——那正是"表面未验"，⛔ 不许硬着头皮往下走。
    """
    if not isinstance(frame, dict):
        raise InboundFrameUnverifiedError(
            f"`message` 事件的载荷必须是帧字典（aibot/types.py:166），实际是 "
            f"{type(frame).__name__}。SDK 表面变了，⛔ 不要绕过本检查。"
        )
    body = frame.get("body")
    if not isinstance(body, dict):
        raise InboundFrameUnverifiedError(
            "帧里没有 dict 型 `body`——SDK 在 message_handler.py:34-38 已经把这种帧"
            "拦掉了，能走到这里说明 SDK 表面变了。⛔ 不要绕过本检查。"
        )
    msgtype = _read_path(frame, MSGTYPE_PATH)
    if not isinstance(msgtype, str) or not msgtype:
        raise InboundFrameUnverifiedError(
            f"`body.msgtype` 必须是非空字符串（aibot/types.py:98-114），实际是 {msgtype!r}。"
            "⛔ 不要绕过本检查。"
        )
    return msgtype


def compute_inbound_frame(frame: Any) -> InboundFrameFields:
    """帧 → 参数的纯映射。**取不到就抛，⛔ 绝不返回半份或带默认值的结果。**

    `FIELD_PATHS`／`THREAD_ID_PATHS_BY_CHATTYPE` 任一为空 ⇒ 必抛——那是「表还没填」
    的终态，不该发生在填表之后，留着当防线（比如测试里手滑把表清空）。
    """
    msgtype = verify_message_frame_envelope(frame)

    if not FIELD_PATHS or not THREAD_ID_PATHS_BY_CHATTYPE:
        raise InboundFrameUnverifiedError(
            "帧字段映射表 frames.FIELD_PATHS／THREAD_ID_PATHS_BY_CHATTYPE 是空的：`msgid` / "
            "发送人 userid / 会话 id 落在帧的哪个键上，没有真实帧依据。按 TD-19 同一处置 "
            "fail-closed：⛔ 宁可这条消息不落库，也不落一条键取错了的归档。"
        )

    values: dict[str, Any] = {}
    missing: list[str] = []

    # thread_id 按 chattype 分叉（design.md D6），不在 FIELD_PATHS 里，单独处理。
    chattype_value = _read_path(frame, CHATTYPE_PATH)
    thread_path = (
        THREAD_ID_PATHS_BY_CHATTYPE.get(chattype_value)
        if isinstance(chattype_value, str)
        else None
    )
    if thread_path is None:
        missing.append(
            f"thread_id（body.chattype={chattype_value!r} 不是 "
            f"{sorted(THREAD_ID_PATHS_BY_CHATTYPE)} 之一，不知道会话标识落在哪个键）"
        )
    else:
        thread_value = _read_path(frame, thread_path)
        if not isinstance(thread_value, str) or not thread_value:
            missing.append(
                f"thread_id（chattype={chattype_value!r} 对应路径 "
                f"{'.'.join(thread_path)} 取到 {type(thread_value).__name__}）"
            )
        else:
            values["thread_id"] = thread_value

    for field in ("msgid", "sender_userid"):
        path = FIELD_PATHS.get(field)
        if path is None:
            missing.append(f"{field}（路径表里没有这一项）")
            continue
        value = _read_path(frame, path)
        if not isinstance(value, str) or not value:
            missing.append(f"{field}（路径 {'.'.join(path)} 取到 {type(value).__name__}）")
            continue
        values[field] = value

    content_path = FIELD_PATHS.get("content")
    content = _read_path(frame, content_path) if content_path is not None else None
    if content is None:
        content = ""
    if not isinstance(content, str):
        missing.append(f"content（路径 {'.'.join(content_path or ())} 取到非字符串）")
    elif not content and msgtype in CONTENT_REQUIRED_MSGTYPES:
        # 文本类消息的正文就是材料本身：空正文归档等于把材料丢了，且台账里看着正常。
        missing.append(f"content（msgtype={msgtype} 的正文不许为空）")

    if missing:
        raise InboundFrameUnverifiedError(
            "帧里取不到这些字段，本条消息 ⛔ 不落库（fail-closed）：" + "；".join(missing)
        )

    return InboundFrameFields(
        thread_id=values["thread_id"],
        msgid=values["msgid"],
        sender_userid=values["sender_userid"],
        msgtype=msgtype,
        content=content,
    )


def compute_attachment_ref(frame: Any, msgtype: str) -> InboundAttachmentRef | None:
    """帧 → 附件句柄的纯映射。不带媒体项的 `msgtype` ⇒ `None`；带媒体项但表里没有这个
    `msgtype`、或必需路径取不到 ⇒ 抛 `AttachmentFieldsUnverifiedError`，⛔ 绝不返回
    一个猜出来的 URL——下载一个猜错的地址要么 404、要么把别的东西当材料落盘。

    纯函数：不读文件、不读时钟、不记日志（工程铁律 2）。
    """
    if msgtype not in ATTACHMENT_MSGTYPES:
        return None

    paths = ATTACHMENT_FIELD_PATHS_BY_MSGTYPE.get(msgtype)
    if not paths:
        raise AttachmentFieldsUnverifiedError(
            f"msgtype={msgtype} 的附件句柄（url/aeskey/filename）落在帧的哪个键上，没有真实"
            "文件帧依据（frames.ATTACHMENT_FIELD_PATHS_BY_MSGTYPE 里没有这一项，TD-51）。"
            "按 TD-19 同一处置 fail-closed：正文照常归档，附件 ⛔ 不猜地址、不下载、不落盘。"
        )

    values: dict[str, str | None] = {}
    missing: list[str] = []
    for key in ATTACHMENT_REQUIRED_KEYS:
        path = paths.get(key)
        value = _read_path(frame, path) if path is not None else None
        if not isinstance(value, str) or not value:
            missing.append(
                f"{key}（路径 {'.'.join(path) if path else '<表里没有>'} 取到 "
                f"{type(value).__name__}）"
            )
        else:
            values[key] = value
    for key in ATTACHMENT_OPTIONAL_KEYS:
        path = paths.get(key)
        value = _read_path(frame, path) if path is not None else None
        values[key] = value if isinstance(value, str) and value else None

    if missing:
        raise AttachmentFieldsUnverifiedError(
            f"msgtype={msgtype} 的帧里取不到附件句柄，附件 ⛔ 不下载、不落盘（fail-closed）："
            + "；".join(missing)
        )

    return InboundAttachmentRef(
        msgtype=msgtype,
        download_url=values["download_url"],
        aes_key=values["aes_key"],
        filename=values["filename"],
    )


def describe_frame_shape(frame: Any, *, max_depth: int = 4) -> str:
    """把帧渲染成一行**只有键名与值类型**的结构图，给 AT-1b 填路径表用。

    🔴 **⛔ 一个取值都不许出现在返回值里**：正文、姓名、userid 都是个人信息，
    而这份字符串是要进日志的（日志＝个人信息的第二份拷贝，见 `.gitignore:17`）。
    容器只报长度——`len` 足够判断"这个键是不是装着正文"，且不泄露内容。

    纯函数：不读时钟、不记日志、不改入参。
    """
    return ", ".join(_describe_node(frame, (), max_depth)) or "<空帧>"


def frame_key_types(frame: Any, *, max_depth: int = 4) -> dict[str, str]:
    """把帧渲染成 `{点分键路径: 类型名}` 的字典，只给未知附件帧取证落盘用（`0921E`）。

    🔴 **⛔ 一个取值都不许出现在返回值里，也 ⛔ 不带长度**——比 `describe_frame_shape`
    还严格一档：那个函数给字符串报长度是为了判断"这个键是不是装着正文"，这里连长度
    都不留，因为产出会**落盘**（日志只是一次性打印，文件是持久留存的第二份拷贝）。

    纯函数：不读时钟、不记日志、不改入参、不写文件——落盘是调用方（接线层）的事。
    """
    result: dict[str, str] = {}
    _collect_key_types(frame, (), max_depth, result)
    return result


def _collect_key_types(
    node: Any, path: tuple[str, ...], depth_left: int, out: dict[str, str]
) -> None:
    if isinstance(node, dict):
        if depth_left <= 0 or not node:
            return
        for key in node:
            _collect_key_types(node[key], path + (str(key),), depth_left - 1, out)
        return
    if isinstance(node, (list, tuple)):
        if not node:
            return
        _collect_key_types(node[0], path + ("[0]",), max(depth_left - 1, 0), out)
        return
    label = ".".join(path) if path else "<根>"
    out[label] = type(node).__name__


def _describe_node(node: Any, path: tuple[str, ...], depth_left: int) -> list[str]:
    label = ".".join(path) if path else "<根>"
    if isinstance(node, dict):
        if depth_left <= 0:
            return [f"{label}: dict(键数={len(node)}, 已达深度上限)"]
        if not node:
            return [f"{label}: dict(空)"]
        lines: list[str] = []
        for key in node:
            lines.extend(_describe_node(node[key], path + (str(key),), depth_left - 1))
        return lines
    if isinstance(node, (list, tuple)):
        if not node:
            return [f"{label}: list(空)"]
        head = _describe_node(node[0], path + ("[0]",), max(depth_left - 1, 0))
        return [f"{label}: list(长度={len(node)})", *head]
    if isinstance(node, str):
        return [f"{label}: str(长度={len(node)})"]
    if isinstance(node, bool):
        return [f"{label}: bool"]
    return [f"{label}: {type(node).__name__}"]
