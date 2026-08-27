import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.schemas.job_profile import field_labels
from app.storage.db import get_connection
from app.web.server import create_app


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


class ScriptedChatCompletions:
    """按顺序吐出预先写好的响应，模拟"追问两轮后完成，再生成 JD"整条链路。

    call_count 用于证明重试安全性（confirm 幂等修复的回归测试）：如果
    POST .../confirm 被重复调用时又真的触发了一次 LLM 调用，call_count 会
    多涨 1，且预先准备好的 responses 队列会被过度消费、pop(0) 在空列表上
    抛 IndexError——两种信号中的任意一个都能戳穿"看起来没重复调用"的假象。
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        # 每次调用的完整 kwargs，供断言"到底发给模型的是什么"的测试使用
        # （多轮历史回归测试要检查 messages 里真的带上了前几轮的原文）。
        self.calls = []

    def create(self, **kwargs):
        self.call_count += 1
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class ScriptedChat:
    def __init__(self, responses):
        self.completions = ScriptedChatCompletions(responses)


class ScriptedOpenAIClient:
    def __init__(self, responses):
        self.chat = ScriptedChat(responses)


def make_app_with_scripted_client(tmp_path, responses, root_path: str = ""):
    """同 make_app()，但额外把底层 ScriptedOpenAIClient 交回给调用方，供需要
    断言 LLM 调用次数（call_count）的测试使用（例如 confirm 幂等重试测试）。"""
    from app.llm.gateway import LLMGateway

    db_path = str(tmp_path / "web.db")
    scripted_client = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=scripted_client,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    return TestClient(app), scripted_client


def make_app(tmp_path, responses, root_path: str = ""):
    client, _ = make_app_with_scripted_client(tmp_path, responses, root_path=root_path)
    return client


def test_create_job_returns_first_question(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["是否涉及 AUTOSAR？"],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["type"] == "question"
    assert [q["text"] for q in body["message"]["payload"]["questions"]] == ["是否涉及 AUTOSAR？"]


def test_reply_and_confirm_then_generate_jd(tmp_path):
    responses = [
        # 第一轮：追问
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["MCU 平台族是？"],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        # 第二轮：完成
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"mcu_family": ["英飞凌 Aurix"]},
            }
        ),
        # confirm 后触发 JD 生成
        json.dumps({"body": "负责嵌入式软件开发与调试"}),
    ]
    client = make_app(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]

    reply_resp = client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})
    assert reply_resp.json()["message"]["type"] == "confirmation_prompt"

    # tasks 6.7 起：这份画像有未指定字段，不带知情标记会被 409 挡住。
    # 本用例验的不是缺口门禁，所以显式带上标记走到它原本要验的那一步。
    confirm_resp = client.post(
        f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True}
    )
    assert confirm_resp.status_code == 200
    jd_text = confirm_resp.json()["jd_text"]
    assert "AI 生成" in jd_text
    assert "负责嵌入式软件开发与调试" in jd_text


def test_confirm_rejected_when_still_drafting(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["MCU 平台族是？"],
                "profile_patch": {},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]

    confirm_resp = client.post(f"/api/jobs/{job_id}/confirm")
    assert confirm_resp.status_code == 409


def test_index_defaults_base_href_to_root_when_no_root_path(tmp_path):
    client = make_app(tmp_path, [], root_path="")

    resp = client.get("/")

    assert resp.status_code == 200
    assert '<base href="/">' in resp.text


def test_index_base_href_matches_configured_root_path(tmp_path):
    client = make_app(tmp_path, [], root_path="/hr/recruit-agent")

    resp = client.get("/hr/recruit-agent/")

    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text


def test_app_works_when_mounted_at_arbitrary_subpath(tmp_path):
    """
    路径前缀就绪的硬验收标准（部署约束1）：把服务挂到任意子路径下
    （这里用 /foo/bar 举例，不是 /hr/recruit-agent 也要正常工作）都不 404。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["是否涉及 AUTOSAR？"],
                "profile_patch": {},
            }
        )
    ]
    client = make_app(tmp_path, responses, root_path="/foo/bar")

    index_resp = client.get("/foo/bar/")
    assert index_resp.status_code == 200

    api_resp = client.post("/foo/bar/api/jobs", json={"message": "要个做嵌入式开发的"})
    assert api_resp.status_code == 200
    assert api_resp.json()["message"]["type"] == "question"


def test_unprefixed_paths_404_when_root_path_is_configured(tmp_path):
    """
    反向证明：设了 /foo/bar 前缀后，不带前缀的路径必须 404——
    否则前缀就只是摆设，没有真的生效。
    """
    client = make_app(tmp_path, [], root_path="/foo/bar")

    assert client.get("/").status_code == 404
    assert client.post("/api/jobs", json={"message": "x"}).status_code == 404


def test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths(tmp_path):
    """
    验证「前端资源与接口调用一律相对路径，禁止硬编码 /static/... /api/...」
    这条约束在实际产出的 HTML 里成立，不是文字承诺。
    """
    client = make_app(tmp_path, [], root_path="/hr/recruit-agent")

    html = client.get("/hr/recruit-agent/").text

    assert '"/api/jobs' not in html
    assert "`/api/jobs" not in html
    assert "fetch(\"api/jobs\")" in html or "url = jobId" in html


def test_confirm_retry_does_not_regenerate_jd(tmp_path):
    """
    回归测试（工程铁律1）：generate_jd() 是一次真实、有成本的 LLM 调用，confirm
    触发它必须像其他有副作用的动作一样独占一个幂等 effect。这里模拟客户端重试
    POST .../confirm（双击、超时后重发、反向代理重试，在浏览器 demo 里都是真实
    会发生的场景）：第二次调用不应该再触发一次 LLM 调用，也不应该用一次新的、
    可能不同的生成结果静默覆盖第一次的 JD 文本。

    responses 里只准备了 1 条 intake 响应 + 1 条 JD 响应，一共 2 条。如果修复
    失效、第二次 confirm 又真的调用了一次 generate_jd()，
    ScriptedChatCompletions._responses 会被过度消费，第二次调用会在空列表上
    pop(0) 抛 IndexError，这个测试会直接因异常失败——call_count 断言是更精确的
    正向证据，异常则是修复失效时的兜底信号。
    """
    responses = [
        # 一轮直接给出完整画像（questions=[]），跳过多轮追问，直接进入可确认状态。
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {
                    "job_title": "嵌入式软件工程师",
                    "mcu_family": ["英飞凌 Aurix"],
                },
            }
        ),
        # confirm 应该只消费这一条——重试不应该再消费第二条（这里也确实没有
        # 准备第二条，修复失效会在这里暴露）。
        json.dumps({"body": "负责嵌入式软件开发与调试"}),
    ]
    client, scripted_client = make_app_with_scripted_client(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]
    assert create_resp.json()["message"]["type"] == "confirmation_prompt"
    assert scripted_client.chat.completions.call_count == 1

    # tasks 6.7 起：这份画像有未指定字段，不带知情标记会被 409 挡住。
    # 本用例验的不是缺口门禁，所以显式带上标记走到它原本要验的那一步。
    first = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert first.status_code == 200
    first_jd = first.json()["jd_text"]
    assert scripted_client.chat.completions.call_count == 2

    # 模拟客户端重试：同一个 job 再 confirm 一次。
    second = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert second.status_code == 200
    assert second.json()["jd_text"] == first_jd
    assert second.json()["needs_manual"] is False
    # 关键断言：第二次 confirm 没有再调用一次真实的 LLM——call_count 保持不变。
    assert scripted_client.chat.completions.call_count == 2


def test_second_turn_prompt_contains_first_turn_message_and_known_fields(tmp_path):
    """
    回归测试（review Critical 发现1）：多轮对话历史必须真的送到模型面前。

    修复前 _run_turn 每轮都把 state["history"] 重建成 [{"role":"user","content":本轮消息}]，
    而 IntakeState.history 是没有 reducer 的普通 TypedDict 字段——LangGraph 把它当
    LastValue 处理，每次 invoke 的输入会静默覆盖上一轮 checkpoint 里的值。结果是
    第二轮起模型只看得到最新一句话，既看不到最初的用人需求，也看不到已经收集到的
    字段，而 SYSTEM_PROMPT 却在要求它"不要重复历史已有字段"——一条它根本无从遵守的
    指令，每轮都是冷启动。

    断言的是"发给模型的 user_prompt 里真的有第一轮原文和已确认字段"，而不是
    round_count 有没有涨——后者在修复前也是对的，正是它掩盖了这个 bug。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["MCU 平台族是？"],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["是否有 ASIL 要求？"],
                "profile_patch": {"mcu_family": ["英飞凌 Aurix"]},
            }
        ),
    ]
    client, scripted_client = make_app_with_scripted_client(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]

    client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})

    second_call = scripted_client.chat.completions.calls[1]
    user_prompt = second_call["messages"][-1]["content"]

    assert "要个做嵌入式开发的" in user_prompt, "第二轮的 prompt 丢失了第一轮的原始需求"
    assert "AUTOSAR CP" in user_prompt, "第二轮的 prompt 应包含本轮新消息"
    assert "MCU 平台族是？" in user_prompt, "第二轮的 prompt 应包含上一轮助手问过的问题"
    assert "嵌入式软件工程师" in user_prompt, (
        "第二轮的 prompt 必须带上已累积的 profile_patch，"
        "否则'不要重复历史已有字段'这条指令模型无从遵守"
    )


def test_history_accumulates_exactly_one_pair_per_turn(tmp_path):
    """
    多轮历史既不能丢（发现1 的正面），也不能一轮记两遍（修复方案的反面风险——
    如果给 IntakeState.history 挂 operator.add 之类的 reducer，又同时在 _run_turn
    里把完整历史整份传进来，每轮就会被累加两次）。

    跑三轮真实 HTTP 请求，断言落库的对话记录正好是 3 组 user/assistant 交替、
    内容和顺序都对得上。

    注：客户端重发同一条 reply 会被当成新的一轮（business_key 来自
    round_count = job_profile 行数，而不是客户端给的幂等键）——这是修复前就存在的
    行为，属于本次明确 park 掉的那条技术债（HTTP 入口缺客户端幂等键，需要先改前端
    契约），不在本轮修复范围内。
    """
    def turn(questions, patch):
        return json.dumps(
            {"is_job_related": True, "questions": questions, "profile_patch": patch}
        )

    responses = [
        turn(["是否涉及 AUTOSAR？"], {"job_title": "嵌入式软件工程师"}),
        turn(["MCU 平台族是？"], {"autosar_experience": ["CP"]}),
        turn(["是否有 ASIL 要求？"], {"mcu_family": ["英飞凌 Aurix"]}),
    ]
    client, _ = make_app_with_scripted_client(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"}).json()["job_id"]
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "要 AUTOSAR CP"})
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "英飞凌 Aurix"})

    from app.storage.db import get_connection

    conn = get_connection(str(tmp_path / "web.db"))
    stored = json.loads(
        conn.execute(
            "SELECT history_json FROM conversation WHERE thread_id=?", (job_id,)
        ).fetchone()[0]
    )

    assert [t["role"] for t in stored] == [
        "user", "assistant", "user", "assistant", "user", "assistant",
    ], "三轮对话应该正好落成 3 组 user/assistant，不多不少"
    assert [t["content"] for t in stored if t["role"] == "user"] == [
        "要个做嵌入式开发的",
        "要 AUTOSAR CP",
        "英飞凌 Aurix",
    ]
    assert stored[1]["content"] == "是否涉及 AUTOSAR？"  # 助手轮记的是当轮问出的问题


def test_confirm_returns_422_when_llm_patch_violates_schema(tmp_path):
    """
    回归测试（review Important 发现2）：profile_patch 是 LLM 自由生成的裸 dict，
    到 confirm 这一步才第一次撞上 JobProfile 的类型约束。真实模型完全可能吐出
    {"headcount": "两个人"} 这种人话形态；修复前 JobProfile.model_validate() 外面
    没有 try/except，ValidationError 会一路冒到 FastAPI 变成未处理的 500——而且
    正好发生在业务经理点"确认"的那一刻，整个 demo 最关键的一步。

    修复后应该返回 422 + 可读的错误说明，而不是 500。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {
                    "job_title": "嵌入式软件工程师",
                    "headcount": "两个人",  # 非数字字符串，撞 headcount: int
                },
            }
        )
    ]
    client = make_app(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]
    assert create_resp.json()["message"]["type"] == "confirmation_prompt"

    # tasks 6.7 起：这份画像有未指定字段，不带知情标记会被 409 挡住。
    # 本用例验的不是缺口门禁，所以显式带上标记走到它原本要验的那一步。
    confirm_resp = client.post(
        f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True}
    )

    assert confirm_resp.status_code == 422, (
        f"畸形 profile_patch 应该被转成 422，实际是 {confirm_resp.status_code}"
    )
    detail = confirm_resp.json()["detail"]
    assert "headcount" in json.dumps(detail, ensure_ascii=False), (
        "422 的 detail 应该指出是哪个字段有问题，便于人工修正"
    )


def test_run_turn_stamps_turn_started_at(tmp_path):
    """
    轮次起始时刻必须在 HTTP 请求进入时打——那才是"用户开始等"的时刻。
    在 compute 节点里打会漏掉排队与取数的时间。
    """
    from app.storage.db import get_connection

    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "招几个人？", "field": "headcount"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = resp.json()["job_id"]

    conn = get_connection(str(tmp_path / "web.db"))
    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, created_at FROM job_profile WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None and row[1] >= 0
    assert row[2] >= row[0]  # 结束不早于开始，说明两者格式一致


def test_question_payload_carries_structured_questions(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [
                    {
                        "text": "要哪个 ASIL 等级？",
                        "field": "functional_safety",
                        "options": ["ASIL-B", "ASIL-D", "无要求"],
                    }
                ],
                "profile_patch": {"job_title": "功能安全工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    body = client.post("/api/jobs", json={"message": "要个做功能安全的"}).json()

    payload = body["message"]["payload"]
    assert payload["questions"][0] == {
        "question_id": "functional_safety",
        "text": "要哪个 ASIL 等级？",
        "field": "functional_safety",
        "options": ["ASIL-B", "ASIL-D", "无要求"],
        "allow_free_text": True,
        "is_reask": False,
    }
    # 3.4 起 questions_text 把档位也渲出来：第 4 章的可点选控件合并之前，
    # 文本是用户唯一看得到档位的地方。标识是《AI 生成合成内容标识办法》要求的。
    assert payload["questions_text"] == (
        "要哪个 ASIL 等级？\n可选（以下为 AI 建议选项，也可自由作答）：ASIL-B / ASIL-D / 无要求"
    )


def test_legacy_string_question_rows_are_normalized_on_read(tmp_path):
    """
    .51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的裸字符串问题。
    GET /api/jobs/{id} 会把这些历史行原样读回来，新前端按对象访问 q.text 会在
    真实数据上直接崩——本地测试库全是新写的行，不专门测就走不到这条路径
    （与 design.md 决策 10 同一类只在服务器上炸的坑）。
    """
    from app.storage.db import get_connection

    responses = [
        json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "要一个人"}).json()["job_id"]

    # 手写一条老形态的 outbox 行，模拟升级前留下的数据
    conn = get_connection(str(tmp_path / "web.db"))
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, 'question', ?)",
        (job_id, json.dumps({"questions": ["是否涉及 AUTOSAR？"]}, ensure_ascii=False)),
    )
    conn.commit()

    payload = client.get(f"/api/jobs/{job_id}").json()["message"]["payload"]

    assert payload["questions"][0]["text"] == "是否涉及 AUTOSAR？"
    assert payload["questions"][0]["question_id"]
    assert payload["questions"][0]["options"] == []
    assert payload["questions_text"] == "是否涉及 AUTOSAR？"


def test_confirmation_prompt_payload_is_untouched(tmp_path):
    """
    第 2 章只换追问的载体。确认提示的 payload 本章不动（缺口警示块属第 6 章），
    这条测试防止"顺手一起改了"。
    """
    responses = [
        json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})
    ]
    client = make_app(tmp_path, responses)

    body = client.post("/api/jobs", json={"message": "要一个人"}).json()

    assert body["message"]["type"] == "confirmation_prompt"
    payload = body["message"]["payload"]
    assert payload["type"] == "confirmation_prompt"
    assert "profile_patch_accumulated" in payload
    assert "unspecified_fields" in payload


def test_asked_questions_ledger_accumulates_across_turns(tmp_path):
    """已问台账是第 5 章重问追踪的载体，先在这里证明它真的按轮累积。"""
    import sqlite3

    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "招几个人？", "field": "headcount"}],
                "profile_patch": {"job_title": "嵌入式工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                "profile_patch": {"headcount": 2},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "招 2 个"})

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    rows = conn.execute(
        "SELECT asked_questions FROM job_profile WHERE job_id=? ORDER BY version ASC", (job_id,)
    ).fetchall()
    conn.close()

    ledger = [item["question_id"] for (raw,) in rows for item in json.loads(raw)]
    assert ledger == ["headcount", "toolchain"]


#  4 个真实字段的池子（够 MAX_TOTAL_ROUNDS=8 用）。⚠️ 一轮最多只能问出
# MAX_QUESTIONS_PER_ROUND（3）个新问题（见 app/agents/intake_agent.py 的
# `capped_questions = ...[:MAX_QUESTIONS_PER_ROUND]`）——4 个字段报到不完
# 一轮，必须分两轮，这也是下面 `_rotation_setup_turns` 要处理的事。
_ROTATING_IDLE_FIELDS = ["toolchain", "functional_safety", "mcu_family", "diag_stack"]


def _rotating_idle_turn(n: int, fields: list[str] = _ROTATING_IDLE_FIELDS) -> str:
    """
    一轮空转，但在几个"已问过、都未答"的字段间轮换重问，而不是死磕同一个
    field。tasks 5.4 的重问上限（MAX_ASKS_PER_QUESTION）与本测试要验证的
    MAX_ROUNDS / MAX_TOTAL_ROUNDS 预算是两条独立的限制——本单元（E）把按轮
    台账真正接线之后，连续死磕同一个未答字段会先撞上重问上限而提前进入确认
    （这正是 `test_dropping_an_exhausted_reask_does_not_make_the_turn_productive`
    钉住的行为），根本走不到轮次预算判定，所以换成轮换测试它本来要测的那条
    （这也是本函数取代了原来那个死磕 `toolchain` 的 `_idle_turn` 的原因）。

    **每轮必须换措辞**，不能逐字重复：`_repeats_earlier_assistant_turn` 会把
    逐字重复直接判成 stuck 并当场收尾，那一轮根本走不到预算判定。换措辞重问
    正是 pilot 里真实发生的形态（采购岗 16949/26262，见
    docs/m1-demo-pilot-feedback.md 的调查第 2 条），也是这一章要处理的那种
    空转。该检测本身的去留归 5.8，本单元不动它。
    """
    field = fields[n % len(fields)]
    return json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": f"{field} 方面还有别的要求吗？（第 {n} 次问）", "field": field}],
            "profile_patch": {"job_title": "嵌入式工程师"},
        }
    )


def _rotation_setup_turn(batch: list[str]) -> str:
    """一次性把 `batch`（长度不超过 MAX_QUESTIONS_PER_ROUND）里的字段全问出来，
    给 `_rotating_idle_turn` 的轮换"报到"用。"""
    return json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": f"{field} 上有什么要求？", "field": field} for field in batch],
            "profile_patch": {"job_title": "嵌入式工程师"},
        }
    )


def _rotation_setup_turns(fields: list[str]) -> list[str]:
    """
    把 `fields` 按 MAX_QUESTIONS_PER_ROUND 切成几批、逐轮报到——一轮塞不下
    就得分几轮，见上面 `_ROTATING_IDLE_FIELDS` 的注释。返回值第一项即
    POST /api/jobs 的首轮响应，其余项是紧跟其后的"报到轮"回复。
    """
    from app.agents.intake_agent import MAX_QUESTIONS_PER_ROUND

    batches = [
        fields[i : i + MAX_QUESTIONS_PER_ROUND] for i in range(0, len(fields), MAX_QUESTIONS_PER_ROUND)
    ]
    return [_rotation_setup_turn(batch) for batch in batches]


def _assert_rotation_survives(round_count: int, fields: list[str]) -> None:
    """
    review Critical：轮换字段数与 MAX_REASKS 之间的耦合曾经是"改代码的人自己
    心算、算错了也不会有任何提示"——`_ROTATING_IDLE_FIELDS` 只有 3 个字段时
    对 `MAX_ROUNDS=5` 恰好零余量够用，换到 `MAX_TOTAL_ROUNDS=8` 就不够：某个
    字段会在跑到 `MAX_TOTAL_ROUNDS` 之前先被重问上限摘掉，测试从"测轮次
    预算"悄悄变成"测重问上限"——两条路径殊途同归都是转入确认流程，光看
    `body["message"]["type"] == "confirmation_prompt"` 这一个断言完全看不出
    区别，这正是本测试组两条用例都曾经踩过的坑。

    字段报到本身还要吃掉 `ceil(len(fields) / MAX_QUESTIONS_PER_ROUND) - 1`
    轮"报到轮"（一轮最多报到 MAX_QUESTIONS_PER_ROUND 个新字段），报到轮不
    参与轮换，因此真正用于轮换、可能撞上重问上限的轮数是
    `round_count - 报到轮数`。N 个字段轮换这些轮次，摊得最挤的那个字段
    恰好问 `ceil(轮换轮数 / N)` 次；这个数只要不超过 MAX_REASKS（重问不含
    首问的剩余额度），就不会撞上重问上限。`ceil(a/b) <= c  <=>  a <= b*c`
    （a、b、c 为正整数），所以判据等价于 `轮换轮数 <= 字段数 * MAX_REASKS`。
    """
    from app.agents.intake_agent import MAX_QUESTIONS_PER_ROUND, MAX_REASKS

    field_count = len(fields)
    setup_rounds = -(-field_count // MAX_QUESTIONS_PER_ROUND)  # ceil
    extra_setup_rounds = setup_rounds - 1  # 首轮本来就占 round_count 的一份额度
    rotation_rounds = round_count - extra_setup_rounds
    max_supported_rotation_rounds = field_count * MAX_REASKS
    assert rotation_rounds >= 0, (
        f"字段报到需要 {setup_rounds} 轮（每轮最多报 {MAX_QUESTIONS_PER_ROUND} "
        f"个新字段，共 {field_count} 个字段），但这里总共只打算跑 {round_count} "
        "轮——报都报不完，轮换根本没机会开始"
    )
    assert rotation_rounds <= max_supported_rotation_rounds, (
        f"{field_count} 个字段报到要吃掉 {extra_setup_rounds} 轮，剩下 "
        f"{rotation_rounds} 轮用于轮换；每个字段除了首问最多还能扛 "
        f"MAX_REASKS={MAX_REASKS} 次重问，{field_count} 个字段轮换合计最多撑 "
        f"{max_supported_rotation_rounds} 轮，撑不住这 {rotation_rounds} 轮——"
        "请给 fields 加几个真实字段名，否则这条测试会在跑到 "
        "MAX_ROUNDS/MAX_TOTAL_ROUNDS 之前先撞上重问上限，从测轮次预算悄悄"
        "变成测重问上限（这正是 review Critical 抓到的那个坑）"
    )


def test_idle_rounds_do_not_consume_the_followup_budget(tmp_path):
    """
    spec「空转轮不计入预算」的端到端证据：连跑 5 轮空转（总轮数已到 MAX_ROUNDS
    以上），对话仍然停在追问状态，业务经理没有因为空转而失去有效追问机会。
    """
    from app.agents.intake_agent import MAX_QUESTIONS_PER_ROUND, MAX_ROUNDS

    # 只用 3 个字段（不用池子里的第 4 个 diag_stack）：MAX_QUESTIONS_PER_ROUND=3
    # 意味着 3 个字段一轮就能报到完，不需要额外的"报到轮"——这条测试要断言
    # `productive == 1`（只有第一轮真的有产出），额外的报到轮会引入第二个
    # "问出新 question_id" 的产出轮，把这条断言带崩。
    fields = _ROTATING_IDLE_FIELDS[:MAX_QUESTIONS_PER_ROUND]
    _assert_rotation_survives(MAX_ROUNDS, fields)

    (first,) = _rotation_setup_turns(fields)
    client = make_app(
        tmp_path, [first] + [_rotating_idle_turn(n, fields) for n in range(MAX_ROUNDS)]
    )

    body = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()
    job_id = body["job_id"]
    for _ in range(MAX_ROUNDS):
        body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "嗯"}).json()

    assert body["message"]["type"] == "question"

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    total, productive = conn.execute(
        "SELECT COUNT(*), SUM(is_productive) FROM job_profile WHERE job_id=?", (job_id,)
    ).fetchone()
    assert total == MAX_ROUNDS + 1
    assert productive == 1  # 只有第一轮真的有产出
    conn.close()


def test_total_round_cap_ends_the_conversation(tmp_path):
    """
    spec「总轮次硬上限兜底」：空转到 MAX_TOTAL_ROUNDS 就进确认流程。

    review Critical：这条测试原来用 `_idle_turn`（死磕同一个未答字段
    `toolchain`）模拟空转，台账真正接线后，`toolchain` 在第 4 轮（1 次首问 +
    2 次重问后已达 `MAX_ASKS_PER_QUESTION`）就被重问上限摘掉、`questions`
    变空、当场转入确认——根本没跑到 `MAX_TOTAL_ROUNDS`（8）。这条测试唯一的
    断言只看终态是不是 `confirmation_prompt`，重问上限摘干净questions 同样会
    让终态变成 `confirmation_prompt`，所以它长期"误判过关"：只要终态巧合
    对上，谁把 `MAX_TOTAL_ROUNDS` 调成任何值这条测试都不会变红——`docstring`
    里"空转到 MAX_TOTAL_ROUNDS 就进确认流程"这句话因此从未被真正验证过。

    改成跟它的姊妹测试（`test_idle_rounds_do_not_consume_the_followup_budget`）
    一样，在几个"已问过、都未答"的字段间轮换重问，撑过重问上限、让空转真正
    靠满 `MAX_TOTAL_ROUNDS` 轮次预算才收尾——这样 `MAX_TOTAL_ROUNDS` 的值才
    真正参与了"什么时候进确认"这件事，而不是被重问上限抢跑。

    这里要用满 `_ROTATING_IDLE_FIELDS` 全部 4 个字段（3 个不够撑 8 轮），
    4 个字段一轮报不完（MAX_QUESTIONS_PER_ROUND=3），所以第 2 轮是"报到轮"
    （补报第 4 个字段 diag_stack），从第 3 轮起才真正轮换。报到轮本身也会
    问出新 question_id，因此这条测试不断言 `is_productive` 的总数——它跟
    `test_idle_rounds_do_not_consume_the_followup_budget` 分工不同：那条测
    "空转不吃 MAX_ROUNDS 预算"（要求 productive 精确等于 1），这条测
    "MAX_TOTAL_ROUNDS 真的兜底"（只要求总轮数吃满 MAX_TOTAL_ROUNDS+1）。
    """
    from app.agents.intake_agent import MAX_TOTAL_ROUNDS

    fields = _ROTATING_IDLE_FIELDS
    _assert_rotation_survives(MAX_TOTAL_ROUNDS, fields)

    first, *extra_setup_turns = _rotation_setup_turns(fields)
    rotation_round_count = MAX_TOTAL_ROUNDS - len(extra_setup_turns)
    rotation_turns = [_rotating_idle_turn(n, fields) for n in range(rotation_round_count)]
    client = make_app(tmp_path, [first, *extra_setup_turns, *rotation_turns])

    body = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()
    job_id = body["job_id"]
    for _ in range(MAX_TOTAL_ROUNDS):
        body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "嗯"}).json()

    assert body["message"]["type"] == "confirmation_prompt"

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    total = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    assert total == MAX_TOTAL_ROUNDS + 1, (
        "轮次总数与 MAX_TOTAL_ROUNDS+1 对不上——如果提前转确认的是重问上限"
        "而不是 MAX_TOTAL_ROUNDS，总轮数会比这个值小"
    )
    conn.close()


def test_confirmation_prompt_payload_carries_chinese_labels(tmp_path):
    """
    tasks 6.5：API 返回未指定字段时同时返回中文名，两个列表同序等长。
    前端只渲染中文名（spec：界面上不出现内部英文字段标识）。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        )
    ]
    client = make_app(tmp_path, responses)

    payload = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["message"]

    assert payload["type"] == "confirmation_prompt"
    fields = payload["payload"]["unspecified_fields"]
    labels = payload["payload"]["unspecified_field_labels"]
    assert fields, "画像只填了 job_title，不该一个缺口都没有"
    assert len(labels) == len(fields)
    assert labels == field_labels(fields)
    assert all(not label.isascii() for label in labels), "中文名里混进了英文标识"


# --- 带缺口确认必须显式知情（tasks 6.7 / 6.8 / 6.9 / 6.10） ------------------


def _make_client_at_confirmation(tmp_path):
    """跑到"可确认"状态，且画像里故意留着缺口（只填了 job_title）。"""
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        ),
        json.dumps({"body": "负责 ECU 底层驱动开发与调试"}),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]
    return client, job_id


def test_confirm_without_acknowledgement_is_rejected_with_409(tmp_path):
    """spec Scenario: 未做选择不放行。"""
    client, job_id = _make_client_at_confirmation(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/confirm")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["gaps"], "409 必须附上未指定字段"
    assert all(gap["label"] and not gap["label"].isascii() for gap in detail["gaps"])
    assert {gap["field"] for gap in detail["gaps"]} >= {"toolchain", "mcu_family"}


def test_confirm_with_explicit_acknowledgement_succeeds_and_is_recorded(tmp_path):
    """spec Scenario: 知情确认被记录 —— 确认完成，且事后可从库里查回。"""
    client, job_id = _make_client_at_confirmation(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})

    assert resp.status_code == 200
    assert resp.json()["jd_text"]

    conn = get_connection(str(tmp_path / "web.db"))
    profile_json = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()[0]
    record = json.loads(profile_json)["_gap_acknowledgement"]

    assert record["acknowledged"] is True
    assert record["had_gaps"] is True
    assert "toolchain" in record["fields"]
    assert record["labels"] == field_labels(record["fields"])
    assert record["at"]


def test_gap_acknowledgement_survives_jd_generation(tmp_path):
    """
    effect_generate_and_persist_jd 会用 {**profile_dict, "_jd_text": ...} 整体覆盖
    profile_json。传给它的如果是确认前那份 dict，知情留痕会在 JD 生成的那一刻被
    静默抹掉——事后查不到、也没有任何报错。这条测试就是盯这个。
    """
    client, job_id = _make_client_at_confirmation(tmp_path)
    client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})

    conn = get_connection(str(tmp_path / "web.db"))
    persisted = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )

    assert persisted["_jd_text"], "JD 没落库，前置条件不成立"
    assert persisted["_gap_acknowledgement"]["acknowledged"] is True


def test_confirm_without_gaps_needs_no_body_and_no_extra_click(tmp_path):
    """
    6.10：无缺口时确认流程与今天完全一致。请求体可以整个省略，不多一步点击。
    """
    full_profile = {
        "job_title": "嵌入式软件工程师",
        "department": "研发部",
        "headcount": 2,
        "education_requirement": "本科及以上",
        "experience_years": "3-5年",
        "core_skills": [{"name": "C", "required": True}],
        "project_experience_requirement": "有量产项目",
        "soft_skill_keywords": ["沟通"],
        "autosar_experience": ["CP"],
        "functional_safety": "ASIL-B",
        "mcu_family": ["TC3xx"],
        "diag_stack": ["UDS"],
        "sop_projects": [
            {"vehicle_model": "X1", "role": "开发", "is_mass_production": True}
        ],
        "toolchain": ["CANoe"],
    }
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": full_profile,
                "unspecified_fields": [],
            }
        ),
        json.dumps({"body": "负责 ECU 底层驱动开发与调试"}),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]

    resp = client.post(f"/api/jobs/{job_id}/confirm")

    assert resp.status_code == 200

    conn = get_connection(str(tmp_path / "web.db"))
    record = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )["_gap_acknowledgement"]
    assert record["had_gaps"] is False
    assert record["fields"] == []


def test_going_back_to_answer_keeps_collected_content(tmp_path):
    """
    spec Scenario: 选择"回去补答" —— 会话回到可继续作答的状态，已采集内容保留。
    "回去补答"在后端就是"不确认、继续 POST /reply"，因此这里验证的是：确认提示
    之后再回一轮，之前采集的字段一个都没丢。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"toolchain": ["CANoe"]},
                "unspecified_fields": [],
            }
        ),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]

    client.post(f"/api/jobs/{job_id}/reply", json={"message": "工具链用 CANoe"})

    conn = get_connection(str(tmp_path / "web.db"))
    accumulated = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )
    assert accumulated["job_title"] == "嵌入式软件工程师"  # 补答没有把已采集内容冲掉
    assert accumulated["toolchain"] == ["CANoe"]


def test_empty_confirm_body_is_still_not_acknowledged(tmp_path):
    """
    变异检查补的守卫：只测"不带 body 时 409"是不够的——那条走的是 req is None
    这一支，`acknowledged_gaps` 的**默认值根本没被求值**。把默认值改成 True
    （合规红线上最危险的那个改动：系统替业务经理声明"我知道有缺口"）时，全部
    既有用例照样绿。

    这里发一个 `{}` 空 body，逼默认值真的参与判定。
    """
    client, job_id = _make_client_at_confirmation(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/confirm", json={})

    assert resp.status_code == 409, "acknowledged_gaps 的默认值不是 false —— 系统替人做了知情声明"
    assert resp.json()["detail"]["gaps"]


def test_reask_is_marked_end_to_end_from_the_persisted_ledger(tmp_path):
    """
    端到端：第 1 轮问功能安全、用户答别的，第 2 轮再问同一个子问题，
    API 响应里那条问题必须带 is_reask=true。

    这条用例走的是真实取数路径（job_profile.asked_questions → _run_turn →
    state → agent → payload），它是"台账真源随画像落库"（tasks 5.1）唯一
    的端到端证明——前面几条都是拿手搓的 state 喂进去的。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "功能安全等级（ASIL）上有什么要求？", "field": "functional_safety"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                "profile_patch": {"headcount": 2},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "招 2 个"}).json()

    (question,) = body["message"]["payload"]["questions"]
    assert question["question_id"] == "functional_safety"
    assert question["is_reask"] is True
    assert "（这个你刚才没答）" in body["message"]["payload"]["questions_text"]


def test_reask_cap_turns_the_field_into_a_gap_end_to_end(tmp_path):
    """
    端到端收口 tasks 5.5：同一个子问题连问 3 轮（问 1 次 + 重问 MAX_REASKS(2) 次）
    仍无回答，第 4 次不再问；这一轮的问题被摘空 → is_complete → 会话进确认，
    而那个字段就出现在确认消息自己携带的缺口清单里。

    为什么必须有这一条：5.5 是本分支的头号主张，也是"台账不新增任何存储"的
    全部理由（Global Constraints 决定 1/2）——「重问超限 → 目标字段计入未指定」
    在此之前只在 agent 层被观测。这里让它穿过真实栈跑一遍：
    job_profile.asked_questions 落库 → _run_turn 读回 → 台账推导 → 摘除 →
    derive_unspecified_fields → effect_deliver_message 的 confirmation_prompt
    payload。中间任何一段接线断掉，这条都会红。

    ⛔ 断言落在**响应自己带的** unspecified_fields 上，不在测试里另调一次
    derive_unspecified_fields —— 后者只会证明单元 D 的纯函数好用，与本次会话
    有没有真的把字段交出去无关（2026-08-27 whole-branch review 的同义反复断言）。

    这里没有、也不该有任何"标记为超限未答"的字段：字段之所以进缺口清单，纯粹
    是因为它**没有值**。合规红线要求摘掉一个子问题不得顺手替业务经理填上默认值，
    下面那条"画像里没有 functional_safety"的断言就是这条红线的观测点。
    """
    ask_functional_safety = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "功能安全等级（ASIL）上有什么要求？", "field": "functional_safety"}],
            "profile_patch": {},
        }
    )
    reask_1 = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
            "profile_patch": {},
        }
    )
    reask_2 = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "功能安全的事还得确认一下，有要求吗？", "field": "functional_safety"}],
            "profile_patch": {},
        }
    )
    # 第 4 轮模型仍然想问同一件事——被系统按上限摘掉，模型想不想问不作数。
    reask_over_cap = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "26262 那边最后确认一下？", "field": "functional_safety"}],
            "profile_patch": {},
        }
    )
    client = make_app(
        tmp_path, [ask_functional_safety, reask_1, reask_2, reask_over_cap]
    )

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    for reply in ("先说别的吧", "这个我还没想好", "回头再说"):
        body = client.post(f"/api/jobs/{job_id}/reply", json={"message": reply}).json()

    message = body["message"]
    assert message["type"] == "confirmation_prompt", (
        "第 4 次仍在追问同一个子问题——重问上限没有在真实栈上生效"
    )
    assert "functional_safety" in message["payload"]["unspecified_fields"], (
        "被摘掉的子问题的目标字段没有进确认消息的缺口清单——"
        "「重问超限转未指定」这条接线断了"
    )

    conn = get_connection(str(tmp_path / "web.db"))
    try:
        accumulated = json.loads(
            conn.execute(
                "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
                (job_id,),
            ).fetchone()[0]
        )
    finally:
        conn.close()
    assert "functional_safety" not in accumulated, (
        "停止追问之后系统替业务经理把字段填上了——违反合规红线"
    )


def test_asked_question_rounds_are_accumulated_oldest_first(tmp_path):
    """
    变异检查补的守卫：`_run_turn` 读 `job_profile.asked_questions` 的既有查询按
    `ORDER BY version ASC` 排序（第 3 轮开工前，最近一次改动是 tasks 5.1 接线）。
    is_reask/超限摘除只看 question_id 有没有出现过、问过几次——这两个判据对轮次
    顺序不敏感（求并集、求和），所以把这个查询悄悄倒成 DESC，is_reask 的用例
    照样绿，问题在别处冒头：同一个循环里 `previous_questions` 取的是"最后一次
    遍历到的那一行"，顺序一倒就变成"最早一轮"，"候选档位不得代替用户做决定"
    （合规红线）的判据会去比对错误的那一轮候选项，导致本该被摘掉的 AI 代答
    悄悄写进画像——不报错、不失败，只是画像里多了一个用户没答应过的值。

    第 3 轮问功能安全（给了候选项），第 4 轮改问别的字段（同样给了候选项），
    第 5 轮用户回"你决定吧"（模糊回复）、模型把第 4 轮的候选项直接抄进
    profile_patch——必须被摘掉，因为 (b)(c) 判据要比对的是**最近一轮**
    （第 4 轮）的候选项，不是第 3 轮的。

    review 指出：上面这条候选档位断言其实只验证了 `previous_questions`（同一
    循环里另一个独立变量，取的是"最后遍历到的那一行"）的顺序，没有验证
    `asked_question_rounds` 本身——如果谁在 `_run_turn` 里查询仍按 ASC 读出、
    但在放进 state 之前对这个列表调一次 `.reverse()`，`previous_questions`
    不受影响（它在循环内部已经赋值完毕），候选档位断言会继续通过，但台账本身
    已经倒了。`asked_question_rounds` 不经过任何 API 响应字段，摘不到它就没法
    在 HTTP 层断言；这里改为直接读 LangGraph 的 checkpoint（`SqliteSaver`，
    `tests/test_transaction_ownership.py` 已有先例），拿到本轮真正喂给
    `compute_intake_turn` 的那份 `asked_question_rounds`，逐轮核对顺序——
    这是比派生的 `first_asked_round` 更直接的观测点：直接顺序错了，
    `first_asked_round` 必然跟着错，反之不然。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [
                    {
                        "text": "要哪个 ASIL 等级？",
                        "field": "functional_safety",
                        "options": ["ASIL-B", "ASIL-D", "无要求"],
                    }
                ],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [
                    {
                        "text": "MCU 家族上有什么要求？",
                        "field": "mcu_family",
                        "options": ["英飞凌 Aurix", "TI Hercules"],
                    }
                ],
                "profile_patch": {},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                # 模型把第 4 轮（MCU 家族）给出的候选项直接抄进 patch——
                # 用户回的是"你决定吧"，没有自己打出"英飞凌 Aurix"。
                "profile_patch": {"mcu_family": "英飞凌 Aurix"},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "先这样吧"})
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "你决定吧"})

    from langgraph.checkpoint.sqlite import SqliteSaver

    # try/finally 把连接关掉：部署目标是 Windows（部署约束 4），那里没关的
    # sqlite 句柄会让 pytest 清理 tmp_path 时删不掉文件、失败在别的用例上。
    # 断言放在 finally 之后，断言失败也不会漏掉 close。
    conn = get_connection(str(tmp_path / "web.db"))
    try:
        accumulated = json.loads(
            conn.execute(
                "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
                (job_id,),
            ).fetchone()[0]
        )
        checkpointer = SqliteSaver(conn)
        checkpoint = checkpointer.get_tuple(
            {"configurable": {"thread_id": job_id}}
        ).checkpoint
        asked_question_rounds = checkpoint["channel_values"]["asked_question_rounds"]
    finally:
        conn.close()

    assert "mcu_family" not in accumulated, (
        "候选档位被写进了画像——说明 previous_questions 比对的不是最近一轮，"
        "很可能是 asked_questions 查询的 ORDER BY 被改动或倒序了"
    )
    assert [round_[0]["question_id"] for round_ in asked_question_rounds] == [
        "functional_safety",
        "mcu_family",
    ], (
        "asked_question_rounds 没有按第 1 轮、第 2 轮的真实顺序累积——"
        "第 5 章台账的 first_asked_round 全靠这个顺序推导，顺序错了不会报错，"
        "只会让重问次数与首问轮次悄悄算错"
    )
