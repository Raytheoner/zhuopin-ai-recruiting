"""
tasks 3.5 / 3.6 / 3.7 的验收：一次形状真实的调用穿过
LLMGateway → RecorderAuditHook → AuditRecorder → SqliteSink + JsonlChainSink，
落进真实的 analysis_run 表与真实的哈希链文件。

这里**不 mock 任何一层留痕**，只 mock 供应商 HTTP 客户端（FakeOpenAIClient）——
留痕链路上任何一环写错，这几条会红。
"""

import hashlib
import json

import pytest
from pydantic import BaseModel

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from tests.test_llm_gateway import FakeOpenAIClient


class Verdict(BaseModel):
    ok: bool


# 一段"简历原文"的替身。选一个不可能自然出现在代码或响应里的串，
# 这样"它没出现在留痕里"就是个有意义的断言而不是碰巧。
RESUME_PLAINTEXT = "候选人张三·1990-03·某某大学·ZHENGWEN-MARKER-7f3a"


@pytest.fixture
def wired(tmp_path):
    conn = get_connection(str(tmp_path / "e2e.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    hook = RecorderAuditHook(recorder, conn)
    return conn, chain_path, hook


def _gateway(hook, client):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",
        supports_json_schema=False,
        client=client,
        audit_hook=hook,
    )


def _rows(conn):
    cursor = conn.execute("SELECT * FROM analysis_run")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def test_one_scoring_call_lands_every_reproducibility_field(wired):
    """
    tasks 3.5 / spec「一次评分调用完成」：留痕包含配置侧模型标识、响应返回的模型
    标识、部署指纹、prompt 版本、temperature、输入哈希、rubric 快照、原始响应、
    调用时刻——工程铁律 3 的逐项兑现。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient(
        [json.dumps({"ok": True})],
        response_model="deepseek-chat-241226",
        system_fingerprint="fp_8802",
    )

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分",
        user_prompt=RESUME_PLAINTEXT,
        schema=Verdict,
        prompt_version="score-v1",
        audit_context={
            "thread_id": "job-7",
            "node": "compute_score",
            "application_id": "app-3",
            "job_id": "job-7",
            "rubric_version": "ecu-embedded-v2",
            "rubric_snapshot": {"skill_match": {"weight": 0.4}},
        },
    )

    row = _rows(conn)[0]
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-241226"  # 分两列，不互相覆盖
    assert row["system_fingerprint"] == "fp_8802"
    assert row["prompt_version"] == "score-v1"
    assert row["temperature"] == 0
    assert row["raw_response"] == json.dumps({"ok": True})
    assert row["application_id"] == "app-3"
    assert row["job_id"] == "job-7"
    assert json.loads(row["rubric_snapshot"])["version"] == "ecu-embedded-v2"
    assert row["created_at"]  # 调用时刻，由数据库 datetime('now') 填


def test_input_hash_is_the_sha256_of_the_prompts_not_something_else(wired):
    """
    "输入以哈希形式记录"这句话只有在哈希**真的是那段输入的哈希**时才成立。
    这里独立重算一遍：留痕里的值必须等于 sha256("system\\nuser")。
    顺带把这个拼接格式钉住——它是审计可复现性的一部分，改了要有人知道。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})])

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分", user_prompt=RESUME_PLAINTEXT, schema=Verdict
    )

    expected = hashlib.sha256(
        f"按 rubric 打分\n{RESUME_PLAINTEXT}".encode("utf-8")
    ).hexdigest()
    assert _rows(conn)[0]["input_hash"] == expected


def test_missing_system_fingerprint_records_null_and_the_call_still_succeeds(wired):
    """
    tasks 3.6 / spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    **留痕流程不因字段缺失而失败**。断言"调用照常返回结果"，不是"抛异常"。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})], system_fingerprint=None)

    parsed = _gateway(hook, client).extract_structured(
        system_prompt="sys", user_prompt="user", schema=Verdict
    )

    assert parsed.ok is True
    row = _rows(conn)[0]
    assert row["system_fingerprint"] is None
    assert row["raw_response"] == json.dumps({"ok": True})  # 其余照常


def test_prompt_text_is_never_stored_only_its_hash(wired):
    """
    ⭐ tasks 3.7 / spec 逐字：「系统 MUST NOT 在留痕记录中存储简历原文。**输入内容**
    以哈希形式记录。」

    检查两侧介质：SQLite 真身的全部列，与 JSONL 镜像的全部字节。用一个不可能
    自然出现的标记串，所以"没找到"是个有意义的结论。

    ⚠️ **本条只覆盖输入侧，名字不要写成"留痕里没有任何简历原文"。** `raw_response`
    是**逐字存**的（工程铁律 3 明令要存原始响应），所以一个把简历片段引回来的
    评分模型会让原文进入留痕，而本测试仍然全绿——桩响应是 `{"ok": true}`，
    根本不含标记串。这是 spec 与铁律 3 之间一处未解决的张力，登记为
    docs/tech-debt.md TD-5，⛔ 不要靠改这条测试的名字来掩盖。
    """
    conn, chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})])

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分",
        user_prompt=RESUME_PLAINTEXT,
        schema=Verdict,
        audit_context={"thread_id": "job-7", "node": "compute_score"},
    )

    persisted = json.dumps(_rows(conn), ensure_ascii=False, default=str)
    mirrored = chain_path.read_text(encoding="utf-8")

    assert "ZHENGWEN-MARKER-7f3a" not in persisted
    assert "ZHENGWEN-MARKER-7f3a" not in mirrored
    # 阴性对照：留痕**确实写了东西**，否则上面两条在"什么都没写"时也是绿的
    assert len(_rows(conn)) == 1
    assert mirrored.strip()


@pytest.mark.parametrize(
    "forbidden_key",
    ["speech_rate", "pause_duration", "silence_ratio", "facial_expression", "micro_expression"],
)
def test_red_line_dimensions_cannot_reach_the_trail(forbidden_key):
    """
    tasks 3.7：写入声学情绪维度、写入人脸/表情维度分别被拒。
    强制点在构造期（Task 1），所以连一个非法的 CriterionScore 对象都造不出来——
    留痕链路上根本没有能承载它的形状。
    """
    from app.audit.criteria import ForbiddenCriterionKey
    from app.audit.events import CriterionScore

    with pytest.raises(ForbiddenCriterionKey):
        CriterionScore(criterion_key=forbidden_key, score=0.9, evidence_ref="r-1#1-2")


def test_the_mirror_chain_verifies_after_a_real_call(wired):
    """
    留痕镜像不是"写进去就算数"——U2 的链校验必须在真实调用之后仍然通过。
    这条把 U3 的接线与 U2 的防篡改能力连起来测一次。
    """
    conn, chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True}), json.dumps({"ok": False})])
    gateway = _gateway(hook, client)

    gateway.extract_structured(system_prompt="s1", user_prompt="u1", schema=Verdict)
    gateway.extract_structured(system_prompt="s2", user_prompt="u2", schema=Verdict)

    result = JsonlChainSink(chain_path).verify_chain()
    assert result.ok is True
    assert result.total == 2
    assert len(_rows(conn)) == 2
