"""0917W·私信附件 → 归档 → 回件桥用附件路径（spec `hr-wecom-aibot-liaison` 第 4 章「附件落盘」）。

**本文件守的是「入站帧里的文件真的会被取出落盘、且桥用的是那份文件的路径」**。
此前 `__main__.handle_message_frame` 写死 `attachment=None`，`bridge.py` 的注释直说
`outcome.attachments` 恒为空——业务要专员回的偏偏是**文件**（判例批改表、评测集标注）。

两条纪律与 `frames.py` 的 `FIELD_PATHS` 同款：
- 帧 → 附件句柄的映射是纯函数 `compute_attachment_ref`；表没填（现状，无真实文件帧依据，
  TD-51）⇒ fail-closed：正文照常归档、附件不落盘、只记一行**无取值**的帧键结构。
- 下载与落盘是副作用，只在值守线程侧经 `InboundPorts.download` 这个注入口发生。
  ⛔ 本文件 ⛔ 不 `import aibot`，下载口用替身。

⚠️ `STAND_IN_ATTACHMENT_PATHS` 是**占位键名**，⛔ 不是真实企微帧的形状证据——键名
刻意取成 `stand_in_*`，防止被顺手抄进生产的 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE`。
"""

from __future__ import annotations

import json
import logging
import queue

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import frames
from tools.liaison.tests.test_inbound_wiring import (
    ADMITTED_USERID,
    OUTSIDER_USERID,
    STAND_IN_CHATTYPE,
    T0,
    ReplySpy,
    StoppingEvent,
    make_frame,
    mapped,  # noqa: F401 —— pytest fixture，按名字注入
    ports,  # noqa: F401
    roster,  # noqa: F401
    svc,  # noqa: F401
)

STAND_IN_ATTACHMENT_PATHS = {
    "file": {
        "download_url": ("body", "stand_in_file", "stand_in_url"),
        "aes_key": ("body", "stand_in_file", "stand_in_key"),
        "filename": ("body", "stand_in_file", "stand_in_name"),
    },
}

PAYLOAD = b"PK\x03\x04\x00\x00xlsx-bytes\xff\xfe"


def make_file_frame(*, msgid="MSGID0002", sender=ADMITTED_USERID, filename="判例批改表.xlsx"):
    """一份 `msgtype=file` 的帧：正文键缺席（文件消息本来就没正文），附件句柄用占位键。"""
    frame = make_frame(msgid=msgid, sender=sender, content="")
    frame["body"]["msgtype"] = "file"
    del frame["body"]["stand_in_text"]
    frame["body"]["stand_in_file"] = {
        "stand_in_url": "https://example.invalid/media/abc",
        "stand_in_key": "c2VjcmV0",
        "stand_in_name": filename,
    }
    return frame


@pytest.fixture
def attachment_mapped(monkeypatch):
    monkeypatch.setattr(
        frames, "ATTACHMENT_FIELD_PATHS_BY_MSGTYPE", dict(STAND_IN_ATTACHMENT_PATHS)
    )


class DownloadSpy:
    """替身下载口：签名对齐 SDK `client.download_file` 的返回 `(bytes, filename|None)`。"""

    def __init__(self, result=(PAYLOAD, None), *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[frames.InboundAttachmentRef] = []

    def __call__(self, ref: frames.InboundAttachmentRef):
        self.calls.append(ref)
        if self.error is not None:
            raise self.error
        return self.result


def run_one(svc, ports, frame):
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, frame))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )


def archived_files(archive_root):
    return sorted(p.relative_to(archive_root).as_posix() for p in archive_root.rglob("*") if p.is_file())


def attachments_json_of(conn, msgid):
    row = conn.execute(
        "SELECT attachments_json FROM liaison_message WHERE msgid = ?", (msgid,)
    ).fetchone()
    assert row is not None, "消息行必须已归档"
    return json.loads(row[0])


# ─────────────────────────────────────────────────────────────────────────
# ① 纯映射：compute_attachment_ref
# ─────────────────────────────────────────────────────────────────────────


def test_text_frames_carry_no_attachment_ref():
    assert frames.compute_attachment_ref({"body": {"msgtype": "text"}}, "text") is None


def test_production_attachment_table_is_empty_and_fails_closed_on_a_file_frame():
    """现状：无真实文件帧依据（TD-51），表必须是空的，文件帧必抛，⛔ 不猜 URL。"""
    assert frames.ATTACHMENT_FIELD_PATHS_BY_MSGTYPE == {}
    with pytest.raises(frames.AttachmentFieldsUnverifiedError):
        frames.compute_attachment_ref(make_file_frame(), "file")


def test_mapped_file_frame_yields_a_ref_with_url_key_and_filename(attachment_mapped):
    ref = frames.compute_attachment_ref(make_file_frame(), "file")
    assert ref == frames.InboundAttachmentRef(
        msgtype="file",
        download_url="https://example.invalid/media/abc",
        aes_key="c2VjcmV0",
        filename="判例批改表.xlsx",
    )


def test_mapped_frame_missing_the_url_still_fails_closed(attachment_mapped):
    frame = make_file_frame()
    del frame["body"]["stand_in_file"]["stand_in_url"]
    with pytest.raises(frames.AttachmentFieldsUnverifiedError):
        frames.compute_attachment_ref(frame, "file")


def test_mapped_frame_without_optional_keys_still_yields_a_ref(attachment_mapped):
    frame = make_file_frame()
    del frame["body"]["stand_in_file"]["stand_in_key"]
    del frame["body"]["stand_in_file"]["stand_in_name"]
    ref = frames.compute_attachment_ref(frame, "file")
    assert ref is not None
    assert ref.aes_key is None and ref.filename is None


def test_attachment_error_is_a_frame_unverified_error():
    """让 `handle_message_frame` 现有的 `except SdkSurfaceUnverifiedError` 族能兜住。"""
    assert issubclass(frames.AttachmentFieldsUnverifiedError, frames.InboundFrameUnverifiedError)


# ─────────────────────────────────────────────────────────────────────────
# ② fail-closed 支（生产现状）：正文照常归档、附件不落盘、只记帧键结构
# ─────────────────────────────────────────────────────────────────────────


def test_unmapped_file_frame_archives_the_row_without_a_file_and_logs_the_shape(
    svc, ports, mapped, caplog, tmp_path
):
    download = DownloadSpy()
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    with caplog.at_level(logging.ERROR, logger="tools.liaison"):
        run_one(svc, ports, make_file_frame(sender=OUTSIDER_USERID))

    assert attachments_json_of(svc.conn, "MSGID0002") == []
    assert archived_files(ports.archive_root) == [], "fail-closed 支 ⛔ 不落任何附件文件"
    assert download.calls == [], "表没填就 ⛔ 不许去下载"
    shape_lines = [r.getMessage() for r in caplog.records if "帧结构" in r.getMessage()]
    assert len(shape_lines) == 1
    assert "body.stand_in_file.stand_in_url: str" in shape_lines[0]
    assert "https://example.invalid" not in shape_lines[0], "⛔ 帧键结构里不许出现取值"
    assert "判例批改表" not in shape_lines[0]


# ─────────────────────────────────────────────────────────────────────────
# ③ 接线：映射就位 ⇒ 值守线程下载 → 落盘 → 台账 → 桥的 archived_path 指向附件
# ─────────────────────────────────────────────────────────────────────────


def test_mapped_file_frame_is_downloaded_stored_and_recorded(
    svc, ports, mapped, attachment_mapped
):
    download = DownloadSpy()
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    run_one(svc, ports, make_file_frame())

    assert len(download.calls) == 1
    assert download.calls[0].download_url == "https://example.invalid/media/abc"
    items = attachments_json_of(svc.conn, "MSGID0002")
    assert len(items) == 1
    assert items[0]["filename"] == "MSGID0002__判例批改表.xlsx"
    assert items[0]["byte_length"] == len(PAYLOAD)
    stored = ports.archive_root / items[0]["relative_path"]
    assert stored.read_bytes() == PAYLOAD


def test_filename_falls_back_to_the_download_response_when_the_frame_has_none(
    svc, ports, mapped, attachment_mapped
):
    frame = make_file_frame()
    del frame["body"]["stand_in_file"]["stand_in_name"]
    download = DownloadSpy(result=(PAYLOAD, "来自响应头.pdf"))
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    run_one(svc, ports, frame)
    items = attachments_json_of(svc.conn, "MSGID0002")
    assert items[0]["filename"] == "MSGID0002__来自响应头.pdf"


def test_bridge_signal_archived_path_points_at_the_attachment_file(
    svc, roster, mapped, attachment_mapped, tmp_path, monkeypatch
):
    """opener【三】：带附件的入站 ⇒ 信号项 `archived_path` 指向附件文件，⛔ 不是 `正文.txt`。"""
    from tools.liaison.unpack.dispatch import DispatchOutcome

    monkeypatch.setattr(
        liaison_main.dispatch_wiring,
        "bridge_dispatch",
        lambda conn, **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
        "|---|---|---|---|---|---|\n"
        "| `人事部#1` | 2026-09-09 | 汤丽萍 | 事项 | 无 | `✅ 已推送 2026-09-09` |\n",
        encoding="utf-8",
    )
    ports = liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=ReplySpy(),
        ledger_path=ledger_path,
        download=DownloadSpy(),
    )
    run_one(svc, ports, make_file_frame())

    signal = json.loads(liaison_main.UNPACK_SIGNAL_PATH.read_text(encoding="utf-8"))
    pending = [item for item in signal["pending"] if item["msgid"] == "MSGID0002"]
    assert len(pending) == 1
    archived_path = pending[0]["archived_path"]
    assert archived_path.endswith("/MSGID0002__判例批改表.xlsx"), archived_path
    assert "正文.txt" not in archived_path
    assert "MSGID0002__判例批改表.xlsx" in ledger_path.read_text(encoding="utf-8")
    assert archived_files(ports.archive_root) == ["threadA/20260910/MSGID0002__判例批改表.xlsx"], (
        "有附件时 ⛔ 不再额外落一份 正文.txt"
    )


# ─────────────────────────────────────────────────────────────────────────
# ④ 下载失败／返回空 ⇒ 正文照常归档、附件记告警、⛔ 不写 0 字节文件（TD-41 同类）
# ─────────────────────────────────────────────────────────────────────────


def test_empty_download_writes_no_zero_byte_file_and_alerts(
    svc, ports, mapped, attachment_mapped
):
    download = DownloadSpy(result=(b"", "x.xlsx"))
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    run_one(svc, ports, make_file_frame(sender=OUTSIDER_USERID))

    assert attachments_json_of(svc.conn, "MSGID0002") == []
    assert archived_files(ports.archive_root) == [], "⛔ 不许把空响应写成 0 字节文件"
    assert any("MSGID0002" in t and "附件" in t for t in svc.alert_sink.texts), svc.alert_sink.texts


def test_failing_download_still_archives_the_row_and_alerts(
    svc, ports, mapped, attachment_mapped
):
    download = DownloadSpy(error=RuntimeError("网络挂了"))
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    run_one(svc, ports, make_file_frame())

    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert attachments_json_of(svc.conn, "MSGID0002") == []
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1, (
        "下载失败 ⛔ 不丢待办：人还得知道有份文件没取下来"
    )
    assert any("MSGID0002" in t and "附件" in t for t in svc.alert_sink.texts)


def test_no_download_port_is_loud_not_silent(svc, ports, mapped, attachment_mapped, caplog):
    """`InboundPorts.download` 默认 None（生产 SDK 下载口尚未接，TD-51）：只记 WARNING，
    正文照常归档，⛔ 不吞成"这条消息没附件"。"""
    assert ports.download is None
    with caplog.at_level(logging.WARNING, logger="tools.liaison"):
        run_one(svc, ports, make_file_frame())
    assert attachments_json_of(svc.conn, "MSGID0002") == []
    assert any("下载" in r.getMessage() and "MSGID0002" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────
# ⑤ 幂等：同 msgid 重投不重复下载、不重复落盘
# ─────────────────────────────────────────────────────────────────────────


def test_replaying_the_same_msgid_downloads_and_stores_only_once(
    svc, ports, mapped, attachment_mapped
):
    download = DownloadSpy()
    ports = liaison_main.InboundPorts(
        archive_root=ports.archive_root,
        whitelist_path=ports.whitelist_path,
        reply=ports.reply,
        ledger_path=ports.ledger_path,
        download=download,
    )
    run_one(svc, ports, make_file_frame())
    first = archived_files(ports.archive_root)
    first_mtime = (ports.archive_root / first[0]).stat().st_mtime_ns

    run_one(svc, ports, make_file_frame())

    assert len(download.calls) == 1, "同一 msgid 重投 ⛔ 不许再下载一次"
    assert archived_files(ports.archive_root) == first
    assert (ports.archive_root / first[0]).stat().st_mtime_ns == first_mtime
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert len(attachments_json_of(svc.conn, "MSGID0002")) == 1


def test_replay_with_a_ledger_keeps_the_attachment_path_and_writes_no_snapshot(
    svc, roster, mapped, attachment_mapped, tmp_path, monkeypatch
):
    """重投时 `outcome.attachments` 为空（`ArchiveOutcome` docstring：只在 `newly_archived`
    时权威），桥必须改读台账 `attachments_json` 记的那份材料——⛔ 不许顺手落一份
    `正文.txt`、⛔ 不许把信号项的 `archived_path` 换成正文快照。"""
    from tools.liaison.unpack.dispatch import DispatchOutcome

    monkeypatch.setattr(
        liaison_main.dispatch_wiring,
        "bridge_dispatch",
        lambda conn, **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
        "|---|---|---|---|---|---|\n"
        "| `人事部#1` | 2026-09-09 | 汤丽萍 | 事项 | 无 | `✅ 已推送 2026-09-09` |\n",
        encoding="utf-8",
    )
    download = DownloadSpy()
    ports = liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=ReplySpy(),
        ledger_path=ledger_path,
        download=download,
    )
    run_one(svc, ports, make_file_frame())
    run_one(svc, ports, make_file_frame())

    assert len(download.calls) == 1
    assert archived_files(ports.archive_root) == ["threadA/20260910/MSGID0002__判例批改表.xlsx"]
    signal = json.loads(liaison_main.UNPACK_SIGNAL_PATH.read_text(encoding="utf-8"))
    paths = {item["archived_path"] for item in signal["pending"] if item["msgid"] == "MSGID0002"}
    assert paths == {"data/liaison/archive/threadA/20260910/MSGID0002__判例批改表.xlsx"}
