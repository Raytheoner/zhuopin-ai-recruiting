"""入站**带附件**链路的端到端预演（[Mac]0909AP，⛔ 只加测试，不改产品代码）。

**为什么单开一个文件而不是往 `test_inbound_routing.py` 里塞**：那个文件的关注点是
「名单内／外怎么分支」，带附件只是它顺手捎的一个参数（全文件只有 3 处
`payload=`，且都是片段断言）。本文件的关注点是另一件事——

    私信带附件 → 归档落盘 → 台账写入 → 队列入队

这条**从未被端到端走过**的链路，在 8.6 有 AI 专员真实回灌之前先被走一遍。
背景实证见 `docs/findings/2026-09-09-win端aibot收发实证-对8.6灰度的三条影响.md`：
群里 @ 机器人只收得到**文字平信**，**文档回灌只能走私信**——也就是说 8.6 那天
真实附件必然从这条链路进来，而它此刻只有片段覆盖。

🔴 **本文件不修任何东西。** 最有价值的一条是最后那条 TD-22 防呆用例：它把
「真实 `msgid` 一旦含 `_`，带附件消息会整批失败」这个风险从文字变成可执行的证据，
⛔ 断言的是**当前实现的失败面**而不是期望行为。它变红的正确解读是「TD-22 被还了」，
⛔ 不是「这条用例坏了」——见该用例的 docstring。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tools.liaison.archive import ArchivePathError, InboundAttachment
from tools.liaison.inbound import POLITE_NOTICE, handle_inbound_message
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:30:00+08:00"
EXPECTED_DAY = "20260909"
ADMITTED_USERID = "tanglp"
OUTSIDER_USERID = "someone-else"

#: 一份**二进制**载荷。⛔ 刻意不是可解码的 UTF-8：xlsx/pdf 才是 8.6 当天真实会
#: 收到的东西，而参考服务的生产 bug「二进制判误」正是拿它们去做文本解码。
#: `\xff\xfe` 是任何 UTF-8 解码都会炸的字节对，走通即证明全链路没有偷偷解码。
XLSX_LIKE_PAYLOAD = b"PK\x03\x04\x14\x00\xff\xfe\x00\x08quarterly-report-bytes\x00\x1a"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


@pytest.fixture
def roster(tmp_path):
    """只含 `ADMITTED_USERID` 的名单。

    ⛔ 不用仓库里那份真实的 `config/whitelist.yaml`——名单是会变的业务数据，
    绑上去等于让「改名单」顺手打红本文件。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


class ReplySpy:
    """记账用的 reply port。真实通道在第 7 章，本文件只认这个可调用契约。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, thread_id: str, text: str) -> None:
        self.calls.append((thread_id, text))


def _send_with_attachment(
    conn,
    root,
    roster,
    *,
    sender,
    msgid,
    filename="季度汇总.xlsx",
    payload=XLSX_LIKE_PAYLOAD,
    content="",
    reply=None,
):
    """模拟通道层递进来的一条**带附件私信**。

    ⛔ 不建连、⛔ 不发网络、⛔ 不用真实凭据——`handle_inbound_message` 的入参
    就是通道适配层（第 7 章）解包后交给它的东西，从这里进去即是全链路。
    """
    return handle_inbound_message(
        conn,
        thread_id=sender,  # 私聊：thread_id 取 userid
        msgid=msgid,
        sender_userid=sender,
        received_at=RECEIVED_AT,
        msgtype="file",
        content=content,
        attachment=InboundAttachment(filename=filename, payload=payload),
        archive_root=root,
        whitelist_path=roster,
        reply=reply,
    )


def _archived_files(root: pathlib.Path) -> list[pathlib.Path]:
    """归档树里所有**真实材料**文件。

    ⛔ 排除 `.tmp-` 开头的残留临时文件：把它们算进来会让"落了几份材料"
    这个计数在崩溃残留存在时给出错的答案。
    """
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".tmp-")
    )


def _ledger_attachments(conn, msgid: str) -> list[dict]:
    """台账那一行**权威**记录的附件清单。

    ⚠️ 刻意读库而不是读 `outcome.attachments`：`ArchiveOutcome` 的 docstring 写死了
    幂等命中时 `attachments` 描述的是"这次调用算出的落点"而不是"台账指向什么"。
    要断言"三者恒等"就必须从台账取。
    """
    row = conn.execute(
        "SELECT attachments_json FROM liaison_message WHERE msgid = ?", (msgid,)
    ).fetchone()
    assert row is not None, f"台账里没有 msgid={msgid} 这一行"
    return json.loads(row[0])


def _task_rows(conn):
    return conn.execute(
        "SELECT msgid, thread_id, sender_userid, summary, send_status FROM liaison_task"
    ).fetchall()


def _decode_leaf(leaf: str) -> tuple[str, str]:
    """把叶子组件 `<msgid>__<安全文件名>` 逆推回 `(msgid, 文件名)`。

    这段逆推**逐字照抄** `archive.py::_validated_key` docstring 里的解码式——
    它是「msgid 里一个 `_` 都不许有」那条规则存在的唯一理由。测试这边独立写一遍
    而不是 import 一个共用函数，是为了让"产品代码单方面改了编码"这件事被抓住。
    """
    cut = leaf.index("_")
    return leaf[:cut], leaf[cut + 2 :]


# ─────────────────────────────────────────────────────────────────────────
# 链路一：名单内 + 带附件 ⇒ 落盘 / 台账 / 队列 三者恒等
# ─────────────────────────────────────────────────────────────────────────


def test_admitted_attachment_message_lands_on_disk_in_ledger_and_in_queue(conn, root, roster):
    """🔴 8.6 的主链路：AI 专员私信发一份文档进来。

    一次调用要同时立住三件事，且**互相指得上**：
      1. 材料落盘，路径形态符合 design D4 `<thread_id>/<yyyymmdd>/<msgid>__<文件名>`；
      2. 台账 `liaison_message` 有这一行，`attachments_json` 指向**那个**落点；
      3. 队列 `liaison_task` 生成一条待办。

    ⛔ 不许只断言"三张表各有一行"——那种写法在"落盘的是 A、台账记的是 B"时照样绿。
    """
    reply = ReplySpy()
    result = _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP1", reply=reply
    )

    # ── 1. 落盘：路径形态逐段核对 ──
    files = _archived_files(root)
    assert len(files) == 1, f"应当恰好落一份材料，实际 {files}"
    stored_path = files[0]
    relative = stored_path.relative_to(root)
    assert relative.parts[:2] == (ADMITTED_USERID, EXPECTED_DAY), (
        f"design D4 的路径形态是 <thread_id>/<yyyymmdd>/<叶子>，实际 {relative}"
    )
    assert len(relative.parts) == 3, f"⛔ 不许多层嵌套，实际 {relative}"
    assert relative.parts[2] == "msgAP1__季度汇总.xlsx"
    assert stored_path.read_bytes() == XLSX_LIKE_PAYLOAD, "材料落盘后字节必须原样"

    # ── 2. 台账：指向的正是上面那个落点 ──
    entries = _ledger_attachments(conn, "msgAP1")
    assert len(entries) == 1, "一条消息最多一个附件（aibot 协议），台账应当恰好一项"
    entry = entries[0]
    assert (root / entry["relative_path"]) == stored_path, (
        "🔴 台账记的路径与真实落点不是同一个——「台账指向一份不是它描述的材料」"
    )
    assert entry["byte_length"] == len(XLSX_LIKE_PAYLOAD)
    assert entry["filename"] == "msgAP1__季度汇总.xlsx"

    # ── 3. 队列：一条待办，且回指同一条消息 ──
    assert result.enqueued is True
    assert _task_rows(conn) == [
        ("msgAP1", ADMITTED_USERID, ADMITTED_USERID, "[file]", "pending")
    ], "无正文的文件消息，摘要应当退化成 `[file]` 而不是空串"

    # ── 名单内 ⛔ 不发礼貌回复 ──
    assert result.replied is False
    assert reply.calls == []

    # ── 铁律 1 的机器判据 ──
    assert_effect_log_identity(conn)


def test_binary_payload_survives_the_whole_chain_without_being_decoded(conn, root, roster):
    """载荷里的 `\\xff\\xfe` 任何 UTF-8 解码都会炸——它能原样出来即证明全链路没解码。

    这是生产 bug「二进制判误」的回归面：那次是把合法 xlsx 拿去做文本解码后判为损坏。
    `test_attachments.py` 用 AST 守着"模块里不许出现 `.decode(`"，本条是同一件事的
    **行为面**守卫——扫描器管得住 `attachments.py`，管不住链路上别的环节将来手滑。
    """
    _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP2")
    entry = _ledger_attachments(conn, "msgAP2")[0]
    assert (root / entry["relative_path"]).read_bytes() == XLSX_LIKE_PAYLOAD


# ─────────────────────────────────────────────────────────────────────────
# 链路二：同一 msgid 重投 ⇒ 幂等，材料不重复、待办不翻倍
# ─────────────────────────────────────────────────────────────────────────


def test_replaying_the_same_attachment_message_changes_nothing(conn, root, roster):
    """SDK 重连会重投。重投时三样东西都不许变多。

    ⚠️ 断言的是**计数不变**而不是"第二次返回 False"：外部可观察的后果是
    "会不会多出一份材料／多出一条待办"，那才是 8.6 当天出问题时看得见的东西。
    """
    first = _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP3")
    before = [(path, path.read_bytes()) for path in _archived_files(root)]
    assert first.outcome.newly_archived is True

    second = _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP3")

    assert second.outcome.newly_archived is False, "同一 msgid 重投应当幂等命中"
    assert [(path, path.read_bytes()) for path in _archived_files(root)] == before, (
        "重投把材料写了第二份（或改写了原件）——「归档覆盖」那个生产 bug 的形状"
    )
    assert len(_task_rows(conn)) == 1, "重投让待办翻倍了：UNIQUE 与幂等键两道防线都没拦住"
    assert len(_ledger_attachments(conn, "msgAP3")) == 1
    assert_effect_log_identity(conn)


def test_enqueue_survives_a_crash_between_archive_and_enqueue_with_attachments(
    conn, root, roster
):
    """🔴 铁律 1 的失效方向，在**带附件**这条路上再钉一遍。

    `test_inbound_routing.py` 已有同形状的一条，但走的是纯文本。带附件时归档多了
    一次落盘 I/O，"归档已提交、入队之前进程被杀"这个中间态的窗口更宽——真到 8.6
    出这个问题，丢的是一条**带材料**的待办，材料还在盘上、待办永远不出现，
    且**没有任何症状**。
    """
    from tools.liaison.archive import archive_message

    archive_message(
        conn,
        thread_id=ADMITTED_USERID,
        msgid="msgAP4",
        sender_userid=ADMITTED_USERID,
        received_at=RECEIVED_AT,
        msgtype="file",
        content="",
        attachment=InboundAttachment(filename="季度汇总.xlsx", payload=XLSX_LIKE_PAYLOAD),
        archive_root=root,
    )
    assert _task_rows(conn) == [], "前置没造对：这一步只该归档、不该入队"

    result = _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP4")

    assert result.outcome.newly_archived is False, "前置没造对：这次归档应当是幂等命中"
    assert result.enqueued is True, (
        "归档幂等命中时没有入队——这条带材料的待办已经永久丢失。"
        "⛔ 入队不许用 newly_archived 做门槛"
    )
    assert len(_task_rows(conn)) == 1
    assert len(_archived_files(root)) == 1
    assert_effect_log_identity(conn)


# ─────────────────────────────────────────────────────────────────────────
# 链路三：名单外 + 带附件 ⇒ ⛔ 不入队，只走礼貌回复
# ─────────────────────────────────────────────────────────────────────────


def test_outsider_attachment_is_archived_but_never_enqueued(conn, root, roster):
    """名单外带附件：⛔ 不生成任何队列条目，回一条礼貌说明。

    ⚠️ **「归档」这一项刻意断言"会归档"，与 0909AP opener 正文写的
    「名单外 ⇒ ⛔ 不归档」相反**——按 spec 与 `inbound.py` 模块 docstring 的逐字
    原文，名单外的消息「SHALL 仍然归档（以便事后可查'谁在什么时候发过什么'）」。
    opener 与 spec 冲突时本文件取 spec，理由是保守方向：断言现网真实行为，
    ⛔ 不让一条测试去替 spec 改口径。差异已在
    `docs/findings/2026-09-09-入站带附件链路预演.md` 登记，待 Shao Peishen 裁定。
    """
    reply = ReplySpy()
    result = _send_with_attachment(
        conn, root, roster, sender=OUTSIDER_USERID, msgid="msgAP5", reply=reply
    )

    # 🔴 本条真正的红线：⛔ 不入队
    assert result.route.should_enqueue is False
    assert result.enqueued is False
    assert _task_rows(conn) == [], "⛔ 名单外不得生成任何值守任务队列条目"

    # 礼貌回复发出且只发一次
    assert result.replied is True
    assert reply.calls == [(OUTSIDER_USERID, POLITE_NOTICE)]

    # 归档仍然发生（spec 原文），且材料可取回
    entries = _ledger_attachments(conn, "msgAP5")
    assert len(entries) == 1
    assert (root / entries[0]["relative_path"]).read_bytes() == XLSX_LIKE_PAYLOAD


def test_replaying_an_outsider_attachment_does_not_reply_twice(conn, root, roster):
    """重投时 ⛔ 不再发第二遍礼貌回复——那是骚扰，且队列仍然保持为空。"""
    reply = ReplySpy()
    for _ in range(2):
        _send_with_attachment(
            conn, root, roster, sender=OUTSIDER_USERID, msgid="msgAP6", reply=reply
        )
    assert len(reply.calls) == 1, "礼貌回复跟 newly_archived 走，重投不许再发"
    assert _task_rows(conn) == []
    assert len(_archived_files(root)) == 1


# ─────────────────────────────────────────────────────────────────────────
# 链路四：文件名含中文与空格 ⇒ 路径可逆、材料能原样取回
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_name",
    [
        "季度 汇总 报表.xlsx",  # 中文 + 内部空格
        "2026 年 9 月 简历 汇总 V2.xlsx",  # 中文 + 数字 + 多段空格
        "  前后有空白.pdf  ",  # 首尾空白应被 strip（4.2）
        "候选人 张三（嵌入式）.pdf",  # 中文全角括号
    ],
)
def test_attachment_filename_with_chinese_and_spaces_round_trips(conn, root, roster, raw_name):
    """中文与空格 ⛔ 不许被改写，且叶子能唯一逆推回 `(msgid, 文件名)`。

    8.6 当天 AI 专员发过来的文件名**几乎必然**是中文加空格（"9月 简历 汇总.xlsx"
    这种）。`compute_safe_filename` 的契约是「⛔ 不因为非 ASCII 或"看着像乱码"
    改写或拒收」——本条把那条契约在**整条链路**上验一遍，而不只在纯函数上。
    """
    msgid = "msgAPrt"
    result = _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid=msgid, filename=raw_name
    )
    assert result.enqueued is True

    stored_path = _archived_files(root)[0]
    leaf = stored_path.name
    decoded_msgid, decoded_name = _decode_leaf(leaf)

    assert decoded_msgid == msgid, f"叶子 {leaf!r} 逆推不回原 msgid"
    assert decoded_name == raw_name.strip(), (
        "文件名只允许被 strip 首尾空白；中文、内部空格、全角括号都必须原样保留"
    )
    # 台账指向的路径能把材料原样取回来
    entry = _ledger_attachments(conn, msgid)[0]
    assert (root / entry["relative_path"]).read_bytes() == XLSX_LIKE_PAYLOAD


def test_two_different_filenames_under_one_msgid_never_collide(conn, root, roster):
    """两个不同文件名 ⛔ 不许折成同一条路径。

    这是「归档覆盖」的碰撞面：文件名归一化只要把两个不同的名字磨成同一个字符串，
    第二份材料就会在 `store_attachment` 里撞上 `AttachmentIntegrityError`（好），
    或者更糟——静默覆盖第一份（那正是参考服务的生产 bug）。
    """
    from tools.liaison.archive import compute_archive_path

    left = compute_archive_path(
        thread_id=ADMITTED_USERID,
        msgid="msgAP7",
        received_at=RECEIVED_AT,
        filename="汇总 A.xlsx",
        archive_root=root,
    )
    right = compute_archive_path(
        thread_id=ADMITTED_USERID,
        msgid="msgAP7",
        received_at=RECEIVED_AT,
        filename="汇总 B.xlsx",
        archive_root=root,
    )
    assert left != right


# ─────────────────────────────────────────────────────────────────────────
# 🔴 防呆：TD-22 —— 真实 msgid 含 `_` 时，带附件消息会整批失败
# ─────────────────────────────────────────────────────────────────────────


def test_td22_msgid_with_underscore_makes_every_attachment_message_fail(conn, root, roster):
    """🔴 **TD-22 的失败面，写成可执行证据。⛔ 本条不是要修 TD-22。**

    TD-22 欠的是什么：`archive.py::_validated_key(forbid_underscore=True)` 拒收任何
    含 `_` 的 `msgid`——那是让 `<msgid>__<文件名>` 可逆的必要条件。但**企微 aibot 的
    `msgid` 是不透明字符串，字符集从未被真实流量验证过**，只是"看起来"像 base64url，
    而 base64url 的字母表本身就含 `_`。

    **8.6 当天这条会长什么样**：不是一个显而易见的崩溃，是一段困惑——
    纯文本消息一切正常（见下一条用例），**只有带附件的消息全军覆没**，
    而"文档回灌只能走私信"意味着回灌功能整个不可用。

    ⚠️ **本用例变红的正确解读是「TD-22 被还了」**（路径形态改成了不依赖 `_`
    的分隔符，或把 msgid 单独放一段路径）。那时候要做的是**删掉本条、
    换成新形态的正向用例**，⛔ 不是把 msgid 里的 `_` 清洗掉让它变绿——
    清洗等于把「归档覆盖」的缝隙重新打开（见 `_validated_key` docstring）。
    """
    realistic_msgid = "CAQQ_xh7dGVzdA_1"  # base64url 形状，含两个 `_`

    with pytest.raises(ArchivePathError) as excinfo:
        _send_with_attachment(
            conn, root, roster, sender=ADMITTED_USERID, msgid=realistic_msgid
        )

    assert "_" in str(excinfo.value) and "msgid" in str(excinfo.value)

    # 🔴 后果的完整形状：材料没落盘、台账没写、待办没生成——**整条消息丢失**。
    # 归档在 `effect_archive_message` 之前就抛了，所以连"收到过这条消息"都无从查证。
    assert _archived_files(root) == [], "抛异常后不该有半份材料留在盘上"
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0
    assert _task_rows(conn) == []


def test_td22_the_same_underscore_msgid_still_works_for_plain_text(conn, root, roster):
    """🔴 TD-22 之所以是「困惑」而不是「显而易见」，就在这条。

    同一个含 `_` 的 msgid，**纯文本消息照常归档 + 入队**——因为
    `compute_archive_path` 只在 `attachment is not None` 分支下才被调用。

    8.6 当天的症状因此会是：「机器人明明在正常收消息、待办也在生成，
    偏偏一发文件就没反应」。⛔ 排障时不要从"机器人是不是掉线了"入手，
    先看日志里有没有 `ArchivePathError`。
    """
    realistic_msgid = "CAQQ_xh7dGVzdA_1"

    result = handle_inbound_message(
        conn,
        thread_id=ADMITTED_USERID,
        msgid=realistic_msgid,
        sender_userid=ADMITTED_USERID,
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报表我发你了",
        attachment=None,
        archive_root=root,
        whitelist_path=roster,
    )

    assert result.outcome.newly_archived is True, "纯文本这一支不受 TD-22 影响"
    assert result.enqueued is True
    assert _task_rows(conn) == [
        (realistic_msgid, ADMITTED_USERID, ADMITTED_USERID, "报表我发你了", "pending")
    ]
    assert_effect_log_identity(conn)


# ─────────────────────────────────────────────────────────────────────────
# 8.6 当天的观察点：核对器、长中文名、同 msgid 换文件名
# ─────────────────────────────────────────────────────────────────────────


def test_consistency_checker_is_clean_after_a_normal_attachment_delivery(conn, root, roster):
    """走完一条正常的带附件链路后，台账↔材料核对器 ⛔ 必须一条问题都报不出。

    8.6 当天这是**最快的自检动作**：收完一批回灌之后跑一次核对器，
    空清单 = 台账说有的材料都真的在盘上、字节没变。
    """
    from tools.liaison.consistency import verify_ledger_against_archive

    _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP8")
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_long_chinese_filename_still_lands_and_keeps_its_extension(conn, root, roster):
    """长中文名：截断按**字节**算（中文一个字 3 字节），且扩展名必须保住。

    8.6 会收到什么：AI 专员的文件名是中文长句（"2026年9月嵌入式工程师候选人简历
    汇总…xlsx"）。文件系统的单组件上限 255 **字节**，中文名很容易在字数看着不长时
    就撞线。这条验的是"撞线时不炸、且还能看出是个 xlsx"，⛔ 不验具体截到第几个字。
    """
    long_name = "嵌入式工程师候选人简历汇总" * 12 + ".xlsx"  # 远超 255 字节
    assert len(long_name.encode("utf-8")) > 255, "前置没造对：这个名字没到截断线"

    result = _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid="msgAP9", filename=long_name
    )
    assert result.enqueued is True

    leaf = _archived_files(root)[0].name
    assert len(leaf.encode("utf-8")) <= 255, "叶子组件超了文件系统上限，open() 会报 ENAMETOOLONG"
    assert leaf.endswith(".xlsx"), "扩展名被截掉了——事后没人看得出这是张表"
    assert leaf.startswith("msgAP9__")
    # 台账仍然指得上，材料仍然原样
    entry = _ledger_attachments(conn, "msgAP9")[0]
    assert (root / entry["relative_path"]).read_bytes() == XLSX_LIKE_PAYLOAD


def test_same_msgid_with_a_different_filename_leaves_an_orphan_by_design(conn, root, roster):
    """⚠️ **已知且刻意的中间态，写下来是为了 8.6 当天不把它误判成 bug。**

    同一 `msgid` 第二次投递却带了**不同文件名** ⇒ 落点不同 ⇒ 盘上多出一份材料，
    而台账那一行早在第一次就写死了，仍然指向第一份。于是盘上出现一个
    **台账没有引用的孤儿文件**。

    这**不是**不一致：`consistency.py` 的模块 docstring 逐字写明只核对
    「台账说有的、盘上是不是真有」一个方向，反方向（盘上有、台账没有）是
    design D3 允许的、可由重跑收敛的中间态，报出来只会在崩溃恢复期刷假警报。

    真实企微重投同一 `msgid` 时文件名不会变，所以这条概率极低。⛔ 但如果 8.6
    当天真在归档目录里看到"多出来的一份"，正确处置是**对照台账确认哪份是权威的**，
    ⛔ 不要以为归档坏了。
    """
    from tools.liaison.consistency import verify_ledger_against_archive

    _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid="msgAPa", filename="第一版.xlsx"
    )
    _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid="msgAPa", filename="第二版.xlsx"
    )

    names = sorted(path.name for path in _archived_files(root))
    assert names == ["msgAPa__第一版.xlsx", "msgAPa__第二版.xlsx"], "前置没造对"

    # 台账仍然只认第一份，且核对器保持干净（⛔ 不报孤儿）
    entries = _ledger_attachments(conn, "msgAPa")
    assert len(entries) == 1
    assert entries[0]["filename"] == "msgAPa__第一版.xlsx", "台账那一行 ⛔ 不该被第二次投递改写"
    assert verify_ledger_against_archive(conn, archive_root=root) == []
    assert len(_task_rows(conn)) == 1


def test_same_msgid_same_name_but_different_bytes_is_refused_loudly(conn, root, roster):
    """同一路径上来了**不同字节** ⇒ 响亮失败，⛔ 不静默覆盖、⛔ 不静默跳过。

    这是「归档覆盖」那个生产 bug 的最后一道防线。8.6 当天若看到
    `AttachmentIntegrityError`，含义是"同一条协议消息带来了两份不同的材料"
    ——那是协议层出了怪事，⛔ 不要靠删掉旧文件来"修"。
    """
    from tools.liaison.attachments import AttachmentIntegrityError

    _send_with_attachment(conn, root, roster, sender=ADMITTED_USERID, msgid="msgAPb")
    with pytest.raises(AttachmentIntegrityError):
        _send_with_attachment(
            conn, root, roster, sender=ADMITTED_USERID, msgid="msgAPb", payload=b"different"
        )
    # 原件没被动过
    assert _archived_files(root)[0].read_bytes() == XLSX_LIKE_PAYLOAD


def test_a_zero_byte_attachment_is_archived_and_enqueued_with_no_symptom(conn, root, roster):
    """🔴 **预演中发现的新缺口，本条钉住当前行为、⛔ 不修**（守住条款不许改产品代码）。

    一份 **0 字节**的附件会一路走通：落盘成功、台账写入、待办生成，
    连 `consistency.py` 的核对器都报**空清单**——因为核对器核的是
    「台账说的长度/摘要与盘上一致吗」，0 字节与 `e3b0c442…`（空串的 SHA-256）
    完美自洽。

    **为什么这是缺口**：`b""` 有两个来源，而系统分不开——
      1. 用户真的发了个空文件（罕见但合法，⛔ 不该拒收）；
      2. **第 7 章通道层下载附件失败／拿到空响应**（真实、且 8.6 会发生）。
    第 2 种情况下，Shao Peishen 的队列里会出现一条"收到材料"的待办，
    点开却是个空文件，而**全链路没有任何一处报错或告警**。

    **正确的修法在通道层，不在这里**：aibot 协议下发的媒体项带有声明长度，
    第 7 章下载完应当核对"实际字节数 == 协议声明字节数"，不符即当作下载失败
    重试／告警。⛔ 不要改成"归档层拒收 0 字节"——那会误伤第 1 种情况。
    已在 `docs/findings/2026-09-09-入站带附件链路预演.md` 登记，待转 TD。

    本用例变绿 = 缺口仍在。修好之后它应当被替换成"通道层核对长度"的正向用例。
    """
    from tools.liaison.consistency import verify_ledger_against_archive

    result = _send_with_attachment(
        conn, root, roster, sender=ADMITTED_USERID, msgid="msgAPc", payload=b""
    )

    assert result.outcome.newly_archived is True
    assert result.enqueued is True, "队列里出现了一条待办，材料却是空的"
    entry = _ledger_attachments(conn, "msgAPc")[0]
    assert entry["byte_length"] == 0
    assert entry["sha256"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ), "空串的 SHA-256——台账与磁盘完美自洽，这正是缺口无症状的原因"
    assert verify_ledger_against_archive(conn, archive_root=root) == [], (
        "🔴 核对器报不出问题：8.6 当天 ⛔ 不能靠它发现下载失败"
    )
