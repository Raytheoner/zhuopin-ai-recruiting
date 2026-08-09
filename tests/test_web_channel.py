from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.storage.db import get_connection, init_schema


def test_deliver_then_latest_returns_same_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    message = OutboundMessage(type="question", payload={"questions": ["MCU 平台族是？"]})
    channel.deliver("job1", message)

    latest = channel.latest("job1")
    assert latest.type == "question"
    assert latest.payload == {"questions": ["MCU 平台族是？"]}


def test_latest_returns_none_when_no_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    assert channel.latest("unknown-job") is None


def test_latest_returns_most_recent_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    channel.deliver("job1", OutboundMessage(type="question", payload={"n": 1}))
    channel.deliver("job1", OutboundMessage(type="question", payload={"n": 2}))

    latest = channel.latest("job1")
    assert latest.payload == {"n": 2}
