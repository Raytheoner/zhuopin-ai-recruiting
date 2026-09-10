"""通道适配层：把 SDK 的 `message` 事件帧翻译成 `handle_inbound_message` 的入参。

**这一层存在的唯一理由**：`__main__.py` 的回调纪律是「⛔ 不碰库、不碰文件」，而
`inbound.handle_inbound_message` 要的是一组已经拆好的标量。中间这段"从 WsFrame 里
把字段抠出来"必须是**纯函数**（铁律 2 的形状），才能在单测里毫秒跑完、且不需要 SDK。

⚠️ **两类事实在本模块里混着，务必分清**（改这里之前先读完这一段）：

1. **已实证的部分**——事件名与载荷形状，证据是钉死版本 `wecom-aibot-python-sdk==1.0.2`
   的源码本身（`aibot/message_handler.py::_handle_message_callback`）：

       emitter.emit("message", frame)        # 每一条消息回调都先 emit 这个
       emitter.emit(f"message.{msgtype}", frame)   # 再按 msgtype emit 一个细分事件

   ⇒ 订阅 `"message"` 一个就能收全，⛔ 不许改成订阅五个细分事件（`message.text`
   / `.image` / `.mixed` / `.voice` / `.file`）——SDK 对不认识的 msgtype 只
   `logger.debug` 一行、**不 emit 任何细分事件**，按细分事件接线会让新类型的消息
   静默消失，且没有任何症状。

2. **尚未实证的部分**——`frame["body"]` 里的**字段名**。SDK 把这个 dict **原样透传**
   （`WsFrame = Dict[str, Any]`，`types.py` 只注释了 `cmd`/`headers`/`body`/`errcode`
   /`errmsg` 五个顶层键，body 内部一个字段都没定义），所以下面这张 `_BODY_*` 表来自
   企微协议文档而**不是**实测。🔴 **它对不上真实报文的处置必须是"炸得很响"，⛔ 不许
   静默跳过**——这正是 `InboundFrameShapeError` ＋ 告警存在的理由：字段名猜错时，
   现象会是"`liaison_message` 恒为 0"，与"根本没接线""连接假死"三者从外部完全无法
   区分（TD-42 那十小时就是这么耗掉的）。字段名以 8.6 灰度**第一条真实入站**为准核对。

⛔ 本模块不发任何消息、不下载任何附件、不碰网络。理由见 `compute_inbound_fields`
与 `dispatch_inbound_frame` 的 docstring。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.inbound import InboundResult, handle_inbound_message

logger = logging.getLogger(__name__)

#: `frame["body"]` 里的字段名。⚠️ 见模块 docstring 第 2 条：**协议文档口径，未实测**。
#: 集中成常量而不是散在代码里，是为了让 8.6 核对真实报文时只需要改这一处。
_BODY_MSGID = "msgid"
_BODY_MSGTYPE = "msgtype"
_BODY_CHATID = "chatid"
_BODY_FROM = "from"
_BODY_FROM_USERID = "userid"
_BODY_TEXT = "text"
_BODY_TEXT_CONTENT = "content"

#: 带附件的消息类型。⚠️ 取值来自 SDK 的 `MessageType` 枚举（`types.py`，**已实证**），
#: ⛔ 不许在这里自己编字符串：枚举里没有的值 SDK 根本不会分发成细分事件。
ATTACHMENT_MSGTYPES = ("image", "voice", "file", "mixed")


class InboundFrameShapeError(ValueError):
    """入站帧的形状与 `_BODY_*` 字段表对不上。

    ⛔ **消息里只出现"哪些键在、哪些键缺"，绝不出现任何取值**——与
    `MissingCredentialsError` 同一条纪律，理由更硬：这里的取值是同事的 `userid`
    与聊天正文，属个人信息，而报错最常见的去处（日志、聊天、issue）恰恰不设防。
    """

    def __init__(self, reason: str, *, present_keys: tuple[str, ...]) -> None:
        self.present_keys = present_keys
        super().__init__(
            f"入站帧形状不符：{reason}。"
            f"body 实际带的键（⛔ 只列键名、不列取值）＝ {list(present_keys)}。"
            "⚠️ 字段名表来自协议文档、未经真实报文实测（见 channel.py 模块 docstring 第 2 条），"
            "对不上时先核这张表，⛔ 不要先怀疑发送方。"
        )


@dataclass(frozen=True)
class InboundFields:
    """一条入站消息拆出来的标量。纯数据，由 `compute_inbound_fields` 算出。"""

    thread_id: str
    msgid: str
    sender_userid: str
    msgtype: str
    content: str
    #: 这条消息**声称**带附件（msgtype 在 `ATTACHMENT_MSGTYPES` 里），但本层
    #: ⛔ 不下载。取值为真时调用方必须把它记进日志，理由见 `dispatch_inbound_frame`。
    carries_attachment: bool


def _require_text(value: Any, field_name: str, present_keys: tuple[str, ...]) -> str:
    """取一个必需的非空字符串字段。

    ⛔ 不做 `str(value)` 兜底：把一个 dict 或 None 转成 `"None"` 再当 `msgid` 落库，
    会造出一条永远查不回去、还会与下一条同样坏的消息撞幂等键的台账行。
    """
    if not isinstance(value, str) or not value.strip():
        raise InboundFrameShapeError(
            f"{field_name} 必须是非空字符串，实际类型是 {type(value).__name__}",
            present_keys=present_keys,
        )
    return value


def compute_inbound_fields(frame: Any) -> InboundFields:
    """纯函数（铁律 2 的形状）：`WsFrame` → 一组标量。

    ⛔ 不读时钟（`received_at` 由调用方传——回调那一刻取的时间才是收到的时间）、
    ⛔ 不读名单文件（那是 `admit()` 的事）、⛔ 不碰库、⛔ 不下载附件。

    对不上就 raise，⛔ 不许返回 None 让调用方"看情况"——静默跳过一条入站消息
    正是本章要消灭的东西。
    """
    if not isinstance(frame, dict):
        raise InboundFrameShapeError(
            f"frame 必须是 dict（SDK 的 WsFrame），实际类型是 {type(frame).__name__}",
            present_keys=(),
        )
    body = frame.get("body")
    if not isinstance(body, dict):
        raise InboundFrameShapeError(
            f"frame['body'] 必须是 dict，实际类型是 {type(body).__name__}",
            present_keys=tuple(sorted(k for k in frame if isinstance(k, str))),
        )

    present = tuple(sorted(k for k in body if isinstance(k, str)))

    msgid = _require_text(body.get(_BODY_MSGID), _BODY_MSGID, present)
    msgtype = _require_text(body.get(_BODY_MSGTYPE), _BODY_MSGTYPE, present)

    sender = body.get(_BODY_FROM)
    if not isinstance(sender, dict):
        raise InboundFrameShapeError(
            f"body[{_BODY_FROM!r}] 必须是 dict（内含 {_BODY_FROM_USERID}），"
            f"实际类型是 {type(sender).__name__}",
            present_keys=present,
        )
    sender_userid = _require_text(
        sender.get(_BODY_FROM_USERID), f"{_BODY_FROM}.{_BODY_FROM_USERID}", present
    )

    # thread_id 的取法有 SDK 源码背书：`client.send_message` 的 docstring 逐字写着
    # 「chatid: 会话 ID，单聊填用户的 userid，群聊填对应群聊的 chatid」
    # （`aibot/client.py`），与 design D3 对 thread_id 的定义**完全同义**。
    # ⇒ 直接用 chatid，⛔ 不要自己按 chattype 分支——那是把 SDK 已经统一好的东西
    # 再拆一遍，多一处会漂移的真源。
    chatid = body.get(_BODY_CHATID)
    if isinstance(chatid, str) and chatid.strip():
        thread_id = chatid
    else:
        # ⚠️ 退路只在**单聊**语义下成立（单聊的 chatid 本来就等于 userid）。
        # 记 WARNING 而不是静默：chatid 缺席意味着字段名表可能对不上，
        # 而群聊消息一旦走到这里，thread_id 会退化成发送人而不是群——
        # 归档目录与幂等键都会跟着错，且 ⛔ 不会有任何报错。
        logger.warning(
            "入站帧没有可用的 %r，thread_id 退回发送人 userid（只对单聊成立）。"
            "body 实际带的键 ＝ %s（⛔ 只列键名）。⚠️ 若这是群消息，归档与幂等键都会错，"
            "请当场核对 channel.py 的 _BODY_* 字段名表。",
            _BODY_CHATID,
            list(present),
        )
        thread_id = sender_userid

    return InboundFields(
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=sender_userid,
        msgtype=msgtype,
        content=_compute_content(body),
        carries_attachment=msgtype in ATTACHMENT_MSGTYPES,
    )


def _compute_content(body: dict) -> str:
    """取正文。取不到一律空串——⛔ 不 raise。

    正文与 `msgid`/`msgtype` 不同：图片、文件、语音**本来就没有正文**，把"没有正文"
    当成形状错误会让最要紧的那条链路（私信发文档 → 归档）整条打不通。
    下游 `compute_task_summary` 对空串有明确处置（写成 `[file]` 之类），⛔ 不要在这里
    抢它的活。
    """
    text = body.get(_BODY_TEXT)
    if isinstance(text, dict):
        content = text.get(_BODY_TEXT_CONTENT)
        if isinstance(content, str):
            return content
    return ""


def dispatch_inbound_frame(
    conn,
    frame: Any,
    *,
    received_at: str,
    archive_root=DEFAULT_ARCHIVE_ROOT,
    whitelist_path=None,
) -> InboundResult:
    """拆帧 → 交给 `handle_inbound_message`。**在值守线程里调**，⛔ 不在 SDK 回调里调。

    *为什么这条纪律要写死*：sqlite 连接默认只能在创建它的线程里用，而 SDK 回调跑在
    它自己的事件循环线程上。在回调里碰库会在最不该出错的那一刻抛异常，异常再被 pyee
    转成 `error` 事件——TD-39 那条重连链正是这么被打断的。

    ⛔ **不传 `reply`**（`handle_inbound_message` 的礼貌回复端口）：对外发消息要走 SDK
    的事件循环，而这里是值守线程；更要紧的是"对外发送"属本项目 🔴 不可代办的一档，
    ⛔ 不许在一次接线任务里顺手接通。名单外发送人因此**只归档、不收到礼貌说明**——
    这是一个已登记的缺口（`docs/tech-debt.md`），⛔ 不要在这里"顺手"补。

    ⛔ **不下载附件**：`InboundAttachment` 要的是 `bytes`，取字节要 `client.download_file`
    （async ＋ 网络 ＋ AES 解密），且解密要的 `file.url` / `file.aeskey` 字段名同样
    **未经真实报文实测**。⛔ 不许在这里发明一个未经验证的适配器（TD-19 的教训逐字）。
    带附件的消息**照常归档、照常入队**，只是附件字节这一份材料本轮不落盘——
    调用方据 `carries_attachment` 记 WARNING，让这个缺口一直有症状。
    """
    fields = compute_inbound_fields(frame)
    if fields.carries_attachment:
        logger.warning(
            "入站消息 msgtype=%r 声称带附件，但本轮 ⛔ 未下载附件字节（只归档消息本身）。"
            "thread_id=%s msgid=%s。⚠️ 附件下载链路未接（见 channel.py 与 docs/tech-debt.md），"
            "8.6 灰度「私信发文档 → 归档」这条**验不到附件**，⛔ 不要据本条日志判定链路已通。",
            fields.msgtype,
            fields.thread_id,
            fields.msgid,
        )
    return handle_inbound_message(
        conn,
        thread_id=fields.thread_id,
        msgid=fields.msgid,
        sender_userid=fields.sender_userid,
        received_at=received_at,
        msgtype=fields.msgtype,
        content=fields.content,
        attachment=None,
        archive_root=archive_root,
        whitelist_path=whitelist_path,
        reply=None,
    )
