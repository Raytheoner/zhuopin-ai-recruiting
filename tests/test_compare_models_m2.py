import json
from dataclasses import dataclass, field
from pathlib import Path

from app.llm.gateway import LLMGateway
from scripts.compare_models_m2 import (
    PARSE_PROMPT_VERSION,
    RANK_PROMPT_VERSION,
    JsonlAuditHook,
    ModelCandidate,
    Sample,
    compute_cost,
    evaluate_model,
    load_samples,
    percentile,
)

# ---- 假 OpenAI client（与 tests/test_llm_gateway.py 同构，独立定义以免跨测试文件 import）----


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 40


@dataclass
class _Resp:
    choices: list
    model: str
    usage: _Usage = field(default_factory=_Usage)
    system_fingerprint: str | None = "fp_test_0917"


class _Completions:
    def __init__(self, responses: list[str], model: str):
        self._responses = list(responses)
        self._model = model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(choices=[_Choice(_Msg(self._responses.pop(0)))], model=self._model)


class _Chat:
    def __init__(self, responses, model):
        self.completions = _Completions(responses, model)


class FakeClient:
    def __init__(self, responses: list[str], model: str = "fake-model-2026-01"):
        self.chat = _Chat(responses, model)


TEXT_A = "张明远\n期望工作城市：无锡\n工作年限：5 年\n教育背景\n江南大学｜本科｜车辆工程\n工作经历\n无锡华芯车控科技有限公司（2021年—2026年）｜底层软件工程师\n技能\nC、AUTOSAR CP、CAN\n"
TEXT_B = "李子墨\n教育背景\n苏州大学｜大专｜自动化\n工作经历\n杭州云栈软件有限公司（2024年—2026年）｜Java 开发工程师\n技能\nJava、SQL\n"
TEXT_C = "王思远\n期望工作城市：苏州\n教育背景\n东南大学｜硕士｜电子信息工程\n工作经历\n南京擎川电子有限公司（2019年—2026年）｜嵌入式软件工程师\n技能\nC、MCAL、UDS、Bootloader\n"

RUBRIC = {
    "job_title": "底层软件工程师",
    "criteria": [{"key": "skill_match", "description": "底层技能"}, {"key": "experience_depth", "description": "年限"}],
    "profile_text": "底层软件工程师，要求 C/AUTOSAR。",
}


def _samples():
    return [
        Sample("SA", TEXT_A, {"name": "张明远", "years_of_experience": 5, "skills": ["C", "AUTOSAR CP", "CAN"], "companies": ["无锡华芯车控科技有限公司"], "education": {"degree": "本科", "school": "江南大学"}, "expected_city": "无锡"}, 2),
        Sample("SB", TEXT_B, {"name": "李子墨", "years_of_experience": 2, "skills": ["Java", "SQL"], "companies": ["杭州云栈软件有限公司"], "education": {"degree": "大专", "school": "苏州大学"}, "expected_city": None}, 3),
        Sample("SC", TEXT_C, {"name": "王思远", "years_of_experience": 7, "skills": ["C", "MCAL", "UDS", "Bootloader"], "companies": ["南京擎川电子有限公司"], "education": {"degree": "硕士", "school": "东南大学"}, "expected_city": "苏州"}, 1),
    ]


def _parse_json(name_q, name, years, years_q, skills, skills_q, companies, comp_q, degree, school, edu_q, city, city_q):
    def f(value, quote, sid, not_mentioned=False):
        if not_mentioned:
            return {"not_mentioned": True, "confidence": 1.0, "spans": [], "value": None}
        spans = [{"span_id": sid, "quote": quote}] if quote else []  # quote 为空 ⇒ 模型给了值但没给来源
        return {"not_mentioned": False, "confidence": 0.9, "spans": spans, "value": value}

    return json.dumps(
        {
            "name": f(name, name_q, 1),
            "years_of_experience": f(years, years_q, 3),
            "skills": f(skills, skills_q, 9),
            "companies": f(companies, comp_q, 7),
            "education": f({"degree": degree, "school": school}, edu_q, 5),
            "expected_city": f(city, city_q, 2) if city else f(None, "", 0, not_mentioned=True),
        },
        ensure_ascii=False,
    )


def _rank_json(skill_quote, skill_sid, exp_quote, exp_sid, s1=4.0, s2=3.0):
    return json.dumps(
        {
            "scores": [
                {"key": "skill_match", "score": s1, "rationale": "r", "evidence": {"span_id": skill_sid, "quote": skill_quote}},
                {"key": "experience_depth", "score": s2, "rationale": "r", "evidence": {"span_id": exp_sid, "quote": exp_quote}},
            ]
        },
        ensure_ascii=False,
    )


def _candidate(**overrides):
    base = dict(name="fake", api_key_env="FAKE_KEY_0917", base_url="https://example.invalid/v1", model="fake-model-2026-01", supports_json_schema=False)
    return ModelCandidate(**{**base, **overrides})


def _factory_with(responses: list[str]):
    def factory(candidate, hook):
        return LLMGateway(
            api_key="k",
            base_url=candidate.base_url,
            model=candidate.model,
            supports_json_schema=False,
            max_retries=0,
            audit_hook=hook,
            client=FakeClient(responses, model="fake-model-2026-01"),
        )

    return factory


def test_evaluate_model_end_to_end_with_fake_client(tmp_path):
    # 顺序：每份样本先 parse 再 rank
    responses = [
        _parse_json("张明远", "张明远", 5, "工作年限：5 年", ["C", "AUTOSAR CP", "CAN"], "C、AUTOSAR CP、CAN", ["无锡华芯车控科技有限公司"], "无锡华芯车控科技有限公司", "本科", "江南大学", "江南大学｜本科", "无锡", "无锡"),
        _rank_json("C、AUTOSAR CP、CAN", 9, "2021年—2026年", 7, 4.0, 3.0),
        # SB：年限未在文中出现 ⇒ 模型给了值但无 span ⇒ 置信度归 0；rank 的证据引错分片 ⇒ 整次不可用
        _parse_json("李子墨", "李子墨", 2, "", ["Java", "SQL"], "Java、SQL", ["杭州云栈软件有限公司"], "杭州云栈软件有限公司", "大专", "苏州大学", "苏州大学｜大专", None, ""),
        _rank_json("Java、SQL", 7, "不存在的引文", 3, 1.0, 1.0),
        _parse_json("王思远", "王思远", 7, "", ["C", "MCAL", "UDS", "Bootloader"], "C、MCAL、UDS、Bootloader", ["南京擎川电子有限公司"], "南京擎川电子有限公司", "硕士", "东南大学", "东南大学｜硕士", "苏州", "苏州"),
        _rank_json("C、MCAL、UDS、Bootloader", 8, "2019年—2026年", 6, 5.0, 4.0),
    ]
    report = evaluate_model(_candidate(price_in_per_mtok=2.0, price_out_per_mtok=8.0), _samples(), RUBRIC, gateway_factory=_factory_with(responses), audit_dir=tmp_path, top_k=10)

    assert report.skipped is False
    assert report.n_samples == 3
    assert report.parse_ok == 3
    assert report.rank_ok == 2  # SB 证据不可定位 ⇒ 不计分
    assert report.response_models == ["fake-model-2026-01"]
    assert report.fingerprints == ["fp_test_0917"]
    assert report.field_acc.per_field["name"] == 1.0
    assert report.field_acc.per_field["expected_city"] == 1.0
    assert report.spearman is None  # n=2 < 10 ⇒ 不出结论
    assert report.top_k_recall == 1.0  # 人工前 ⌈3/2⌉=2 = {SC, SA}，系统前 2 = {SC, SA}
    assert 0 < report.span_traceability < 1.0  # SB 的一条证据不可定位
    assert report.prompt_tokens == 600 and report.completion_tokens == 240
    assert report.cost_yuan == round(600 / 1e6 * 2.0 + 240 / 1e6 * 8.0, 4)
    assert report.parse_p50_ms >= 0 and report.rank_p95_ms >= 0

    # 留痕：每次调用一行，含 rubric 快照、prompt 版本、原始响应、temperature、input_hash
    jsonl = list(tmp_path.glob("fake/*.jsonl"))
    assert len(jsonl) == 1
    rows = [json.loads(line) for line in jsonl[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6
    assert {r["prompt_version"] for r in rows} == {PARSE_PROMPT_VERSION, RANK_PROMPT_VERSION}
    assert all(r["temperature"] == 0 and r["input_hash"] and r["raw_response"] for r in rows)
    assert all(r["rubric_snapshot"] == RUBRIC for r in rows)
    assert all(r["response_model"] == "fake-model-2026-01" for r in rows)


def test_raw_provider_error_is_recorded_per_sample_and_run_continues(tmp_path):
    # SA 的第一次调用命中网关对不可切换 4xx 原样抛出的原始异常（app/llm/gateway.py ~394-398）；
    # SB 的 parse/rank 照常返回，验证单份样本报错不拖垮整个候选的对比（review 裁决）。
    responses = [
        _parse_json("李子墨", "李子墨", 2, "", ["Java", "SQL"], "Java、SQL", ["杭州云栈软件有限公司"], "杭州云栈软件有限公司", "大专", "苏州大学", "苏州大学｜大专", None, ""),
        _rank_json("Java、SQL", 7, "2024年—2026年", 5, 1.0, 1.0),
    ]

    class _FlakyCompletions:
        def __init__(self, responses, model):
            self._responses = list(responses)
            self._model = model
            self._raised = False
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if not self._raised:
                self._raised = True
                raise RuntimeError("400 model not found")
            return _Resp(choices=[_Choice(_Msg(self._responses.pop(0)))], model=self._model)

    class _FlakyChat:
        def __init__(self, responses, model):
            self.completions = _FlakyCompletions(responses, model)

    class _FlakyClient:
        def __init__(self, responses, model="fake-model-2026-01"):
            self.chat = _FlakyChat(responses, model)

    def factory(candidate, hook):
        return LLMGateway(
            api_key="k",
            base_url=candidate.base_url,
            model=candidate.model,
            supports_json_schema=False,
            max_retries=0,
            audit_hook=hook,
            client=_FlakyClient(responses),
        )

    report = evaluate_model(_candidate(), _samples()[:2], RUBRIC, gateway_factory=factory, audit_dir=tmp_path)

    assert report.skipped is False
    assert report.n_samples == 2
    assert report.parse_ok == 1
    assert len(report.errors) == 1
    assert "SA" in report.errors[0] and "RuntimeError" in report.errors[0]


def test_schema_failure_counts_as_parse_error_and_skips_rank(tmp_path):
    responses = ["{not json", _rank_json("C", 9, "2021年—2026年", 7)]  # 第二条不会被消费
    report = evaluate_model(_candidate(), _samples()[:1], RUBRIC, gateway_factory=_factory_with(responses), audit_dir=tmp_path)
    assert report.parse_ok == 0
    assert report.rank_ok == 0
    assert report.errors and "SA" in report.errors[0]
    assert report.field_acc.n == 0


def test_missing_api_key_yields_skipped_report(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_KEY_0917", raising=False)
    from scripts.compare_models_m2 import default_gateway_factory

    report = evaluate_model(_candidate(), _samples(), RUBRIC, gateway_factory=default_gateway_factory, audit_dir=tmp_path)
    assert report.skipped is True
    assert "FAKE_KEY_0917" in report.skip_reason


def test_load_samples_rejects_live_class(tmp_path):
    (tmp_path / "truth.json").write_text(json.dumps({"sample_class": "live", "rubric": RUBRIC, "samples": []}), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="live"):
        load_samples(tmp_path)


def test_load_samples_reads_txt_and_rubric(tmp_path):
    (tmp_path / "S01.txt").write_text(TEXT_A, encoding="utf-8")
    truth = {"sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id": "S01", "files": {"txt": "S01.txt"}, "fields": {"name": "张明远"}, "human_rank": 1}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    samples, rubric = load_samples(tmp_path)
    assert samples[0].sample_id == "S01" and samples[0].text == TEXT_A and samples[0].human_rank == 1
    assert rubric == RUBRIC


def test_compute_cost_none_when_price_missing():
    assert compute_cost(_candidate(), 1000, 1000) is None
    assert compute_cost(_candidate(price_in_per_mtok=1.0, price_out_per_mtok=2.0), 1_000_000, 500_000) == 2.0


def test_percentile_nearest_rank():
    assert percentile([], 50) == 0.0
    assert percentile([10.0], 95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def test_jsonl_audit_hook_accumulates_tokens_and_fingerprints(tmp_path):
    hook = JsonlAuditHook(tmp_path / "x" / "a.jsonl", rubric_snapshot=None)
    hook.record(model="m", response_model="m2", system_fingerprint="fp1", prompt_version="v", temperature=0, input_hash="h", raw_response="{}", token_usage={"prompt_tokens": 3, "completion_tokens": 2}, latency_ms=1.0, attempt=1, audit_context=None)
    hook.record(model="m", response_model="m2", system_fingerprint=None, prompt_version="v", temperature=0, input_hash="h", raw_response=None, token_usage={}, latency_ms=1.0, attempt=2, audit_context=None)
    assert hook.calls == 2 and hook.prompt_tokens == 3 and hook.completion_tokens == 2
    assert hook.fingerprints == {"fp1"}
    assert len((tmp_path / "x" / "a.jsonl").read_text(encoding="utf-8").splitlines()) == 2
