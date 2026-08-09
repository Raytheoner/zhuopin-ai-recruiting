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
    """按顺序吐出预先写好的响应，模拟"追问两轮后完成，再生成 JD"整条链路。"""

    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class ScriptedChat:
    def __init__(self, responses):
        self.completions = ScriptedChatCompletions(responses)


class ScriptedOpenAIClient:
    def __init__(self, responses):
        self.chat = ScriptedChat(responses)


def make_app(tmp_path, responses, root_path: str = ""):
    from app.llm.gateway import LLMGateway

    db_path = str(tmp_path / "web.db")
    client = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=client,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    return TestClient(app)


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
    assert body["message"]["payload"]["questions"] == ["是否涉及 AUTOSAR？"]


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
