import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

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

    confirm_resp = client.post(f"/api/jobs/{job_id}/confirm")
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

    first = client.post(f"/api/jobs/{job_id}/confirm")
    assert first.status_code == 200
    first_jd = first.json()["jd_text"]
    assert scripted_client.chat.completions.call_count == 2

    # 模拟客户端重试：同一个 job 再 confirm 一次。
    second = client.post(f"/api/jobs/{job_id}/confirm")
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

    confirm_resp = client.post(f"/api/jobs/{job_id}/confirm")

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
    assert payload["questions_text"] == "要哪个 ASIL 等级？"


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
